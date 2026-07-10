"""eKnjiga Oružja — web aplikacija (Flask).

Korak 2: ulazna knjiga (Knjiga nabavljenog oružja) — pojedinačni i bulk unos,
pretraga, šifrarnik dobavljača s autocompleteom, upozorenje na duplikat
tvorničkog broja s override-om uz obaveznu napomenu.

Pokretanje:  python3 app.py   →  http://127.0.0.1:5000
"""
from __future__ import annotations

import functools
import re
import secrets
import sqlite3
from pathlib import Path

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db

KATEGORIJE = ("A", "B", "C")

# Vrste isprava za kolonu 8 prodajne knjige (u ispisu se spajaju u jedan string)
ODOBRENJE_VRSTE = {
    "odobrenje_za_nabavu": "odobrenje za nabavu",
    "oruzni_list": "oružni list",
    "odobrenje_za_promet": "odobrenje za promet",
}

# Kolona 9 evidencije streljiva: prodaja civilu na oružni list ili trgovcu
# na odobrenje za promet
ODOBRENJE_VRSTE_STRELJIVO = {
    "oruzni_list": "oružni list",
    "odobrenje_za_promet": "odobrenje za promet",
}


def create_app(db_path: Path | str = db.DB_PATH) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = str(db_path)

    # Tajni ključ sesije: generira se jednom i čuva u bazi (lokalna aplikacija)
    conn = db.init_db(db_path)
    key = db.get_postavka(conn, "app.secret_key")
    if not key:
        key = secrets.token_hex(32)
        db.set_postavka(conn, "app.secret_key", key)
        conn.commit()
    conn.close()
    app.secret_key = key

    app.teardown_appcontext(_close_conn)
    register_routes(app)
    return app


# --------------------------------------------------------------------------
# Pomoćno: konekcija po zahtjevu, prijavljeni korisnik, kontrola pristupa
# --------------------------------------------------------------------------

def get_conn() -> sqlite3.Connection:
    if "conn" not in g:
        from flask import current_app
        g.conn = db.get_conn(current_app.config["DB_PATH"])
    return g.conn


def _close_conn(_exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_conn().execute(
        "SELECT * FROM korisnici WHERE id = ? AND aktivan = 1", (uid,)
    ).fetchone()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login", next=request.path))
        g.user = user
        return view(*args, **kwargs)
    return wrapped


def vlasnik_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["rola"] != "vlasnik":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def parse_tvornicki_brojevi(tekst: str) -> list[str]:
    """Lista tvorničkih brojeva: jedan po retku ili odvojeni zarezima."""
    brojevi = [b.strip() for b in re.split(r"[,;\n]+", tekst)]
    return [b for b in brojevi if b]


def nadji_duplikate(conn, tvornicki_brojevi: list[str]) -> dict[str, list]:
    """Za svaki tvornički broj vrati postojeće NE-stornirane ulaze (ako ih ima)."""
    duplikati: dict[str, list] = {}
    for tb in tvornicki_brojevi:
        rows = conn.execute(
            "SELECT redni_broj, marka_model, datum_nabave, status FROM ulaz "
            "WHERE tvornicki_broj = ? AND status != 'storno' ORDER BY redni_broj",
            (tb,),
        ).fetchall()
        if rows:
            duplikati[tb] = rows
    return duplikati


_PARTNERI = {
    "dobavljac": ("dobavljaci", "dobavljača"),
    "kupac": ("kupci", "kupca"),
}


def dohvati_ili_kreiraj_partnera(conn, form, prefix: str) -> tuple[int | None, str | None]:
    """Vrati (partner_id, greška) za dobavljača ili kupca.

    Odabir iz šifrarnika (skriveno <prefix>_id polje) ili inline unos novog
    (novi_<prefix>_* polja); isti OIB nikad ne stvara duplikat.
    """
    tablica, naziv_jd = _PARTNERI[prefix]
    partner_id = form.get(f"{prefix}_id", "").strip()
    if partner_id:
        row = conn.execute(
            f"SELECT id FROM {tablica} WHERE id = ? AND aktivan = 1", (partner_id,)
        ).fetchone()
        if row is None:
            return None, f"Odabrani {naziv_jd[:-1]} ne postoji."
        return row["id"], None

    naziv = form.get(f"novi_{prefix}_naziv", "").strip()
    if not naziv:
        return None, f"Odaberite {naziv_jd} iz šifrarnika ili unesite novog."
    adresa = form.get(f"novi_{prefix}_adresa", "").strip()
    oib = form.get(f"novi_{prefix}_oib", "").strip() or None
    if oib and not re.fullmatch(r"\d{11}", oib):
        return None, f"OIB {naziv_jd} mora imati točno 11 znamenki."
    if oib:
        postojeci = conn.execute(
            f"SELECT id FROM {tablica} WHERE oib = ?", (oib,)
        ).fetchone()
        if postojeci:
            return postojeci["id"], None  # isti OIB = isti partner
    cur = conn.execute(
        f"INSERT INTO {tablica} (naziv, adresa, oib) VALUES (?, ?, ?)",
        (naziv, adresa, oib),
    )
    db.audit(conn, g.user["id"], g.user["korisnicko_ime"], tablica,
             cur.lastrowid, "unos", None,
             {"naziv": naziv, "adresa": adresa, "oib": oib})
    return cur.lastrowid, None


def veza_na_ulaz(ulaz) -> str:
    """Auto-generirana napomena prodaje s vezom na ulaz (killer feature #1).

    Migrirani zapisi referenciraju papirnatu knjigu; digitalni redni broj
    ulazne eKnjige.
    """
    if ulaz["legacy_knjiga"] or ulaz["legacy_stranica"] or ulaz["legacy_redni_broj"]:
        return (f"ul. knjiga {ulaz['legacy_knjiga'] or '?'}, "
                f"str. {ulaz['legacy_stranica'] or '?'}, "
                f"r.br. {ulaz['legacy_redni_broj'] or '?'}")
    return f"ul. r.br. {ulaz['redni_broj']}"


def _prodaja_dict(row) -> dict:
    """Snimka retka prodaje za audit log (samo podatkovna polja)."""
    polja = ("redni_broj", "datum_prodaje", "kupac_id", "vrsta", "kategorija",
             "marka_model", "kalibar", "tvornicki_broj", "odobrenje_vrsta",
             "odobrenje_broj", "odobrenje_datum", "odobrenje_izdavatelj",
             "napomena", "ulaz_id", "status",
             "legacy_knjiga", "legacy_stranica", "legacy_redni_broj")
    return {p: row[p] for p in polja}


def _streljivo_dict(row) -> dict:
    """Snimka retka streljiva za audit log (samo podatkovna polja)."""
    polja = ("redni_broj", "datum_prodaje", "kupac_id", "vrsta", "marka",
             "kalibar", "lot_broj", "kolicina", "odobrenje_vrsta",
             "odobrenje_broj", "odobrenje_datum", "odobrenje_izdavatelj",
             "oruzje_broj", "napomena", "status",
             "legacy_knjiga", "legacy_stranica", "legacy_redni_broj")
    return {p: row[p] for p in polja}


def parse_lotovi(tekst: str) -> tuple[list[tuple[str, int]], list[str]]:
    """Parsiraj retke „lot;količina” (ili lot,količina / lot količina).

    Vraća (lista (lot, količina), greške).
    """
    lotovi: list[tuple[str, int]] = []
    greske: list[str] = []
    for i, redak in enumerate(tekst.splitlines(), start=1):
        redak = redak.strip()
        if not redak:
            continue
        dijelovi = [d for d in re.split(r"[;,\t]+|\s{2,}|(?<=\S)\s+(?=\d+$)", redak) if d.strip()]
        if len(dijelovi) != 2:
            greske.append(f"Redak {i}: očekivan oblik „broj lota; količina” — dobiveno „{redak}”.")
            continue
        lot, kolicina = dijelovi[0].strip(), dijelovi[1].strip()
        if not kolicina.isdigit() or int(kolicina) <= 0:
            greske.append(f"Redak {i}: količina mora biti pozitivan broj — dobiveno „{kolicina}”.")
            continue
        lotovi.append((lot, int(kolicina)))
    return lotovi, greske


def provjeri_kupca_streljivo(conn, kupac_id: int) -> str | None:
    """Za prodaju streljiva kupac mora imati potpune podatke:
    ime i prezime / naziv, adresu i OIB."""
    k = conn.execute("SELECT * FROM kupci WHERE id = ?", (kupac_id,)).fetchone()
    nedostaje = []
    if not k["adresa"]:
        nedostaje.append("adresa")
    if not k["oib"]:
        nedostaje.append("OIB")
    if nedostaje:
        return (f"Kupcu „{k['naziv']}” nedostaje: {', '.join(nedostaje)}. "
                "Za prodaju streljiva obavezni su ime i prezime, adresa i OIB — "
                "dopunite podatke u šifrarniku kupaca.")
    return None


def _ulaz_dict(row) -> dict:
    """Snimka retka ulaza za audit log (samo podatkovna polja)."""
    polja = ("redni_broj", "datum_nabave", "broj_oruznog_lista", "isprava",
             "dobavljac_id", "vrsta", "kategorija", "marka_model", "kalibar",
             "tvornicki_broj", "napomena", "status",
             "legacy_knjiga", "legacy_stranica", "legacy_redni_broj")
    return {p: row[p] for p in polja}


# --------------------------------------------------------------------------
# Rute
# --------------------------------------------------------------------------

def register_routes(app: Flask) -> None:

    @app.context_processor
    def inject_user():
        return {"user": current_user()}

    # ---------------- Prvo pokretanje i prijava ----------------

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        conn = get_conn()
        if conn.execute("SELECT COUNT(*) AS n FROM korisnici").fetchone()["n"]:
            return redirect(url_for("login"))
        if request.method == "POST":
            ime = request.form.get("ime_prezime", "").strip()
            korisnicko = request.form.get("korisnicko_ime", "").strip()
            lozinka = request.form.get("lozinka", "")
            if not (ime and korisnicko and len(lozinka) >= 8):
                flash("Popunite sva polja; lozinka najmanje 8 znakova.", "error")
            else:
                conn.execute(
                    "INSERT INTO korisnici (korisnicko_ime, lozinka_hash, ime_prezime, rola) "
                    "VALUES (?, ?, ?, 'vlasnik')",
                    (korisnicko, generate_password_hash(lozinka), ime),
                )
                conn.commit()
                flash("Račun vlasnika kreiran. Prijavite se.", "ok")
                return redirect(url_for("login"))
        return render_template("setup.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        conn = get_conn()
        if not conn.execute("SELECT COUNT(*) AS n FROM korisnici").fetchone()["n"]:
            return redirect(url_for("setup"))
        if request.method == "POST":
            row = conn.execute(
                "SELECT * FROM korisnici WHERE korisnicko_ime = ? AND aktivan = 1",
                (request.form.get("korisnicko_ime", "").strip(),),
            ).fetchone()
            if row and check_password_hash(row["lozinka_hash"], request.form.get("lozinka", "")):
                session.clear()
                session["user_id"] = row["id"]
                cilj = request.args.get("next", "")
                return redirect(cilj if cilj.startswith("/") else url_for("ulaz_lista"))
            flash("Pogrešno korisničko ime ili lozinka.", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        return redirect(url_for("ulaz_lista"))

    # ---------------- Ulazna knjiga: pregled i pretraga ----------------

    @app.get("/ulaz")
    @login_required
    def ulaz_lista():
        conn = get_conn()
        q = request.args.get("q", "").strip()
        datum_od = request.args.get("datum_od", "").strip()
        datum_do = request.args.get("datum_do", "").strip()
        status = request.args.get("status", "").strip()

        uvjeti, params = [], []
        if q:
            uvjeti.append(
                "(u.tvornicki_broj LIKE ? OR u.marka_model LIKE ? OR u.vrsta LIKE ? "
                "OR u.kalibar LIKE ? OR d.naziv LIKE ? OR u.isprava LIKE ?)"
            )
            params += [f"%{q}%"] * 6
        if datum_od:
            uvjeti.append("u.datum_nabave >= ?"); params.append(datum_od)
        if datum_do:
            uvjeti.append("u.datum_nabave <= ?"); params.append(datum_do)
        if status:
            uvjeti.append("u.status = ?"); params.append(status)

        sql = (
            "SELECT u.*, d.naziv AS dobavljac_naziv, d.adresa AS dobavljac_adresa, "
            "d.oib AS dobavljac_oib FROM ulaz u JOIN dobavljaci d ON d.id = u.dobavljac_id"
        )
        if uvjeti:
            sql += " WHERE " + " AND ".join(uvjeti)
        sql += " ORDER BY u.redni_broj DESC LIMIT 200"
        zapisi = conn.execute(sql, params).fetchall()
        ukupno = conn.execute("SELECT COUNT(*) AS n FROM ulaz").fetchone()["n"]
        na_stanju = conn.execute(
            "SELECT COUNT(*) AS n FROM ulaz WHERE status = 'na_stanju'"
        ).fetchone()["n"]
        return render_template("ulaz_lista.html", zapisi=zapisi, ukupno=ukupno,
                               na_stanju=na_stanju)

    # ---------------- Pojedinačni unos ----------------

    @app.route("/ulaz/novi", methods=["GET", "POST"])
    @login_required
    def ulaz_novi():
        conn = get_conn()
        if request.method == "POST":
            f = request.form
            greske = []
            datum = f.get("datum_nabave", "").strip()
            isprava = f.get("isprava", "").strip()
            vrsta = f.get("vrsta", "").strip()
            kategorija = f.get("kategorija", "").strip()
            marka_model = f.get("marka_model", "").strip()
            kalibar = f.get("kalibar", "").strip()
            tvornicki = f.get("tvornicki_broj", "").strip()
            napomena = f.get("napomena", "").strip()

            for polje, naziv in [(datum, "Datum nabave"), (isprava, "Isprava"),
                                 (vrsta, "Vrsta"), (marka_model, "Marka i model"),
                                 (kalibar, "Kalibar"), (tvornicki, "Tvornički broj")]:
                if not polje:
                    greske.append(f"{naziv} je obavezno polje.")
            if kategorija not in KATEGORIJE:
                greske.append("Kategorija mora biti A, B ili C.")

            duplikati = nadji_duplikate(conn, [tvornicki]) if tvornicki else {}
            override = f.get("potvrdi_duplikat") == "1"
            if duplikati and not greske:
                if not override:
                    greske.append(
                        "Tvornički broj već postoji u knjizi — potvrdite unos "
                        "duplikata i obavezno upišite napomenu s obrazloženjem."
                    )
                elif not napomena:
                    greske.append("Kod unosa duplikata napomena je obavezna.")

            if greske:
                for gr in greske:
                    flash(gr, "error")
                return render_template("ulaz_forma.html", f=f, duplikati=duplikati,
                                       kategorije=KATEGORIJE)

            conn.execute("BEGIN IMMEDIATE")
            try:
                dobavljac_id, err = dohvati_ili_kreiraj_partnera(conn, f, "dobavljac")
                if err:
                    conn.execute("ROLLBACK")
                    flash(err, "error")
                    return render_template("ulaz_forma.html", f=f, duplikati=duplikati,
                                           kategorije=KATEGORIJE)
                redni_broj = db.sljedeci_redni_broj(conn, "ulaz")
                cur = conn.execute(
                    "INSERT INTO ulaz (redni_broj, datum_nabave, broj_oruznog_lista, isprava, "
                    "dobavljac_id, vrsta, kategorija, marka_model, kalibar, tvornicki_broj, "
                    "napomena, kreirao_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (redni_broj, datum, f.get("broj_oruznog_lista", "").strip() or None,
                     isprava, dobavljac_id, vrsta, kategorija, marka_model, kalibar,
                     tvornicki, napomena, g.user["id"]),
                )
                novi = conn.execute("SELECT * FROM ulaz WHERE id = ?", (cur.lastrowid,)).fetchone()
                db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "ulaz",
                         cur.lastrowid, "unos", None, _ulaz_dict(novi))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            flash(f"Upisan redni broj {redni_broj}.", "ok")
            return redirect(url_for("ulaz_lista"))

        return render_template("ulaz_forma.html", f={}, duplikati={}, kategorije=KATEGORIJE)

    # ---------------- Bulk unos ----------------

    @app.route("/ulaz/bulk", methods=["GET", "POST"])
    @login_required
    def ulaz_bulk():
        conn = get_conn()
        if request.method == "POST":
            f = request.form
            greske = []
            datum = f.get("datum_nabave", "").strip()
            isprava = f.get("isprava", "").strip()
            vrsta = f.get("vrsta", "").strip()
            kategorija = f.get("kategorija", "").strip()
            marka_model = f.get("marka_model", "").strip()
            kalibar = f.get("kalibar", "").strip()
            napomena = f.get("napomena", "").strip()
            brojevi = parse_tvornicki_brojevi(f.get("tvornicki_brojevi", ""))

            for polje, naziv in [(datum, "Datum nabave"), (isprava, "Isprava"),
                                 (vrsta, "Vrsta"), (marka_model, "Marka i model"),
                                 (kalibar, "Kalibar")]:
                if not polje:
                    greske.append(f"{naziv} je obavezno polje.")
            if kategorija not in KATEGORIJE:
                greske.append("Kategorija mora biti A, B ili C.")
            if not brojevi:
                greske.append("Unesite barem jedan tvornički broj.")
            ponovljeni = {b for b in brojevi if brojevi.count(b) > 1}
            if ponovljeni:
                greske.append("Isti tvornički broj naveden više puta u listi: "
                              + ", ".join(sorted(ponovljeni)))

            duplikati = nadji_duplikate(conn, brojevi)
            override = f.get("potvrdi_duplikat") == "1"
            if duplikati and not greske:
                if not override:
                    greske.append(
                        "Neki tvornički brojevi već postoje u knjizi (označeni dolje) — "
                        "potvrdite unos duplikata i obavezno upišite napomenu."
                    )
                elif not napomena:
                    greske.append("Kod unosa duplikata napomena je obavezna.")

            if greske:
                for gr in greske:
                    flash(gr, "error")
                return render_template("ulaz_bulk.html", f=f, duplikati=duplikati,
                                       kategorije=KATEGORIJE)

            conn.execute("BEGIN IMMEDIATE")
            try:
                dobavljac_id, err = dohvati_ili_kreiraj_partnera(conn, f, "dobavljac")
                if err:
                    conn.execute("ROLLBACK")
                    flash(err, "error")
                    return render_template("ulaz_bulk.html", f=f, duplikati=duplikati,
                                           kategorije=KATEGORIJE)
                prvi = db.sljedeci_redni_broj(conn, "ulaz")
                for i, tb in enumerate(brojevi):
                    cur = conn.execute(
                        "INSERT INTO ulaz (redni_broj, datum_nabave, broj_oruznog_lista, isprava, "
                        "dobavljac_id, vrsta, kategorija, marka_model, kalibar, tvornicki_broj, "
                        "napomena, kreirao_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (prvi + i, datum, f.get("broj_oruznog_lista", "").strip() or None,
                         isprava, dobavljac_id, vrsta, kategorija, marka_model, kalibar,
                         tb, napomena, g.user["id"]),
                    )
                    novi = conn.execute("SELECT * FROM ulaz WHERE id = ?", (cur.lastrowid,)).fetchone()
                    db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "ulaz",
                             cur.lastrowid, "unos", None, _ulaz_dict(novi))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            flash(f"Upisano {len(brojevi)} zapisa: redni brojevi {prvi}–{prvi + len(brojevi) - 1}.", "ok")
            return redirect(url_for("ulaz_lista"))

        return render_template("ulaz_bulk.html", f={}, duplikati={}, kategorije=KATEGORIJE)

    # ---------------- Detalj, izmjena (vlasnik), storno (vlasnik) ----------------

    @app.get("/ulaz/<int:ulaz_id>")
    @login_required
    def ulaz_detalj(ulaz_id):
        conn = get_conn()
        zapis = conn.execute(
            "SELECT u.*, d.naziv AS dobavljac_naziv, d.adresa AS dobavljac_adresa, "
            "d.oib AS dobavljac_oib FROM ulaz u JOIN dobavljaci d ON d.id = u.dobavljac_id "
            "WHERE u.id = ?", (ulaz_id,),
        ).fetchone()
        if zapis is None:
            abort(404)
        audit_zapisi = conn.execute(
            "SELECT * FROM audit_log WHERE tablica = 'ulaz' AND zapis_id = ? ORDER BY id",
            (ulaz_id,),
        ).fetchall()
        prodaja = conn.execute(
            "SELECT p.id, p.redni_broj, p.datum_prodaje, k.naziv AS kupac_naziv "
            "FROM prodaja p JOIN kupci k ON k.id = p.kupac_id "
            "WHERE p.ulaz_id = ? AND p.status = 'aktivno'", (ulaz_id,),
        ).fetchone()
        return render_template("ulaz_detalj.html", z=zapis, audit_zapisi=audit_zapisi,
                               prodaja=prodaja)

    @app.route("/ulaz/<int:ulaz_id>/uredi", methods=["GET", "POST"])
    @vlasnik_required
    def ulaz_uredi(ulaz_id):
        conn = get_conn()
        zapis = conn.execute("SELECT * FROM ulaz WHERE id = ?", (ulaz_id,)).fetchone()
        if zapis is None:
            abort(404)
        if zapis["status"] == "storno":
            flash("Stornirani zapis se ne može uređivati.", "error")
            return redirect(url_for("ulaz_detalj", ulaz_id=ulaz_id))
        if request.method == "POST":
            f = request.form
            staro = _ulaz_dict(zapis)
            novo = dict(staro)
            novo.update({
                "datum_nabave": f.get("datum_nabave", "").strip(),
                "broj_oruznog_lista": f.get("broj_oruznog_lista", "").strip() or None,
                "isprava": f.get("isprava", "").strip(),
                "vrsta": f.get("vrsta", "").strip(),
                "kategorija": f.get("kategorija", "").strip(),
                "marka_model": f.get("marka_model", "").strip(),
                "kalibar": f.get("kalibar", "").strip(),
                "tvornicki_broj": f.get("tvornicki_broj", "").strip(),
                "napomena": f.get("napomena", "").strip(),
            })
            obavezna = ["datum_nabave", "isprava", "vrsta", "marka_model",
                        "kalibar", "tvornicki_broj"]
            if any(not novo[p] for p in obavezna) or novo["kategorija"] not in KATEGORIJE:
                flash("Popunite sva obavezna polja (kategorija A/B/C).", "error")
                return render_template("ulaz_forma.html", f=f, duplikati={},
                                       kategorije=KATEGORIJE, uredi=zapis)
            if novo == staro:
                flash("Nema izmjena.", "ok")
                return redirect(url_for("ulaz_detalj", ulaz_id=ulaz_id))
            conn.execute(
                "UPDATE ulaz SET datum_nabave=?, broj_oruznog_lista=?, isprava=?, vrsta=?, "
                "kategorija=?, marka_model=?, kalibar=?, tvornicki_broj=?, napomena=?, "
                "izmijenio_id=?, izmijenjeno=datetime('now','localtime') WHERE id=?",
                (novo["datum_nabave"], novo["broj_oruznog_lista"], novo["isprava"],
                 novo["vrsta"], novo["kategorija"], novo["marka_model"], novo["kalibar"],
                 novo["tvornicki_broj"], novo["napomena"], g.user["id"], ulaz_id),
            )
            db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "ulaz",
                     ulaz_id, "izmjena", staro, novo)
            conn.commit()
            flash("Zapis izmijenjen (staro stanje sačuvano u dnevniku izmjena).", "ok")
            return redirect(url_for("ulaz_detalj", ulaz_id=ulaz_id))
        return render_template("ulaz_forma.html", f=dict(zapis), duplikati={},
                               kategorije=KATEGORIJE, uredi=zapis)

    @app.route("/ulaz/<int:ulaz_id>/storno", methods=["POST"])
    @vlasnik_required
    def ulaz_storno(ulaz_id):
        conn = get_conn()
        zapis = conn.execute("SELECT * FROM ulaz WHERE id = ?", (ulaz_id,)).fetchone()
        if zapis is None:
            abort(404)
        razlog = request.form.get("storno_razlog", "").strip()
        if zapis["status"] == "storno":
            flash("Zapis je već storniran.", "error")
        elif zapis["status"] == "prodano":
            flash("Komad je prodan — prvo stornirajte prodaju.", "error")
        elif not razlog:
            flash("Obrazloženje storna je obavezno.", "error")
        else:
            staro = _ulaz_dict(zapis)
            conn.execute(
                "UPDATE ulaz SET status='storno', storno_razlog=?, storno_korisnik_id=?, "
                "storno_vrijeme=datetime('now','localtime') WHERE id=?",
                (razlog, g.user["id"], ulaz_id),
            )
            novo = dict(staro); novo["status"] = "storno"; novo["storno_razlog"] = razlog
            db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "ulaz",
                     ulaz_id, "storno", staro, novo)
            conn.commit()
            flash(f"Redni broj {zapis['redni_broj']} storniran.", "ok")
        return redirect(url_for("ulaz_detalj", ulaz_id=ulaz_id))

    # ---------------- Prodajna knjiga ----------------

    @app.get("/prodaja")
    @login_required
    def prodaja_lista():
        conn = get_conn()
        q = request.args.get("q", "").strip()
        datum_od = request.args.get("datum_od", "").strip()
        datum_do = request.args.get("datum_do", "").strip()

        uvjeti, params = [], []
        if q:
            uvjeti.append(
                "(p.tvornicki_broj LIKE ? OR p.marka_model LIKE ? OR p.vrsta LIKE ? "
                "OR p.kalibar LIKE ? OR k.naziv LIKE ? OR p.odobrenje_broj LIKE ?)"
            )
            params += [f"%{q}%"] * 6
        if datum_od:
            uvjeti.append("p.datum_prodaje >= ?"); params.append(datum_od)
        if datum_do:
            uvjeti.append("p.datum_prodaje <= ?"); params.append(datum_do)

        sql = (
            "SELECT p.*, k.naziv AS kupac_naziv, k.adresa AS kupac_adresa, "
            "k.oib AS kupac_oib, u.redni_broj AS ulaz_redni_broj "
            "FROM prodaja p JOIN kupci k ON k.id = p.kupac_id "
            "LEFT JOIN ulaz u ON u.id = p.ulaz_id"
        )
        if uvjeti:
            sql += " WHERE " + " AND ".join(uvjeti)
        sql += " ORDER BY p.redni_broj DESC LIMIT 200"
        zapisi = conn.execute(sql, params).fetchall()
        ukupno = conn.execute("SELECT COUNT(*) AS n FROM prodaja").fetchone()["n"]
        return render_template("prodaja_lista.html", zapisi=zapisi, ukupno=ukupno,
                               odobrenje_vrste=ODOBRENJE_VRSTE)

    @app.route("/prodaja/nova", methods=["GET", "POST"])
    @login_required
    def prodaja_nova():
        conn = get_conn()

        if request.method == "GET":
            ulaz = None
            tb = request.args.get("tb", "").strip()
            ulaz_id = request.args.get("ulaz_id", "").strip()
            if ulaz_id:
                ulaz = conn.execute("SELECT * FROM ulaz WHERE id = ?", (ulaz_id,)).fetchone()
                if ulaz is None:
                    abort(404)
            elif tb:
                kandidati = conn.execute(
                    "SELECT * FROM ulaz WHERE tvornicki_broj = ? AND status = 'na_stanju' "
                    "ORDER BY redni_broj", (tb,),
                ).fetchall()
                if len(kandidati) == 1:
                    ulaz = kandidati[0]
                elif len(kandidati) > 1:
                    flash(f"Više komada na stanju s tvorničkim brojem {tb} — odaberite ulaz.", "warn")
                    return render_template("prodaja_nova_izbor.html", kandidati=kandidati, tb=tb)
                else:
                    flash(f"Nema komada na stanju s tvorničkim brojem „{tb}”.", "error")
            if ulaz and ulaz["status"] != "na_stanju":
                flash(f"Komad r.br. {ulaz['redni_broj']} nije na stanju "
                      f"(status: {ulaz['status'].replace('_', ' ')}).", "error")
                ulaz = None
            f = {}
            if ulaz:
                f = {"vrsta": ulaz["vrsta"], "kategorija": ulaz["kategorija"],
                     "marka_model": ulaz["marka_model"], "kalibar": ulaz["kalibar"],
                     "tvornicki_broj": ulaz["tvornicki_broj"]}
            return render_template("prodaja_forma.html", f=f, ulaz=ulaz,
                                   kategorije=KATEGORIJE, odobrenje_vrste=ODOBRENJE_VRSTE)

        # POST
        f = request.form
        greske = []
        ulaz_id = f.get("ulaz_id", "").strip()
        datum = f.get("datum_prodaje", "").strip()
        vrsta = f.get("vrsta", "").strip()
        kategorija = f.get("kategorija", "").strip()
        marka_model = f.get("marka_model", "").strip()
        kalibar = f.get("kalibar", "").strip()
        tvornicki = f.get("tvornicki_broj", "").strip()
        odobrenje_vrsta = f.get("odobrenje_vrsta", "").strip()
        odobrenje_broj = f.get("odobrenje_broj", "").strip()
        odobrenje_datum = f.get("odobrenje_datum", "").strip()
        odobrenje_izdavatelj = f.get("odobrenje_izdavatelj", "").strip()
        napomena_dodatno = f.get("napomena", "").strip()

        ulaz = None
        if not ulaz_id:
            greske.append("Prodaja se kreira iz zapisa ulaza — otvorite je gumbom "
                          "„Prodaj” ili pronađite komad po tvorničkom broju.")
        else:
            ulaz = conn.execute("SELECT * FROM ulaz WHERE id = ?", (ulaz_id,)).fetchone()
            if ulaz is None:
                greske.append("Vezani ulaz ne postoji.")

        for polje, naziv in [(datum, "Datum prodaje"), (vrsta, "Vrsta"),
                             (marka_model, "Marka i model"), (kalibar, "Kalibar"),
                             (tvornicki, "Tvornički broj"),
                             (odobrenje_broj, "Broj odobrenja"),
                             (odobrenje_datum, "Datum odobrenja"),
                             (odobrenje_izdavatelj, "Izdavatelj (PU/PP)")]:
            if not polje:
                greske.append(f"{naziv} je obavezno polje.")
        if kategorija not in KATEGORIJE:
            greske.append("Kategorija mora biti A, B ili C.")
        if odobrenje_vrsta not in ODOBRENJE_VRSTE:
            greske.append("Odaberite vrstu isprave za promet/nabavu.")

        if greske:
            for gr in greske:
                flash(gr, "error")
            return render_template("prodaja_forma.html", f=f, ulaz=ulaz,
                                   kategorije=KATEGORIJE, odobrenje_vrste=ODOBRENJE_VRSTE)

        conn.execute("BEGIN IMMEDIATE")
        try:
            ulaz = conn.execute("SELECT * FROM ulaz WHERE id = ?", (ulaz_id,)).fetchone()
            if ulaz["status"] != "na_stanju":
                conn.execute("ROLLBACK")
                flash(f"Komad r.br. {ulaz['redni_broj']} više nije na stanju "
                      f"(status: {ulaz['status'].replace('_', ' ')}).", "error")
                return render_template("prodaja_forma.html", f=f, ulaz=ulaz,
                                       kategorije=KATEGORIJE, odobrenje_vrste=ODOBRENJE_VRSTE)
            kupac_id, err = dohvati_ili_kreiraj_partnera(conn, f, "kupac")
            if err:
                conn.execute("ROLLBACK")
                flash(err, "error")
                return render_template("prodaja_forma.html", f=f, ulaz=ulaz,
                                       kategorije=KATEGORIJE, odobrenje_vrste=ODOBRENJE_VRSTE)

            napomena = veza_na_ulaz(ulaz)
            if napomena_dodatno:
                napomena += "; " + napomena_dodatno
            redni_broj = db.sljedeci_redni_broj(conn, "prodaja")
            cur = conn.execute(
                "INSERT INTO prodaja (redni_broj, datum_prodaje, kupac_id, vrsta, kategorija, "
                "marka_model, kalibar, tvornicki_broj, odobrenje_vrsta, odobrenje_broj, "
                "odobrenje_datum, odobrenje_izdavatelj, napomena, ulaz_id, kreirao_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (redni_broj, datum, kupac_id, vrsta, kategorija, marka_model, kalibar,
                 tvornicki, odobrenje_vrsta, odobrenje_broj, odobrenje_datum,
                 odobrenje_izdavatelj, napomena, ulaz["id"], g.user["id"]),
            )
            novi = conn.execute("SELECT * FROM prodaja WHERE id = ?", (cur.lastrowid,)).fetchone()
            db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "prodaja",
                     cur.lastrowid, "unos", None, _prodaja_dict(novi))

            staro_ulaz = _ulaz_dict(ulaz)
            conn.execute(
                "UPDATE ulaz SET status = 'prodano', izmijenio_id = ?, "
                "izmijenjeno = datetime('now','localtime') WHERE id = ?",
                (g.user["id"], ulaz["id"]),
            )
            novo_ulaz = dict(staro_ulaz); novo_ulaz["status"] = "prodano"
            db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "ulaz",
                     ulaz["id"], "izmjena", staro_ulaz, novo_ulaz)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        flash(f"Prodaja upisana pod rednim brojem {redni_broj}; "
              f"ulaz r.br. {ulaz['redni_broj']} označen kao prodan.", "ok")
        return redirect(url_for("prodaja_lista"))

    @app.get("/prodaja/<int:prodaja_id>")
    @login_required
    def prodaja_detalj(prodaja_id):
        conn = get_conn()
        zapis = conn.execute(
            "SELECT p.*, k.naziv AS kupac_naziv, k.adresa AS kupac_adresa, "
            "k.oib AS kupac_oib, u.redni_broj AS ulaz_redni_broj, u.id AS ulaz_pk "
            "FROM prodaja p JOIN kupci k ON k.id = p.kupac_id "
            "LEFT JOIN ulaz u ON u.id = p.ulaz_id WHERE p.id = ?", (prodaja_id,),
        ).fetchone()
        if zapis is None:
            abort(404)
        audit_zapisi = conn.execute(
            "SELECT * FROM audit_log WHERE tablica = 'prodaja' AND zapis_id = ? ORDER BY id",
            (prodaja_id,),
        ).fetchall()
        return render_template("prodaja_detalj.html", z=zapis, audit_zapisi=audit_zapisi,
                               odobrenje_vrste=ODOBRENJE_VRSTE)

    @app.route("/prodaja/<int:prodaja_id>/storno", methods=["POST"])
    @vlasnik_required
    def prodaja_storno(prodaja_id):
        conn = get_conn()
        zapis = conn.execute("SELECT * FROM prodaja WHERE id = ?", (prodaja_id,)).fetchone()
        if zapis is None:
            abort(404)
        razlog = request.form.get("storno_razlog", "").strip()
        if zapis["status"] == "storno":
            flash("Prodaja je već stornirana.", "error")
        elif not razlog:
            flash("Obrazloženje storna je obavezno.", "error")
        else:
            conn.execute("BEGIN IMMEDIATE")
            try:
                staro = _prodaja_dict(zapis)
                conn.execute(
                    "UPDATE prodaja SET status='storno', storno_razlog=?, storno_korisnik_id=?, "
                    "storno_vrijeme=datetime('now','localtime') WHERE id=?",
                    (razlog, g.user["id"], prodaja_id),
                )
                novo = dict(staro); novo["status"] = "storno"; novo["storno_razlog"] = razlog
                db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "prodaja",
                         prodaja_id, "storno", staro, novo)
                # storno prodaje vraća komad na stanje
                if zapis["ulaz_id"]:
                    ulaz = conn.execute("SELECT * FROM ulaz WHERE id = ?",
                                        (zapis["ulaz_id"],)).fetchone()
                    if ulaz and ulaz["status"] == "prodano":
                        staro_u = _ulaz_dict(ulaz)
                        conn.execute(
                            "UPDATE ulaz SET status='na_stanju', izmijenio_id=?, "
                            "izmijenjeno=datetime('now','localtime') WHERE id=?",
                            (g.user["id"], ulaz["id"]),
                        )
                        novo_u = dict(staro_u); novo_u["status"] = "na_stanju"
                        db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "ulaz",
                                 ulaz["id"], "izmjena", staro_u, novo_u)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            flash(f"Prodaja r.br. {zapis['redni_broj']} stornirana"
                  + ("; komad vraćen na stanje." if zapis["ulaz_id"] else "."), "ok")
        return redirect(url_for("prodaja_detalj", prodaja_id=prodaja_id))

    @app.route("/prodaja/<int:prodaja_id>/uredi", methods=["GET", "POST"])
    @vlasnik_required
    def prodaja_uredi(prodaja_id):
        conn = get_conn()
        zapis = conn.execute("SELECT * FROM prodaja WHERE id = ?", (prodaja_id,)).fetchone()
        if zapis is None:
            abort(404)
        if zapis["status"] == "storno":
            flash("Stornirani zapis se ne može uređivati.", "error")
            return redirect(url_for("prodaja_detalj", prodaja_id=prodaja_id))
        if request.method == "POST":
            f = request.form
            staro = _prodaja_dict(zapis)
            novo = dict(staro)
            novo.update({
                "datum_prodaje": f.get("datum_prodaje", "").strip(),
                "vrsta": f.get("vrsta", "").strip(),
                "kategorija": f.get("kategorija", "").strip(),
                "marka_model": f.get("marka_model", "").strip(),
                "kalibar": f.get("kalibar", "").strip(),
                "tvornicki_broj": f.get("tvornicki_broj", "").strip(),
                "odobrenje_vrsta": f.get("odobrenje_vrsta", "").strip(),
                "odobrenje_broj": f.get("odobrenje_broj", "").strip(),
                "odobrenje_datum": f.get("odobrenje_datum", "").strip(),
                "odobrenje_izdavatelj": f.get("odobrenje_izdavatelj", "").strip(),
                "napomena": f.get("napomena", "").strip(),
            })
            obavezna = ["datum_prodaje", "vrsta", "marka_model", "kalibar",
                        "tvornicki_broj", "odobrenje_broj", "odobrenje_datum",
                        "odobrenje_izdavatelj"]
            if (any(not novo[p] for p in obavezna)
                    or novo["kategorija"] not in KATEGORIJE
                    or novo["odobrenje_vrsta"] not in ODOBRENJE_VRSTE):
                flash("Popunite sva obavezna polja.", "error")
                return render_template("prodaja_forma.html", f=f, ulaz=None, uredi=zapis,
                                       kategorije=KATEGORIJE, odobrenje_vrste=ODOBRENJE_VRSTE)
            if novo == staro:
                flash("Nema izmjena.", "ok")
                return redirect(url_for("prodaja_detalj", prodaja_id=prodaja_id))
            conn.execute(
                "UPDATE prodaja SET datum_prodaje=?, vrsta=?, kategorija=?, marka_model=?, "
                "kalibar=?, tvornicki_broj=?, odobrenje_vrsta=?, odobrenje_broj=?, "
                "odobrenje_datum=?, odobrenje_izdavatelj=?, napomena=?, izmijenio_id=?, "
                "izmijenjeno=datetime('now','localtime') WHERE id=?",
                (novo["datum_prodaje"], novo["vrsta"], novo["kategorija"],
                 novo["marka_model"], novo["kalibar"], novo["tvornicki_broj"],
                 novo["odobrenje_vrsta"], novo["odobrenje_broj"], novo["odobrenje_datum"],
                 novo["odobrenje_izdavatelj"], novo["napomena"], g.user["id"], prodaja_id),
            )
            db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "prodaja",
                     prodaja_id, "izmjena", staro, novo)
            conn.commit()
            flash("Zapis izmijenjen (staro stanje sačuvano u dnevniku izmjena).", "ok")
            return redirect(url_for("prodaja_detalj", prodaja_id=prodaja_id))
        return render_template("prodaja_forma.html", f=dict(zapis), ulaz=None, uredi=zapis,
                               kategorije=KATEGORIJE, odobrenje_vrste=ODOBRENJE_VRSTE)

    # ---------------- Evidencija streljiva ----------------

    def _validiraj_streljivo(f) -> list[str]:
        """Zajednička validacija polja streljiva (bez lota/količine)."""
        greske = []
        for kljuc, naziv in [("datum_prodaje", "Datum prodaje"), ("vrsta", "Vrsta"),
                             ("marka", "Marka (proizvođač)"), ("kalibar", "Kalibar"),
                             ("odobrenje_broj", "Broj isprave"),
                             ("odobrenje_izdavatelj", "Izdavatelj (PU/PP)")]:
            if not f.get(kljuc, "").strip():
                greske.append(f"{naziv} je obavezno polje.")
        if f.get("odobrenje_vrsta", "").strip() not in ODOBRENJE_VRSTE_STRELJIVO:
            greske.append("Odaberite vrstu isprave (oružni list / odobrenje za promet).")
        if (f.get("odobrenje_vrsta") == "oruzni_list"
                and not f.get("oruzje_broj", "").strip()):
            greske.append("Kod prodaje na oružni list obavezan je tvornički broj "
                          "oružja upisanog u oružni list.")
        return greske

    def _streljivo_insert(conn, f, redni_broj, kupac_id, lot, kolicina):
        cur = conn.execute(
            "INSERT INTO streljivo (redni_broj, datum_prodaje, kupac_id, vrsta, marka, "
            "kalibar, lot_broj, kolicina, odobrenje_vrsta, odobrenje_broj, odobrenje_datum, "
            "odobrenje_izdavatelj, oruzje_broj, napomena, kreirao_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (redni_broj, f.get("datum_prodaje", "").strip(), kupac_id,
             f.get("vrsta", "").strip(), f.get("marka", "").strip(),
             f.get("kalibar", "").strip(), lot, kolicina,
             f.get("odobrenje_vrsta", "").strip(), f.get("odobrenje_broj", "").strip(),
             f.get("odobrenje_datum", "").strip() or None,
             f.get("odobrenje_izdavatelj", "").strip(),
             f.get("oruzje_broj", "").strip() or None,
             f.get("napomena", "").strip(), g.user["id"]),
        )
        novi = conn.execute("SELECT * FROM streljivo WHERE id = ?", (cur.lastrowid,)).fetchone()
        db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "streljivo",
                 cur.lastrowid, "unos", None, _streljivo_dict(novi))

    @app.get("/streljivo")
    @login_required
    def streljivo_lista():
        conn = get_conn()
        q = request.args.get("q", "").strip()
        datum_od = request.args.get("datum_od", "").strip()
        datum_do = request.args.get("datum_do", "").strip()

        uvjeti, params = [], []
        if q:
            uvjeti.append(
                "(s.marka LIKE ? OR s.vrsta LIKE ? OR s.kalibar LIKE ? OR s.lot_broj LIKE ? "
                "OR k.naziv LIKE ? OR s.odobrenje_broj LIKE ? OR s.oruzje_broj LIKE ?)"
            )
            params += [f"%{q}%"] * 7
        if datum_od:
            uvjeti.append("s.datum_prodaje >= ?"); params.append(datum_od)
        if datum_do:
            uvjeti.append("s.datum_prodaje <= ?"); params.append(datum_do)

        sql = (
            "SELECT s.*, k.naziv AS kupac_naziv, k.adresa AS kupac_adresa, "
            "k.oib AS kupac_oib FROM streljivo s JOIN kupci k ON k.id = s.kupac_id"
        )
        if uvjeti:
            sql += " WHERE " + " AND ".join(uvjeti)
        sql += " ORDER BY s.redni_broj DESC LIMIT 200"
        zapisi = conn.execute(sql, params).fetchall()
        ukupno = conn.execute("SELECT COUNT(*) AS n FROM streljivo").fetchone()["n"]
        return render_template("streljivo_lista.html", zapisi=zapisi, ukupno=ukupno,
                               odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)

    @app.route("/streljivo/novo", methods=["GET", "POST"])
    @login_required
    def streljivo_novo():
        conn = get_conn()
        if request.method == "POST":
            f = request.form
            greske = _validiraj_streljivo(f)
            lot = f.get("lot_broj", "").strip()
            kolicina = f.get("kolicina", "").strip()
            if not lot:
                greske.append("Broj lota / broj pakiranja je obavezno polje.")
            if not kolicina.isdigit() or int(kolicina) <= 0:
                greske.append("Količina mora biti pozitivan broj.")
            if greske:
                for gr in greske:
                    flash(gr, "error")
                return render_template("streljivo_forma.html", f=f,
                                       odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)

            conn.execute("BEGIN IMMEDIATE")
            try:
                kupac_id, err = dohvati_ili_kreiraj_partnera(conn, f, "kupac")
                if not err:
                    err = provjeri_kupca_streljivo(conn, kupac_id)
                if err:
                    conn.execute("ROLLBACK")
                    flash(err, "error")
                    return render_template("streljivo_forma.html", f=f,
                                           odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)
                redni_broj = db.sljedeci_redni_broj(conn, "streljivo")
                _streljivo_insert(conn, f, redni_broj, kupac_id, lot, int(kolicina))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            flash(f"Upisan redni broj {redni_broj}.", "ok")
            return redirect(url_for("streljivo_lista"))

        return render_template("streljivo_forma.html", f={},
                               odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)

    @app.route("/streljivo/bulk", methods=["GET", "POST"])
    @login_required
    def streljivo_bulk():
        conn = get_conn()
        if request.method == "POST":
            f = request.form
            greske = _validiraj_streljivo(f)
            lotovi, lot_greske = parse_lotovi(f.get("lotovi", ""))
            greske += lot_greske
            if not lotovi and not lot_greske:
                greske.append("Unesite barem jedan redak „broj lota; količina”.")
            if greske:
                for gr in greske:
                    flash(gr, "error")
                return render_template("streljivo_bulk.html", f=f,
                                       odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)

            conn.execute("BEGIN IMMEDIATE")
            try:
                kupac_id, err = dohvati_ili_kreiraj_partnera(conn, f, "kupac")
                if not err:
                    err = provjeri_kupca_streljivo(conn, kupac_id)
                if err:
                    conn.execute("ROLLBACK")
                    flash(err, "error")
                    return render_template("streljivo_bulk.html", f=f,
                                           odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)
                prvi = db.sljedeci_redni_broj(conn, "streljivo")
                for i, (lot, kolicina) in enumerate(lotovi):
                    _streljivo_insert(conn, f, prvi + i, kupac_id, lot, kolicina)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            flash(f"Upisano {len(lotovi)} zapisa: redni brojevi "
                  f"{prvi}–{prvi + len(lotovi) - 1}.", "ok")
            return redirect(url_for("streljivo_lista"))

        return render_template("streljivo_bulk.html", f={},
                               odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)

    @app.get("/streljivo/<int:streljivo_id>")
    @login_required
    def streljivo_detalj(streljivo_id):
        conn = get_conn()
        zapis = conn.execute(
            "SELECT s.*, k.naziv AS kupac_naziv, k.adresa AS kupac_adresa, "
            "k.oib AS kupac_oib FROM streljivo s JOIN kupci k ON k.id = s.kupac_id "
            "WHERE s.id = ?", (streljivo_id,),
        ).fetchone()
        if zapis is None:
            abort(404)
        audit_zapisi = conn.execute(
            "SELECT * FROM audit_log WHERE tablica = 'streljivo' AND zapis_id = ? ORDER BY id",
            (streljivo_id,),
        ).fetchall()
        return render_template("streljivo_detalj.html", z=zapis, audit_zapisi=audit_zapisi,
                               odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)

    @app.route("/streljivo/<int:streljivo_id>/uredi", methods=["GET", "POST"])
    @vlasnik_required
    def streljivo_uredi(streljivo_id):
        conn = get_conn()
        zapis = conn.execute("SELECT * FROM streljivo WHERE id = ?", (streljivo_id,)).fetchone()
        if zapis is None:
            abort(404)
        if zapis["status"] == "storno":
            flash("Stornirani zapis se ne može uređivati.", "error")
            return redirect(url_for("streljivo_detalj", streljivo_id=streljivo_id))
        if request.method == "POST":
            f = request.form
            greske = _validiraj_streljivo(f)
            lot = f.get("lot_broj", "").strip()
            kolicina = f.get("kolicina", "").strip()
            if not lot:
                greske.append("Broj lota / broj pakiranja je obavezno polje.")
            if not kolicina.isdigit() or int(kolicina) <= 0:
                greske.append("Količina mora biti pozitivan broj.")
            if greske:
                for gr in greske:
                    flash(gr, "error")
                return render_template("streljivo_forma.html", f=f, uredi=zapis,
                                       odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)
            staro = _streljivo_dict(zapis)
            novo = dict(staro)
            novo.update({
                "datum_prodaje": f.get("datum_prodaje", "").strip(),
                "vrsta": f.get("vrsta", "").strip(),
                "marka": f.get("marka", "").strip(),
                "kalibar": f.get("kalibar", "").strip(),
                "lot_broj": lot,
                "kolicina": int(kolicina),
                "odobrenje_vrsta": f.get("odobrenje_vrsta", "").strip(),
                "odobrenje_broj": f.get("odobrenje_broj", "").strip(),
                "odobrenje_datum": f.get("odobrenje_datum", "").strip() or None,
                "odobrenje_izdavatelj": f.get("odobrenje_izdavatelj", "").strip(),
                "oruzje_broj": f.get("oruzje_broj", "").strip() or None,
                "napomena": f.get("napomena", "").strip(),
            })
            if novo == staro:
                flash("Nema izmjena.", "ok")
                return redirect(url_for("streljivo_detalj", streljivo_id=streljivo_id))
            conn.execute(
                "UPDATE streljivo SET datum_prodaje=?, vrsta=?, marka=?, kalibar=?, "
                "lot_broj=?, kolicina=?, odobrenje_vrsta=?, odobrenje_broj=?, "
                "odobrenje_datum=?, odobrenje_izdavatelj=?, oruzje_broj=?, napomena=?, "
                "izmijenio_id=?, izmijenjeno=datetime('now','localtime') WHERE id=?",
                (novo["datum_prodaje"], novo["vrsta"], novo["marka"], novo["kalibar"],
                 novo["lot_broj"], novo["kolicina"], novo["odobrenje_vrsta"],
                 novo["odobrenje_broj"], novo["odobrenje_datum"],
                 novo["odobrenje_izdavatelj"], novo["oruzje_broj"], novo["napomena"],
                 g.user["id"], streljivo_id),
            )
            db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "streljivo",
                     streljivo_id, "izmjena", staro, novo)
            conn.commit()
            flash("Zapis izmijenjen (staro stanje sačuvano u dnevniku izmjena).", "ok")
            return redirect(url_for("streljivo_detalj", streljivo_id=streljivo_id))
        return render_template("streljivo_forma.html", f=dict(zapis), uredi=zapis,
                               odobrenje_vrste=ODOBRENJE_VRSTE_STRELJIVO)

    @app.route("/streljivo/<int:streljivo_id>/storno", methods=["POST"])
    @vlasnik_required
    def streljivo_storno(streljivo_id):
        conn = get_conn()
        zapis = conn.execute("SELECT * FROM streljivo WHERE id = ?", (streljivo_id,)).fetchone()
        if zapis is None:
            abort(404)
        razlog = request.form.get("storno_razlog", "").strip()
        if zapis["status"] == "storno":
            flash("Zapis je već storniran.", "error")
        elif not razlog:
            flash("Obrazloženje storna je obavezno.", "error")
        else:
            staro = _streljivo_dict(zapis)
            conn.execute(
                "UPDATE streljivo SET status='storno', storno_razlog=?, storno_korisnik_id=?, "
                "storno_vrijeme=datetime('now','localtime') WHERE id=?",
                (razlog, g.user["id"], streljivo_id),
            )
            novo = dict(staro); novo["status"] = "storno"; novo["storno_razlog"] = razlog
            db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "streljivo",
                     streljivo_id, "storno", staro, novo)
            conn.commit()
            flash(f"Redni broj {zapis['redni_broj']} storniran.", "ok")
        return redirect(url_for("streljivo_detalj", streljivo_id=streljivo_id))

    # ---------------- Šifrarnici (dobavljači/kupci) + autocomplete ----------------

    def _api_partneri(tablica):
        q = request.args.get("q", "").strip()
        rows = get_conn().execute(
            f"SELECT id, naziv, adresa, oib FROM {tablica} "
            "WHERE aktivan = 1 AND (naziv LIKE ? OR oib LIKE ?) ORDER BY naziv LIMIT 10",
            (f"%{q}%", f"{q}%"),
        ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/api/dobavljaci")
    @login_required
    def api_dobavljaci():
        return _api_partneri("dobavljaci")

    @app.get("/api/kupci")
    @login_required
    def api_kupci():
        return _api_partneri("kupci")

    def _sifrarnik(tablica, naslov, naziv_jd, endpoint, broj_sql):
        conn = get_conn()
        if request.method == "POST":
            naziv = request.form.get("naziv", "").strip()
            adresa = request.form.get("adresa", "").strip()
            oib = request.form.get("oib", "").strip() or None
            if not naziv:
                flash("Naziv je obavezan.", "error")
            elif oib and not re.fullmatch(r"\d{11}", oib):
                flash("OIB mora imati točno 11 znamenki.", "error")
            elif oib and conn.execute(f"SELECT 1 FROM {tablica} WHERE oib = ?", (oib,)).fetchone():
                flash(f"{naziv_jd.capitalize()} s tim OIB-om već postoji.", "error")
            else:
                cur = conn.execute(
                    f"INSERT INTO {tablica} (naziv, adresa, oib) VALUES (?, ?, ?)",
                    (naziv, adresa, oib),
                )
                db.audit(conn, g.user["id"], g.user["korisnicko_ime"], tablica,
                         cur.lastrowid, "unos", None,
                         {"naziv": naziv, "adresa": adresa, "oib": oib})
                conn.commit()
                flash(f"{naziv_jd.capitalize()} dodan.", "ok")
            return redirect(url_for(endpoint))
        zapisi = conn.execute(
            f"SELECT t.*, ({broj_sql}) AS broj_zapisa FROM {tablica} t ORDER BY t.naziv"
        ).fetchall()
        return render_template("partneri.html", zapisi=zapisi, naslov=naslov,
                               naziv_jd=naziv_jd)

    @app.route("/dobavljaci", methods=["GET", "POST"])
    @login_required
    def dobavljaci():
        return _sifrarnik("dobavljaci", "Šifrarnik dobavljača", "dobavljač",
                          "dobavljaci",
                          "SELECT COUNT(*) FROM ulaz u WHERE u.dobavljac_id = t.id")

    @app.route("/kupci", methods=["GET", "POST"])
    @login_required
    def kupci():
        return _sifrarnik("kupci", "Šifrarnik kupaca", "kupac", "kupci",
                          "SELECT COUNT(*) FROM prodaja p WHERE p.kupac_id = t.id")

    # ---------------- Postavke (vlasnik) ----------------

    @app.route("/postavke", methods=["GET", "POST"])
    @vlasnik_required
    def postavke():
        conn = get_conn()
        if request.method == "POST":
            for kljuc in ("trgovina.naziv", "trgovina.adresa", "trgovina.oib",
                          "redni_broj.start.ulaz", "redni_broj.start.prodaja",
                          "redni_broj.start.streljivo"):
                vrijednost = request.form.get(kljuc, "").strip()
                if kljuc.startswith("redni_broj.") and vrijednost and not vrijednost.isdigit():
                    flash(f"Početni redni broj mora biti broj ({kljuc}).", "error")
                    return redirect(url_for("postavke"))
                if vrijednost:
                    db.set_postavka(conn, kljuc, vrijednost)
            conn.commit()
            flash("Postavke spremljene.", "ok")
            return redirect(url_for("postavke"))
        vrijednosti = {r["kljuc"]: r["vrijednost"]
                       for r in conn.execute("SELECT * FROM postavke").fetchall()}
        return render_template("postavke.html", p=vrijednosti)


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)

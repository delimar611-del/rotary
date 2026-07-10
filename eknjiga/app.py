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


def dohvati_ili_kreiraj_dobavljaca(conn, form) -> tuple[int | None, str | None]:
    """Vrati (dobavljac_id, greška). Ako je unesen novi dobavljač, kreiraj ga."""
    dobavljac_id = form.get("dobavljac_id", "").strip()
    if dobavljac_id:
        row = conn.execute(
            "SELECT id FROM dobavljaci WHERE id = ? AND aktivan = 1", (dobavljac_id,)
        ).fetchone()
        if row is None:
            return None, "Odabrani dobavljač ne postoji."
        return row["id"], None

    naziv = form.get("novi_dobavljac_naziv", "").strip()
    if not naziv:
        return None, "Odaberite dobavljača iz šifrarnika ili unesite novog."
    adresa = form.get("novi_dobavljac_adresa", "").strip()
    oib = form.get("novi_dobavljac_oib", "").strip() or None
    if oib and not re.fullmatch(r"\d{11}", oib):
        return None, "OIB dobavljača mora imati točno 11 znamenki."
    if oib:
        postojeci = conn.execute(
            "SELECT id, naziv FROM dobavljaci WHERE oib = ?", (oib,)
        ).fetchone()
        if postojeci:
            return postojeci["id"], None  # isti OIB = isti dobavljač
    cur = conn.execute(
        "INSERT INTO dobavljaci (naziv, adresa, oib) VALUES (?, ?, ?)",
        (naziv, adresa, oib),
    )
    db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "dobavljaci",
             cur.lastrowid, "unos", None,
             {"naziv": naziv, "adresa": adresa, "oib": oib})
    return cur.lastrowid, None


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
                dobavljac_id, err = dohvati_ili_kreiraj_dobavljaca(conn, f)
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
                dobavljac_id, err = dohvati_ili_kreiraj_dobavljaca(conn, f)
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
        return render_template("ulaz_detalj.html", z=zapis, audit_zapisi=audit_zapisi)

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

    # ---------------- Šifrarnik dobavljača + autocomplete ----------------

    @app.get("/api/dobavljaci")
    @login_required
    def api_dobavljaci():
        q = request.args.get("q", "").strip()
        rows = get_conn().execute(
            "SELECT id, naziv, adresa, oib FROM dobavljaci "
            "WHERE aktivan = 1 AND (naziv LIKE ? OR oib LIKE ?) ORDER BY naziv LIMIT 10",
            (f"%{q}%", f"{q}%"),
        ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route("/dobavljaci", methods=["GET", "POST"])
    @login_required
    def dobavljaci():
        conn = get_conn()
        if request.method == "POST":
            naziv = request.form.get("naziv", "").strip()
            adresa = request.form.get("adresa", "").strip()
            oib = request.form.get("oib", "").strip() or None
            if not naziv:
                flash("Naziv je obavezan.", "error")
            elif oib and not re.fullmatch(r"\d{11}", oib):
                flash("OIB mora imati točno 11 znamenki.", "error")
            elif oib and conn.execute("SELECT 1 FROM dobavljaci WHERE oib = ?", (oib,)).fetchone():
                flash("Dobavljač s tim OIB-om već postoji.", "error")
            else:
                cur = conn.execute(
                    "INSERT INTO dobavljaci (naziv, adresa, oib) VALUES (?, ?, ?)",
                    (naziv, adresa, oib),
                )
                db.audit(conn, g.user["id"], g.user["korisnicko_ime"], "dobavljaci",
                         cur.lastrowid, "unos", None,
                         {"naziv": naziv, "adresa": adresa, "oib": oib})
                conn.commit()
                flash("Dobavljač dodan.", "ok")
            return redirect(url_for("dobavljaci"))
        zapisi = conn.execute(
            "SELECT d.*, (SELECT COUNT(*) FROM ulaz u WHERE u.dobavljac_id = d.id) AS broj_ulaza "
            "FROM dobavljaci d ORDER BY d.naziv"
        ).fetchall()
        return render_template("dobavljaci.html", zapisi=zapisi)

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

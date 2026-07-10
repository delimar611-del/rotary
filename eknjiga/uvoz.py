"""eKnjiga Oružja — CSV import za migraciju papirnatih knjiga i CSV izvoz.

Predlošci koriste hrvatska zaglavlja kolona; separator ; ili , (autodetekcija),
kodiranje UTF-8 (s BOM-om ili bez). Datumi u formatu GGGG-MM-DD.
Import je sve-ili-ništa: bilo koja greška odbija cijelu datoteku s popisom
grešaka po recima.
"""
from __future__ import annotations

import csv
import io
import re

import db

# Zaglavlja predložaka (redoslijed = redoslijed kolona u CSV-u)
PREDLOSCI = {
    "ulaz": [
        "redni_broj", "datum_nabave", "broj_oruznog_lista", "isprava",
        "dobavljac_naziv", "dobavljac_adresa", "dobavljac_oib",
        "vrsta", "kategorija", "marka_model", "kalibar", "tvornicki_broj",
        "napomena", "status", "legacy_knjiga", "legacy_stranica", "legacy_redni_broj",
    ],
    "prodaja": [
        "redni_broj", "datum_prodaje", "kupac_naziv", "kupac_adresa", "kupac_oib",
        "vrsta", "kategorija", "marka_model", "kalibar", "tvornicki_broj",
        "odobrenje_vrsta", "odobrenje_broj", "odobrenje_datum", "odobrenje_izdavatelj",
        "napomena", "legacy_knjiga", "legacy_stranica", "legacy_redni_broj",
    ],
    "streljivo": [
        "redni_broj", "datum_prodaje", "kupac_naziv", "kupac_adresa", "kupac_oib",
        "vrsta", "marka", "kalibar", "lot_broj", "kolicina",
        "odobrenje_vrsta", "odobrenje_broj", "odobrenje_datum", "odobrenje_izdavatelj",
        "oruzje_broj", "napomena", "legacy_knjiga", "legacy_stranica", "legacy_redni_broj",
    ],
}

# Prihvaćene vrijednosti odobrenje_vrsta (hrvatski nazivi i interni ključevi)
_ODOBRENJE_MAPA = {
    "odobrenje za nabavu": "odobrenje_za_nabavu",
    "odobrenje_za_nabavu": "odobrenje_za_nabavu",
    "oružni list": "oruzni_list",
    "oruzni list": "oruzni_list",
    "oruzni_list": "oruzni_list",
    "odobrenje za promet": "odobrenje_za_promet",
    "odobrenje_za_promet": "odobrenje_za_promet",
}

_DATUM_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def predlozak_csv(knjiga: str) -> str:
    """CSV predložak: samo redak zaglavlja."""
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerow(PREDLOSCI[knjiga])
    return out.getvalue()


def _procitaj(tekst: str, knjiga: str) -> tuple[list[dict], list[str]]:
    """Parsiraj CSV u retke; vrati (retci, greške zaglavlja)."""
    tekst = tekst.lstrip("﻿")
    prva = tekst.splitlines()[0] if tekst.strip() else ""
    delimiter = ";" if prva.count(";") >= prva.count(",") else ","
    citac = csv.DictReader(io.StringIO(tekst), delimiter=delimiter)
    ocekivano = PREDLOSCI[knjiga]
    stvarno = [(s or "").strip() for s in (citac.fieldnames or [])]
    nedostaje = [k for k in ocekivano if k not in stvarno]
    if nedostaje:
        return [], [f"U zaglavlju CSV-a nedostaju kolone: {', '.join(nedostaje)}. "
                    f"Preuzmite predložak i kopirajte podatke u njega."]
    retci = []
    for red in citac:
        retci.append({k: (red.get(k) or "").strip() for k in ocekivano})
    if not retci:
        return [], ["Datoteka ne sadrži nijedan redak podataka."]
    return retci, []


def _partner_id(conn, cache: dict, naziv: str, adresa: str, oib: str,
                tablica: str, user) -> int:
    """Pronađi partnera po OIB-u pa po nazivu; inače kreiraj. Keširano po datoteci."""
    kljuc = (tablica, oib or naziv.lower())
    if kljuc in cache:
        return cache[kljuc]
    row = None
    if oib:
        row = conn.execute(f"SELECT id FROM {tablica} WHERE oib = ?", (oib,)).fetchone()
    if row is None:
        row = conn.execute(
            f"SELECT id FROM {tablica} WHERE naziv = ? ORDER BY id LIMIT 1", (naziv,)
        ).fetchone()
    if row is not None:
        cache[kljuc] = row["id"]
        return row["id"]
    cur = conn.execute(
        f"INSERT INTO {tablica} (naziv, adresa, oib) VALUES (?, ?, ?)",
        (naziv, adresa, oib or None),
    )
    db.audit(conn, user["id"], user["korisnicko_ime"], tablica, cur.lastrowid,
             "unos", None, {"naziv": naziv, "adresa": adresa, "oib": oib or None})
    cache[kljuc] = cur.lastrowid
    return cur.lastrowid


def _validiraj_zajednicko(red: dict, i: int, greske: list[str],
                          datum_polje: str, partner_prefix: str) -> None:
    if not _DATUM_RE.match(red[datum_polje]):
        greske.append(f"Redak {i}: {datum_polje} mora biti GGGG-MM-DD — "
                      f"dobiveno „{red[datum_polje]}”.")
    if not red[f"{partner_prefix}_naziv"]:
        greske.append(f"Redak {i}: {partner_prefix}_naziv je obavezan.")
    oib = red[f"{partner_prefix}_oib"]
    if oib and not re.fullmatch(r"\d{11}", oib):
        greske.append(f"Redak {i}: {partner_prefix}_oib mora imati 11 znamenki — "
                      f"dobiveno „{oib}”.")
    rb = red["redni_broj"]
    if rb and not rb.isdigit():
        greske.append(f"Redak {i}: redni_broj mora biti broj — dobiveno „{rb}”.")


def _dodijeli_redne_brojeve(conn, knjiga: str, retci: list[dict],
                            greske: list[str]) -> None:
    """Redni broj iz datoteke (validiran na duplikate) ili automatski slijedom."""
    postojeci = {r["redni_broj"] for r in
                 conn.execute(f"SELECT redni_broj FROM {knjiga}").fetchall()}
    sljedeci = db.sljedeci_redni_broj(conn, knjiga)
    videni: set[int] = set()
    for i, red in enumerate(retci, start=2):
        if red["redni_broj"]:
            rb = int(red["redni_broj"])
            if rb in postojeci:
                greske.append(f"Redak {i}: redni broj {rb} već postoji u knjizi.")
            if rb in videni:
                greske.append(f"Redak {i}: redni broj {rb} ponavlja se u datoteci.")
            videni.add(rb)
            red["_rb"] = rb
        else:
            while sljedeci in postojeci or sljedeci in videni:
                sljedeci += 1
            videni.add(sljedeci)
            red["_rb"] = sljedeci
            sljedeci += 1


def uvezi_csv(conn, knjiga: str, tekst: str, user) -> tuple[int, list[str]]:
    """Uvezi CSV u knjigu. Vraća (broj uvezenih, greške). Sve-ili-ništa —
    poziva se unutar otvorene transakcije; kod grešaka ništa nije upisano."""
    retci, greske = _procitaj(tekst, knjiga)
    if greske:
        return 0, greske

    if knjiga == "ulaz":
        return _uvezi_ulaz(conn, retci, user)
    if knjiga == "prodaja":
        return _uvezi_prodaja(conn, retci, user)
    return _uvezi_streljivo(conn, retci, user)


def _uvezi_ulaz(conn, retci, user):
    greske: list[str] = []
    for i, red in enumerate(retci, start=2):
        _validiraj_zajednicko(red, i, greske, "datum_nabave", "dobavljac")
        for polje in ("isprava", "vrsta", "marka_model", "kalibar", "tvornicki_broj"):
            if not red[polje]:
                greske.append(f"Redak {i}: {polje} je obavezan.")
        if red["kategorija"] not in ("A", "B", "C"):
            greske.append(f"Redak {i}: kategorija mora biti A, B ili C — "
                          f"dobiveno „{red['kategorija']}”.")
        if red["status"] not in ("", "na_stanju", "prodano"):
            greske.append(f"Redak {i}: status mora biti na_stanju ili prodano "
                          f"(prazno = na_stanju) — dobiveno „{red['status']}”.")
    _dodijeli_redne_brojeve(conn, "ulaz", retci, greske)
    if greske:
        return 0, greske

    cache: dict = {}
    for red in retci:
        dobavljac_id = _partner_id(conn, cache, red["dobavljac_naziv"],
                                   red["dobavljac_adresa"], red["dobavljac_oib"],
                                   "dobavljaci", user)
        cur = conn.execute(
            "INSERT INTO ulaz (redni_broj, datum_nabave, broj_oruznog_lista, isprava, "
            "dobavljac_id, vrsta, kategorija, marka_model, kalibar, tvornicki_broj, "
            "napomena, status, legacy_knjiga, legacy_stranica, legacy_redni_broj, "
            "kreirao_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (red["_rb"], red["datum_nabave"], red["broj_oruznog_lista"] or None,
             red["isprava"], dobavljac_id, red["vrsta"], red["kategorija"],
             red["marka_model"], red["kalibar"], red["tvornicki_broj"],
             red["napomena"], red["status"] or "na_stanju",
             red["legacy_knjiga"] or None, red["legacy_stranica"] or None,
             red["legacy_redni_broj"] or None, user["id"]),
        )
        novi = conn.execute("SELECT * FROM ulaz WHERE id = ?", (cur.lastrowid,)).fetchone()
        db.audit(conn, user["id"], user["korisnicko_ime"], "ulaz", cur.lastrowid,
                 "unos", None, dict(novi) | {"_migracija": True})
    return len(retci), []


def _odobrenje_kljuc(red, i, greske, dozvoljene) -> str | None:
    kljuc = _ODOBRENJE_MAPA.get(red["odobrenje_vrsta"].lower())
    if kljuc is None or kljuc not in dozvoljene:
        greske.append(f"Redak {i}: odobrenje_vrsta mora biti jedna od: "
                      f"{', '.join(dozvoljene)} — dobiveno „{red['odobrenje_vrsta']}”.")
    return kljuc


def _uvezi_prodaja(conn, retci, user):
    greske: list[str] = []
    dozvoljene = ("odobrenje za nabavu", "oružni list", "odobrenje za promet")
    for i, red in enumerate(retci, start=2):
        _validiraj_zajednicko(red, i, greske, "datum_prodaje", "kupac")
        for polje in ("vrsta", "marka_model", "kalibar", "tvornicki_broj",
                      "odobrenje_broj", "odobrenje_datum", "odobrenje_izdavatelj"):
            if not red[polje]:
                greske.append(f"Redak {i}: {polje} je obavezan.")
        if red["kategorija"] not in ("A", "B", "C"):
            greske.append(f"Redak {i}: kategorija mora biti A, B ili C.")
        red["_odobrenje"] = _odobrenje_kljuc(
            red, i, greske,
            ("odobrenje_za_nabavu", "oruzni_list", "odobrenje_za_promet"))
        if red["odobrenje_datum"] and not _DATUM_RE.match(red["odobrenje_datum"]):
            greske.append(f"Redak {i}: odobrenje_datum mora biti GGGG-MM-DD.")
    _dodijeli_redne_brojeve(conn, "prodaja", retci, greske)
    if greske:
        return 0, greske

    cache: dict = {}
    povezano = set()  # ulaz.id već vezani u ovoj datoteci
    for red in retci:
        kupac_id = _partner_id(conn, cache, red["kupac_naziv"], red["kupac_adresa"],
                               red["kupac_oib"], "kupci", user)
        # auto-povezivanje: komad na stanju s istim tvorničkim brojem
        ulaz = conn.execute(
            "SELECT * FROM ulaz WHERE tvornicki_broj = ? AND status = 'na_stanju' "
            "ORDER BY redni_broj LIMIT 1", (red["tvornicki_broj"],),
        ).fetchone()
        ulaz_id = ulaz["id"] if ulaz and ulaz["id"] not in povezano else None
        cur = conn.execute(
            "INSERT INTO prodaja (redni_broj, datum_prodaje, kupac_id, vrsta, kategorija, "
            "marka_model, kalibar, tvornicki_broj, odobrenje_vrsta, odobrenje_broj, "
            "odobrenje_datum, odobrenje_izdavatelj, napomena, ulaz_id, "
            "legacy_knjiga, legacy_stranica, legacy_redni_broj, kreirao_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (red["_rb"], red["datum_prodaje"], kupac_id, red["vrsta"], red["kategorija"],
             red["marka_model"], red["kalibar"], red["tvornicki_broj"],
             red["_odobrenje"], red["odobrenje_broj"], red["odobrenje_datum"],
             red["odobrenje_izdavatelj"], red["napomena"],
             ulaz_id, red["legacy_knjiga"] or None, red["legacy_stranica"] or None,
             red["legacy_redni_broj"] or None, user["id"]),
        )
        novi = conn.execute("SELECT * FROM prodaja WHERE id = ?", (cur.lastrowid,)).fetchone()
        db.audit(conn, user["id"], user["korisnicko_ime"], "prodaja", cur.lastrowid,
                 "unos", None, dict(novi) | {"_migracija": True})
        if ulaz_id is not None:
            povezano.add(ulaz_id)
            conn.execute("UPDATE ulaz SET status = 'prodano' WHERE id = ?", (ulaz_id,))
            db.audit(conn, user["id"], user["korisnicko_ime"], "ulaz", ulaz_id,
                     "izmjena", {"status": "na_stanju"},
                     {"status": "prodano", "_migracija": True})
    return len(retci), []


def _uvezi_streljivo(conn, retci, user):
    greske: list[str] = []
    for i, red in enumerate(retci, start=2):
        _validiraj_zajednicko(red, i, greske, "datum_prodaje", "kupac")
        for polje in ("vrsta", "marka", "kalibar", "lot_broj",
                      "odobrenje_broj", "odobrenje_izdavatelj"):
            if not red[polje]:
                greske.append(f"Redak {i}: {polje} je obavezan.")
        if not red["kolicina"].isdigit() or int(red["kolicina"]) <= 0:
            greske.append(f"Redak {i}: kolicina mora biti pozitivan broj — "
                          f"dobiveno „{red['kolicina']}”.")
        red["_odobrenje"] = _odobrenje_kljuc(
            red, i, greske, ("oruzni_list", "odobrenje_za_promet"))
        if red["odobrenje_datum"] and not _DATUM_RE.match(red["odobrenje_datum"]):
            greske.append(f"Redak {i}: odobrenje_datum mora biti GGGG-MM-DD.")
    _dodijeli_redne_brojeve(conn, "streljivo", retci, greske)
    if greske:
        return 0, greske

    cache: dict = {}
    for red in retci:
        kupac_id = _partner_id(conn, cache, red["kupac_naziv"], red["kupac_adresa"],
                               red["kupac_oib"], "kupci", user)
        cur = conn.execute(
            "INSERT INTO streljivo (redni_broj, datum_prodaje, kupac_id, vrsta, marka, "
            "kalibar, lot_broj, kolicina, odobrenje_vrsta, odobrenje_broj, "
            "odobrenje_datum, odobrenje_izdavatelj, oruzje_broj, napomena, "
            "legacy_knjiga, legacy_stranica, legacy_redni_broj, kreirao_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (red["_rb"], red["datum_prodaje"], kupac_id, red["vrsta"], red["marka"],
             red["kalibar"], red["lot_broj"], int(red["kolicina"]),
             red["_odobrenje"], red["odobrenje_broj"], red["odobrenje_datum"] or None,
             red["odobrenje_izdavatelj"], red["oruzje_broj"] or None, red["napomena"],
             red["legacy_knjiga"] or None, red["legacy_stranica"] or None,
             red["legacy_redni_broj"] or None, user["id"]),
        )
        novi = conn.execute("SELECT * FROM streljivo WHERE id = ?", (cur.lastrowid,)).fetchone()
        db.audit(conn, user["id"], user["korisnicko_ime"], "streljivo", cur.lastrowid,
                 "unos", None, dict(novi) | {"_migracija": True})
    return len(retci), []


# ---------------------------------------------------------------------------
# CSV izvoz
# ---------------------------------------------------------------------------

IZVOZ_SQL = {
    "ulaz": (
        "SELECT u.redni_broj, u.datum_nabave, u.broj_oruznog_lista, u.isprava, "
        "d.naziv AS dobavljac_naziv, d.adresa AS dobavljac_adresa, d.oib AS dobavljac_oib, "
        "u.vrsta, u.kategorija, u.marka_model, u.kalibar, u.tvornicki_broj, "
        "u.napomena, u.status, u.storno_razlog, "
        "u.legacy_knjiga, u.legacy_stranica, u.legacy_redni_broj "
        "FROM ulaz u JOIN dobavljaci d ON d.id = u.dobavljac_id ORDER BY u.redni_broj"
    ),
    "prodaja": (
        "SELECT p.redni_broj, p.datum_prodaje, "
        "k.naziv AS kupac_naziv, k.adresa AS kupac_adresa, k.oib AS kupac_oib, "
        "p.vrsta, p.kategorija, p.marka_model, p.kalibar, p.tvornicki_broj, "
        "p.odobrenje_vrsta, p.odobrenje_broj, p.odobrenje_datum, p.odobrenje_izdavatelj, "
        "p.napomena, u.redni_broj AS ulaz_redni_broj, p.status, p.storno_razlog, "
        "p.legacy_knjiga, p.legacy_stranica, p.legacy_redni_broj "
        "FROM prodaja p JOIN kupci k ON k.id = p.kupac_id "
        "LEFT JOIN ulaz u ON u.id = p.ulaz_id ORDER BY p.redni_broj"
    ),
    "streljivo": (
        "SELECT s.redni_broj, s.datum_prodaje, "
        "k.naziv AS kupac_naziv, k.adresa AS kupac_adresa, k.oib AS kupac_oib, "
        "s.vrsta, s.marka, s.kalibar, s.lot_broj, s.kolicina, "
        "s.odobrenje_vrsta, s.odobrenje_broj, s.odobrenje_datum, s.odobrenje_izdavatelj, "
        "s.oruzje_broj, s.napomena, s.status, s.storno_razlog, "
        "s.legacy_knjiga, s.legacy_stranica, s.legacy_redni_broj "
        "FROM streljivo s JOIN kupci k ON k.id = s.kupac_id ORDER BY s.redni_broj"
    ),
}


def izvoz_csv(conn, knjiga: str) -> str:
    """Cijela knjiga kao CSV (';' separator, za Excel uz UTF-8 BOM na izlazu)."""
    rows = conn.execute(IZVOZ_SQL[knjiga]).fetchall()
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    if rows:
        w.writerow(rows[0].keys())
    else:
        w.writerow(PREDLOSCI[knjiga])
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return out.getvalue()

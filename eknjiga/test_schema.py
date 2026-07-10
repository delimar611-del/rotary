"""Testovi sheme baze — korak 1.

Pokretanje:  python3 -m unittest test_schema -v
"""
import sqlite3
import unittest

import db


def _demo_korisnik(conn):
    conn.execute(
        "INSERT INTO korisnici (korisnicko_ime, lozinka_hash, ime_prezime, rola) "
        "VALUES ('vlasnik', 'x', 'Test Vlasnik', 'vlasnik')"
    )
    return conn.execute("SELECT id FROM korisnici").fetchone()["id"]


def _demo_dobavljac(conn):
    conn.execute(
        "INSERT INTO dobavljaci (naziv, adresa, oib) VALUES ('Oružje d.o.o.', 'Zagreb', '12345678901')"
    )
    return conn.execute("SELECT id FROM dobavljaci").fetchone()["id"]


def _demo_kupac(conn):
    conn.execute(
        "INSERT INTO kupci (naziv, adresa, oib) VALUES ('Ivan Horvat', 'Ilica 1, Zagreb', '98765432109')"
    )
    return conn.execute("SELECT id FROM kupci").fetchone()["id"]


def _demo_ulaz(conn, uid, did, redni_broj=1, tvornicki_broj="AB1234"):
    conn.execute(
        "INSERT INTO ulaz (redni_broj, datum_nabave, isprava, dobavljac_id, vrsta, kategorija, "
        "marka_model, kalibar, tvornicki_broj, kreirao_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (redni_broj, "2026-07-01", "Račun 55/2026", did, "lovački karabin", "B",
         "CZ 557", ".30-06", tvornicki_broj, uid),
    )
    return conn.execute("SELECT id FROM ulaz WHERE redni_broj = ?", (redni_broj,)).fetchone()["id"]


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.init_db(":memory:")
        self.uid = _demo_korisnik(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_dijakritika_utf8(self):
        did = _demo_dobavljac(self.conn)
        _demo_ulaz(self.conn, self.uid, did)
        row = self.conn.execute("SELECT vrsta FROM ulaz").fetchone()
        self.assertEqual(row["vrsta"], "lovački karabin")

    def test_oib_mora_biti_11_znamenki(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO kupci (naziv, oib) VALUES ('X', '123')")

    def test_oib_moze_biti_null_strani_dobavljac(self):
        self.conn.execute("INSERT INTO dobavljaci (naziv, adresa) VALUES ('Blaser GmbH', 'Isny, DE')")

    def test_duplikat_tvornickog_broja_dopusten(self):
        # Aplikacija upozorava, ali baza NE smije zabraniti duplikat (override uz napomenu)
        did = _demo_dobavljac(self.conn)
        _demo_ulaz(self.conn, self.uid, did, redni_broj=1, tvornicki_broj="X1")
        _demo_ulaz(self.conn, self.uid, did, redni_broj=2, tvornicki_broj="X1")

    def test_redni_broj_unique(self):
        did = _demo_dobavljac(self.conn)
        _demo_ulaz(self.conn, self.uid, did, redni_broj=1)
        with self.assertRaises(sqlite3.IntegrityError):
            _demo_ulaz(self.conn, self.uid, did, redni_broj=1, tvornicki_broj="Y2")

    def test_sljedeci_redni_broj_nastavak_papirnate_knjige(self):
        # Migracija: papirnata knjiga stala na 730 → digitalna kreće od 731
        db.set_postavka(self.conn, "redni_broj.start.ulaz", "731")
        self.assertEqual(db.sljedeci_redni_broj(self.conn, "ulaz"), 731)
        did = _demo_dobavljac(self.conn)
        _demo_ulaz(self.conn, self.uid, did, redni_broj=731)
        self.assertEqual(db.sljedeci_redni_broj(self.conn, "ulaz"), 732)

    def test_brisanje_ulaza_zabranjeno(self):
        did = _demo_dobavljac(self.conn)
        _demo_ulaz(self.conn, self.uid, did)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM ulaz")

    def test_storno_trazi_obrazlozenje(self):
        did = _demo_dobavljac(self.conn)
        uid_ulaz = _demo_ulaz(self.conn, self.uid, did)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE ulaz SET status = 'storno' WHERE id = ?", (uid_ulaz,))
        # sa obrazloženjem prolazi
        self.conn.execute(
            "UPDATE ulaz SET status = 'storno', storno_razlog = 'pogrešan unos', "
            "storno_korisnik_id = ?, storno_vrijeme = datetime('now') WHERE id = ?",
            (self.uid, uid_ulaz),
        )

    def test_jedan_ulaz_jedna_aktivna_prodaja(self):
        did, kid = _demo_dobavljac(self.conn), _demo_kupac(self.conn)
        ulaz_id = _demo_ulaz(self.conn, self.uid, did)

        def prodaj(redni_broj, status="aktivno", razlog=None):
            self.conn.execute(
                "INSERT INTO prodaja (redni_broj, datum_prodaje, kupac_id, vrsta, kategorija, "
                "marka_model, kalibar, tvornicki_broj, odobrenje_vrsta, odobrenje_broj, "
                "odobrenje_datum, odobrenje_izdavatelj, ulaz_id, status, storno_razlog, kreirao_id) "
                "VALUES (?, '2026-07-05', ?, 'lovački karabin', 'B', 'CZ 557', '.30-06', 'AB1234', "
                "'odobrenje_za_nabavu', '511-19-04/2-26', '2026-06-20', 'PU zagrebačka', ?, ?, ?, ?)",
                (redni_broj, kid, ulaz_id, status, razlog, self.uid),
            )

        prodaj(1)
        with self.assertRaises(sqlite3.IntegrityError):
            prodaj(2)  # drugi aktivni zapis za isti ulaz → zabranjeno
        # storno prve prodaje oslobađa ulaz za novu prodaju
        self.conn.execute(
            "UPDATE prodaja SET status = 'storno', storno_razlog = 'odustao kupac' WHERE redni_broj = 1"
        )
        prodaj(2)

    def test_audit_log_append_only(self):
        db.audit(self.conn, self.uid, "vlasnik", "ulaz", 1, "unos", None, {"marka_model": "CZ 557"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE audit_log SET novo_json = '{}'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM audit_log")
        row = self.conn.execute("SELECT novo_json FROM audit_log").fetchone()
        self.assertIn("CZ 557", row["novo_json"])

    def test_streljivo_kolicina_pozitivna(self):
        kid = _demo_kupac(self.conn)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO streljivo (redni_broj, datum_prodaje, kupac_id, vrsta, marka, kalibar, "
                "lot_broj, kolicina, odobrenje_vrsta, odobrenje_broj, kreirao_id) "
                "VALUES (1, '2026-07-05', ?, 'karabinsko', 'Geco', '.30-06', 'L123', 0, 'oruzni_list', '123', ?)",
                (kid, self.uid),
            )


if __name__ == "__main__":
    unittest.main()

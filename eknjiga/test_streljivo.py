"""Testovi evidencije streljiva (korak 4).

Pokretanje:  python3 -m unittest test_streljivo -v
"""
import tempfile
import unittest
from pathlib import Path

import app as app_module
import db


STRELJIVO = {
    "datum_prodaje": "2026-07-10",
    "novi_kupac_naziv": "Ivan Horvat",
    "novi_kupac_adresa": "Ilica 1, Zagreb",
    "novi_kupac_oib": "98765432109",
    "vrsta": "karabinsko streljivo",
    "marka": "Geco",
    "kalibar": ".30-06",
    "lot_broj": "L-4471",
    "kolicina": "50",
    "odobrenje_vrsta": "oruzni_list",
    "odobrenje_broj": "OL-12345",
    "odobrenje_izdavatelj": "PP Sesvete",
    "oruzje_broj": "C557-88231",
}


class StreljivoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = app_module.create_app(Path(self.tmp.name) / "test.db")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.client.post("/setup", data={
            "ime_prezime": "Vlado Vlasnik", "korisnicko_ime": "vlado",
            "lozinka": "tajna12345"})
        self.client.post("/login", data={
            "korisnicko_ime": "vlado", "lozinka": "tajna12345"})

    def tearDown(self):
        self.tmp.cleanup()

    def conn(self):
        return db.get_conn(self.app.config["DB_PATH"])

    def unos(self, **extra):
        return self.client.post("/streljivo/novo", data={**STRELJIVO, **extra},
                                follow_redirects=True)

    # ------------------------------------------------------------------

    def test_unos_prodaje_civilu_na_oruzni_list(self):
        r = self.unos()
        self.assertIn("Upisan redni broj 1", r.get_data(as_text=True))
        conn = self.conn()
        z = conn.execute("SELECT * FROM streljivo").fetchone()
        self.assertEqual(z["lot_broj"], "L-4471")
        self.assertEqual(z["kolicina"], 50)
        self.assertEqual(z["oruzje_broj"], "C557-88231")
        self.assertEqual(z["odobrenje_izdavatelj"], "PP Sesvete")
        a = conn.execute("SELECT * FROM audit_log WHERE tablica='streljivo'").fetchone()
        self.assertEqual(a["akcija"], "unos")
        conn.close()

    def test_oruzni_list_trazi_broj_oruzja(self):
        r = self.unos(oruzje_broj="")
        self.assertIn("tvornički broj", r.get_data(as_text=True).lower())
        conn = self.conn()
        self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM streljivo").fetchone()["n"], 0)
        conn.close()

    def test_odobrenje_za_promet_ne_trazi_broj_oruzja(self):
        r = self.unos(odobrenje_vrsta="odobrenje_za_promet", oruzje_broj="",
                      novi_kupac_naziv="Lovački obrt Srna",
                      novi_kupac_adresa="Trg 1, Sisak", novi_kupac_oib="11111111119")
        self.assertIn("Upisan redni broj 1", r.get_data(as_text=True))

    def test_kupac_bez_oiba_ili_adrese_odbijen(self):
        r = self.unos(novi_kupac_oib="")
        html = r.get_data(as_text=True)
        self.assertIn("OIB", html)
        self.assertIn("obavezni su ime i prezime, adresa i OIB", html)
        r = self.unos(novi_kupac_adresa="", novi_kupac_oib="98765432109")
        self.assertIn("adresa", r.get_data(as_text=True))
        conn = self.conn()
        self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM streljivo").fetchone()["n"], 0)
        conn.close()

    def test_bulk_po_lotovima(self):
        r = self.client.post("/streljivo/bulk", data={
            **{k: v for k, v in STRELJIVO.items() if k not in ("lot_broj", "kolicina")},
            "lotovi": "L-1; 50\nL-2, 50\nL-3; 100"}, follow_redirects=True)
        self.assertIn("Upisano 3 zapisa", r.get_data(as_text=True))
        conn = self.conn()
        rows = conn.execute(
            "SELECT redni_broj, lot_broj, kolicina FROM streljivo ORDER BY redni_broj"
        ).fetchall()
        self.assertEqual([(r["redni_broj"], r["lot_broj"], r["kolicina"]) for r in rows],
                         [(1, "L-1", 50), (2, "L-2", 50), (3, "L-3", 100)])
        conn.close()

    def test_bulk_neispravan_redak_odbijen(self):
        r = self.client.post("/streljivo/bulk", data={
            **{k: v for k, v in STRELJIVO.items() if k not in ("lot_broj", "kolicina")},
            "lotovi": "L-1; 50\nL-2; puno"}, follow_redirects=True)
        self.assertIn("količina mora biti pozitivan broj", r.get_data(as_text=True))
        conn = self.conn()
        self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM streljivo").fetchone()["n"], 0)
        conn.close()

    def test_pretraga_po_lotu_i_broju_oruzja(self):
        self.unos()
        html = self.client.get("/streljivo?q=L-4471").get_data(as_text=True)
        self.assertIn("Geco", html)
        html = self.client.get("/streljivo?q=C557-88231").get_data(as_text=True)
        self.assertIn("L-4471", html)
        html = self.client.get("/streljivo?q=NEPOSTOJI").get_data(as_text=True)
        self.assertIn("Nema zapisa", html)

    def test_storno_uz_obrazlozenje(self):
        self.unos()
        r = self.client.post("/streljivo/1/storno", data={
            "storno_razlog": "pogrešan unos"}, follow_redirects=True)
        self.assertIn("storniran", r.get_data(as_text=True))
        conn = self.conn()
        self.assertEqual(conn.execute("SELECT status FROM streljivo").fetchone()["status"],
                         "storno")
        conn.close()

    def test_prodavac_ne_moze_uredivati_ni_stornirati(self):
        self.unos()
        conn = self.conn()
        from werkzeug.security import generate_password_hash
        conn.execute(
            "INSERT INTO korisnici (korisnicko_ime, lozinka_hash, ime_prezime, rola) "
            "VALUES ('pero', ?, 'Pero Prodavač', 'prodavac')",
            (generate_password_hash("tajna12345"),))
        conn.close()
        self.client.post("/login", data={"korisnicko_ime": "pero", "lozinka": "tajna12345"})
        self.assertEqual(self.client.get("/streljivo/1/uredi").status_code, 403)
        self.assertEqual(self.client.post("/streljivo/1/storno",
                         data={"storno_razlog": "x"}).status_code, 403)

    def test_migracija_dodaje_oruzje_broj_starim_bazama(self):
        # simuliraj bazu bez kolone oruzje_broj
        import sqlite3
        conn = self.conn()
        conn.execute("ALTER TABLE streljivo DROP COLUMN oruzje_broj")
        conn.close()
        conn = db.init_db(self.app.config["DB_PATH"])
        kolone = {r["name"] for r in conn.execute("PRAGMA table_info(streljivo)")}
        self.assertIn("oruzje_broj", kolone)
        conn.close()


if __name__ == "__main__":
    unittest.main()

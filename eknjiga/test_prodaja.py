"""Testovi prodajne knjige (korak 3): križno povezivanje, statusi, storno.

Pokretanje:  python3 -m unittest test_prodaja -v
"""
import tempfile
import unittest
from pathlib import Path

import app as app_module
import db


ORUZJE = {
    "datum_nabave": "2026-07-01",
    "isprava": "Račun br. 55/2026",
    "novi_dobavljac_naziv": "Oružje d.o.o.",
    "novi_dobavljac_oib": "12345678901",
    "vrsta": "lovački karabin",
    "kategorija": "B",
    "marka_model": "CZ 557",
    "kalibar": ".30-06",
}

PRODAJA = {
    "datum_prodaje": "2026-07-05",
    "novi_kupac_naziv": "Ivan Horvat",
    "novi_kupac_adresa": "Ilica 1, Zagreb",
    "novi_kupac_oib": "98765432109",
    "vrsta": "lovački karabin",
    "kategorija": "B",
    "marka_model": "CZ 557",
    "kalibar": ".30-06",
    "tvornicki_broj": "AB1234",
    "odobrenje_vrsta": "odobrenje_za_nabavu",
    "odobrenje_broj": "511-19-04/2-26",
    "odobrenje_datum": "2026-06-20",
    "odobrenje_izdavatelj": "PU zagrebačka",
}


class ProdajaTest(unittest.TestCase):
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
        # jedan ulaz na stanju (id=1, r.br. 1)
        self.client.post("/ulaz/novi", data={**ORUZJE, "tvornicki_broj": "AB1234"})

    def tearDown(self):
        self.tmp.cleanup()

    def conn(self):
        return db.get_conn(self.app.config["DB_PATH"])

    def prodaj(self, **extra):
        return self.client.post("/prodaja/nova", data={
            **PRODAJA, "ulaz_id": "1", **extra}, follow_redirects=True)

    # ------------------------------------------------------------------

    def test_prodaja_iz_ulaza_prefill(self):
        html = self.client.get("/prodaja/nova?ulaz_id=1").get_data(as_text=True)
        self.assertIn("CZ 557", html)
        self.assertIn("AB1234", html)
        self.assertIn("Podaci o oružju su predispunjeni", html)

    def test_pronalazak_ulaza_po_tvornickom_broju(self):
        html = self.client.get("/prodaja/nova?tb=AB1234").get_data(as_text=True)
        self.assertIn("Prodaja iz ulaza", html)
        self.assertIn("CZ 557", html)
        html = self.client.get("/prodaja/nova?tb=NEPOSTOJI").get_data(as_text=True)
        self.assertIn("Nema komada na stanju", html)

    def test_prodaja_mijenja_status_i_generira_vezu(self):
        r = self.prodaj()
        html = r.get_data(as_text=True)
        self.assertIn("Prodaja upisana pod rednim brojem 1", html)
        conn = self.conn()
        ulaz = conn.execute("SELECT status FROM ulaz WHERE id=1").fetchone()
        self.assertEqual(ulaz["status"], "prodano")
        p = conn.execute("SELECT * FROM prodaja").fetchone()
        self.assertEqual(p["napomena"], "ul. r.br. 1")
        self.assertEqual(p["ulaz_id"], 1)
        # audit za prodaju i za promjenu statusa ulaza
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM audit_log WHERE tablica='prodaja' AND akcija='unos'").fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM audit_log WHERE tablica='ulaz' AND akcija='izmjena' "
            "AND novo_json LIKE '%prodano%'").fetchone())
        conn.close()

    def test_veza_koristi_legacy_referencu(self):
        conn = self.conn()
        conn.execute("UPDATE ulaz SET legacy_knjiga='2', legacy_stranica='14', "
                     "legacy_redni_broj='388' WHERE id=1")
        conn.close()
        self.prodaj()
        conn = self.conn()
        p = conn.execute("SELECT napomena FROM prodaja").fetchone()
        self.assertEqual(p["napomena"], "ul. knjiga 2, str. 14, r.br. 388")
        conn.close()

    def test_dodatna_napomena_iza_veze(self):
        self.prodaj(napomena="preuzeto osobno")
        conn = self.conn()
        p = conn.execute("SELECT napomena FROM prodaja").fetchone()
        self.assertEqual(p["napomena"], "ul. r.br. 1; preuzeto osobno")
        conn.close()

    def test_prodani_komad_ne_moze_se_prodati_opet(self):
        self.prodaj()
        r = self.prodaj()
        self.assertIn("nije na stanju", r.get_data(as_text=True).replace("više nije", "nije"))
        conn = self.conn()
        self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM prodaja").fetchone()["n"], 1)
        conn.close()

    def test_prodaja_bez_ulaza_odbijena(self):
        r = self.client.post("/prodaja/nova", data=PRODAJA, follow_redirects=True)
        self.assertIn("Prodaja se kreira iz zapisa ulaza", r.get_data(as_text=True))

    def test_storno_prodaje_vraca_komad_na_stanje(self):
        self.prodaj()
        r = self.client.post("/prodaja/1/storno", data={
            "storno_razlog": "kupac odustao"}, follow_redirects=True)
        self.assertIn("komad vraćen na stanje", r.get_data(as_text=True))
        conn = self.conn()
        self.assertEqual(conn.execute("SELECT status FROM ulaz WHERE id=1").fetchone()["status"],
                         "na_stanju")
        self.assertEqual(conn.execute("SELECT status FROM prodaja WHERE id=1").fetchone()["status"],
                         "storno")
        conn.close()
        # komad se sada može ponovno prodati
        r = self.prodaj(novi_kupac_naziv="Marko Marić", novi_kupac_oib="11111111119")
        self.assertIn("Prodaja upisana pod rednim brojem 2", r.get_data(as_text=True))

    def test_kupac_autocomplete_i_sifrarnik(self):
        self.prodaj()
        data = self.client.get("/api/kupci?q=horv").get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["naziv"], "Ivan Horvat")
        html = self.client.get("/kupci").get_data(as_text=True)
        self.assertIn("Ivan Horvat", html)

    def test_pretraga_prodaje_po_kupcu(self):
        self.prodaj()
        html = self.client.get("/prodaja?q=Horvat").get_data(as_text=True)
        self.assertIn("AB1234", html)
        html = self.client.get("/prodaja?q=NEPOSTOJI").get_data(as_text=True)
        self.assertIn("Nema zapisa", html)

    def test_prodavac_ne_moze_stornirati_prodaju(self):
        self.prodaj()
        conn = self.conn()
        from werkzeug.security import generate_password_hash
        conn.execute(
            "INSERT INTO korisnici (korisnicko_ime, lozinka_hash, ime_prezime, rola) "
            "VALUES ('pero', ?, 'Pero Prodavač', 'prodavac')",
            (generate_password_hash("tajna12345"),))
        conn.close()
        self.client.post("/login", data={"korisnicko_ime": "pero", "lozinka": "tajna12345"})
        self.assertEqual(self.client.post("/prodaja/1/storno",
                         data={"storno_razlog": "x"}).status_code, 403)
        self.assertEqual(self.client.get("/prodaja/1/uredi").status_code, 403)

    def test_ulaz_detalj_pokazuje_vezanu_prodaju(self):
        self.prodaj()
        html = self.client.get("/ulaz/1").get_data(as_text=True)
        self.assertIn("Vezana prodaja", html)
        self.assertIn("Ivan Horvat", html)


if __name__ == "__main__":
    unittest.main()

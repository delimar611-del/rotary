"""Testovi web aplikacije — korak 2 (ulazna knjiga).

Pokretanje:  python3 -m unittest test_app -v
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
    "novi_dobavljac_adresa": "Savska 1, Zagreb",
    "novi_dobavljac_oib": "12345678901",
    "vrsta": "lovački karabin",
    "kategorija": "B",
    "marka_model": "CZ 557",
    "kalibar": ".30-06",
}


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = app_module.create_app(Path(self.tmp.name) / "test.db")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        # setup vlasnika + prijava
        self.client.post("/setup", data={
            "ime_prezime": "Vlado Vlasnik", "korisnicko_ime": "vlado",
            "lozinka": "tajna12345"})
        self.login("vlado", "tajna12345")

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, korisnicko, lozinka):
        return self.client.post("/login", data={
            "korisnicko_ime": korisnicko, "lozinka": lozinka})

    def dodaj_prodavaca(self):
        conn = db.get_conn(self.app.config["DB_PATH"])
        from werkzeug.security import generate_password_hash
        conn.execute(
            "INSERT INTO korisnici (korisnicko_ime, lozinka_hash, ime_prezime, rola) "
            "VALUES ('pero', ?, 'Pero Prodavač', 'prodavac')",
            (generate_password_hash("tajna12345"),))
        conn.commit(); conn.close()

    def unos(self, **extra):
        return self.client.post("/ulaz/novi", data={**ORUZJE,
                                "tvornicki_broj": "AB1234", **extra},
                                follow_redirects=True)

    # ------------------------------------------------------------------

    def test_neprijavljen_preusmjeren_na_login(self):
        c = self.app.test_client()  # bez sesije
        r = c.get("/ulaz")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_pojedinacni_unos_kreira_zapis_dobavljaca_i_audit(self):
        r = self.unos()
        self.assertIn("Upisan redni broj 1", r.get_data(as_text=True))
        conn = db.get_conn(self.app.config["DB_PATH"])
        z = conn.execute("SELECT * FROM ulaz").fetchone()
        self.assertEqual(z["tvornicki_broj"], "AB1234")
        self.assertEqual(z["status"], "na_stanju")
        d = conn.execute("SELECT * FROM dobavljaci").fetchone()
        self.assertEqual(d["oib"], "12345678901")
        a = conn.execute("SELECT * FROM audit_log WHERE tablica='ulaz'").fetchone()
        self.assertEqual(a["akcija"], "unos")
        self.assertIn("CZ 557", a["novo_json"])
        conn.close()

    def test_duplikat_upozorenje_pa_override_uz_napomenu(self):
        self.unos()
        # isti tvornički broj bez potvrde → odbijeno s upozorenjem
        r = self.unos()
        html = r.get_data(as_text=True)
        self.assertIn("već postoji", html)
        # s potvrdom ali bez napomene → odbijeno
        r = self.unos(potvrdi_duplikat="1")
        self.assertIn("napomena je obavezna", r.get_data(as_text=True))
        # s potvrdom i napomenom → prolazi
        r = self.unos(potvrdi_duplikat="1", napomena="Povrat pa ponovna nabava")
        self.assertIn("Upisan redni broj 2", r.get_data(as_text=True))

    def test_bulk_unos_uzastopni_redni_brojevi_od_731(self):
        # nastavak papirnate knjige od 731
        self.client.post("/postavke", data={"redni_broj.start.ulaz": "731"})
        r = self.client.post("/ulaz/bulk", data={
            **ORUZJE, "tvornicki_brojevi": "X1\nX2, X3\nX4"},
            follow_redirects=True)
        self.assertIn("Upisano 4 zapisa", r.get_data(as_text=True))
        self.assertIn("731", r.get_data(as_text=True))
        conn = db.get_conn(self.app.config["DB_PATH"])
        brojevi = [r["redni_broj"] for r in
                   conn.execute("SELECT redni_broj FROM ulaz ORDER BY redni_broj")]
        self.assertEqual(brojevi, [731, 732, 733, 734])
        conn.close()

    def test_bulk_ponovljeni_broj_u_listi_odbijen(self):
        r = self.client.post("/ulaz/bulk", data={
            **ORUZJE, "tvornicki_brojevi": "X1\nX1"}, follow_redirects=True)
        self.assertIn("više puta u listi", r.get_data(as_text=True))

    def test_pretraga_po_tvornickom_broju_i_dobavljacu(self):
        self.unos()
        html = self.client.get("/ulaz?q=AB1234").get_data(as_text=True)
        self.assertIn("CZ 557", html)
        html = self.client.get("/ulaz?q=Oružje d.o.o.").get_data(as_text=True)
        self.assertIn("AB1234", html)
        html = self.client.get("/ulaz?q=NEPOSTOJI").get_data(as_text=True)
        self.assertIn("Nema zapisa", html)

    def test_autocomplete_api(self):
        self.unos()
        data = self.client.get("/api/dobavljaci?q=oru").get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["naziv"], "Oružje d.o.o.")

    def test_storno_trazi_obrazlozenje_i_pise_audit(self):
        self.unos()
        r = self.client.post("/ulaz/1/storno", data={}, follow_redirects=True)
        self.assertIn("Obrazloženje storna je obavezno", r.get_data(as_text=True))
        r = self.client.post("/ulaz/1/storno", data={
            "storno_razlog": "pogrešan unos"}, follow_redirects=True)
        self.assertIn("storniran", r.get_data(as_text=True))
        conn = db.get_conn(self.app.config["DB_PATH"])
        self.assertEqual(conn.execute("SELECT status FROM ulaz").fetchone()["status"], "storno")
        a = conn.execute("SELECT * FROM audit_log WHERE akcija='storno'").fetchone()
        self.assertIsNotNone(a)
        conn.close()

    def test_izmjena_pise_staro_novo_u_audit(self):
        self.unos()
        self.client.post("/ulaz/1/uredi", data={**ORUZJE,
                         "tvornicki_broj": "AB1234", "kalibar": "8x57"},
                         follow_redirects=True)
        conn = db.get_conn(self.app.config["DB_PATH"])
        a = conn.execute("SELECT * FROM audit_log WHERE akcija='izmjena'").fetchone()
        self.assertIn(".30-06", a["staro_json"])
        self.assertIn("8x57", a["novo_json"])
        conn.close()

    def test_prodavac_ne_moze_uredivati_ni_stornirati_ni_postavke(self):
        self.unos()
        self.dodaj_prodavaca()
        self.login("pero", "tajna12345")
        self.assertEqual(self.client.get("/ulaz/1/uredi").status_code, 403)
        self.assertEqual(self.client.post("/ulaz/1/storno",
                         data={"storno_razlog": "x"}).status_code, 403)
        self.assertEqual(self.client.get("/postavke").status_code, 403)
        # ali smije unositi i pretraživati
        r = self.unos(tvornicki_broj="PP99")
        self.assertIn("Upisan redni broj 2", r.get_data(as_text=True))
        self.assertEqual(self.client.get("/ulaz?q=PP99").status_code, 200)


if __name__ == "__main__":
    unittest.main()

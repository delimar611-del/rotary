"""Testovi koraka 5: PDF ispis, CSV uvoz (migracija), CSV izvoz, zalihe.

Pokretanje:  python3 -m unittest test_ispis_uvoz -v
"""
import tempfile
import unittest
from pathlib import Path

import app as app_module
import db


CSV_ULAZ = """redni_broj;datum_nabave;broj_oruznog_lista;isprava;dobavljac_naziv;dobavljac_adresa;dobavljac_oib;vrsta;kategorija;marka_model;kalibar;tvornicki_broj;napomena;status;legacy_knjiga;legacy_stranica;legacy_redni_broj
729;2024-03-11;;Račun 18/2024;Oružje d.o.o.;Savska 1, Zagreb;12345678901;lovački karabin;B;CZ 557;.30-06;C557-001;;na_stanju;2;44;729
730;2024-04-02;OL-99;Kupoprodajni ugovor 5/2024;Marko Marić;Vlaška 8, Zagreb;;lovačka puška;C;Beretta 686;12/76;B686-77;komisija;na_stanju;2;44;730
"""

CSV_PRODAJA = """redni_broj;datum_prodaje;kupac_naziv;kupac_adresa;kupac_oib;vrsta;kategorija;marka_model;kalibar;tvornicki_broj;odobrenje_vrsta;odobrenje_broj;odobrenje_datum;odobrenje_izdavatelj;napomena;legacy_knjiga;legacy_stranica;legacy_redni_broj
;2024-05-20;Ivan Horvat;Ilica 1, Zagreb;98765432109;lovački karabin;B;CZ 557;.30-06;C557-001;odobrenje za nabavu;511-19/24;2024-05-10;PU zagrebačka;ul. knjiga 2, str. 44, r.br. 729;1;12;101
"""

CSV_STRELJIVO = """redni_broj;datum_prodaje;kupac_naziv;kupac_adresa;kupac_oib;vrsta;marka;kalibar;lot_broj;kolicina;odobrenje_vrsta;odobrenje_broj;odobrenje_datum;odobrenje_izdavatelj;oruzje_broj;napomena;legacy_knjiga;legacy_stranica;legacy_redni_broj
;2024-06-01;Ivan Horvat;Ilica 1, Zagreb;98765432109;karabinsko;Geco;.30-06;L-1;50;oružni list;OL-12345;;PP Sesvete;C557-001;;;;
"""


class IspisUvozTest(unittest.TestCase):
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

    def uvezi(self, knjiga, sadrzaj):
        return self.client.post("/migracija", data={
            "knjiga": knjiga,
            "datoteka": (Path(self.tmp.name) / "x.csv", "x.csv")
        } | {"datoteka": (__import__("io").BytesIO(sadrzaj.encode("utf-8")), "x.csv")},
            content_type="multipart/form-data", follow_redirects=True)

    # ---------------- uvoz ----------------

    def test_uvoz_ulaza_s_rednim_brojevima_iz_papira(self):
        r = self.uvezi("ulaz", CSV_ULAZ)
        self.assertIn("Uvezeno 2 zapisa", r.get_data(as_text=True))
        conn = self.conn()
        rows = conn.execute("SELECT * FROM ulaz ORDER BY redni_broj").fetchall()
        self.assertEqual([z["redni_broj"] for z in rows], [729, 730])
        self.assertEqual(rows[0]["legacy_redni_broj"], "729")
        # dobavljači kreirani, OIB povezan
        self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM dobavljaci").fetchone()["n"], 2)
        # sljedeći unos nastavlja od 731
        self.assertEqual(db.sljedeci_redni_broj(conn, "ulaz"), 731)
        conn.close()

    def test_uvoz_prodaje_automatski_povezuje_ulaz(self):
        self.uvezi("ulaz", CSV_ULAZ)
        r = self.uvezi("prodaja", CSV_PRODAJA)
        self.assertIn("Uvezeno 1 zapisa", r.get_data(as_text=True))
        conn = self.conn()
        p = conn.execute("SELECT * FROM prodaja").fetchone()
        u = conn.execute("SELECT * FROM ulaz WHERE tvornicki_broj='C557-001'").fetchone()
        self.assertEqual(p["ulaz_id"], u["id"])
        self.assertEqual(u["status"], "prodano")
        self.assertEqual(p["odobrenje_vrsta"], "odobrenje_za_nabavu")
        conn.close()

    def test_uvoz_streljiva(self):
        r = self.uvezi("streljivo", CSV_STRELJIVO)
        self.assertIn("Uvezeno 1 zapisa", r.get_data(as_text=True))
        conn = self.conn()
        s = conn.execute("SELECT * FROM streljivo").fetchone()
        self.assertEqual(s["odobrenje_vrsta"], "oruzni_list")
        self.assertEqual(s["oruzje_broj"], "C557-001")
        conn.close()

    def test_uvoz_sve_ili_nista(self):
        los = CSV_ULAZ.replace("lovačka puška;C", "lovačka puška;X")  # kategorija X
        r = self.uvezi("ulaz", los)
        html = r.get_data(as_text=True)
        self.assertIn("Uvoz odbijen", html)
        self.assertIn("kategorija", html)
        conn = self.conn()
        self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM ulaz").fetchone()["n"], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM dobavljaci").fetchone()["n"], 0)
        conn.close()

    def test_uvoz_duplikat_rednog_broja_odbijen(self):
        self.uvezi("ulaz", CSV_ULAZ)
        r = self.uvezi("ulaz", CSV_ULAZ)
        self.assertIn("već postoji u knjizi", r.get_data(as_text=True))

    def test_predlozak_i_prodavac_nema_pristup(self):
        r = self.client.get("/migracija/predlozak/ulaz.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("tvornicki_broj", r.get_data(as_text=True))
        from werkzeug.security import generate_password_hash
        conn = self.conn()
        conn.execute(
            "INSERT INTO korisnici (korisnicko_ime, lozinka_hash, ime_prezime, rola) "
            "VALUES ('pero', ?, 'Pero', 'prodavac')",
            (generate_password_hash("tajna12345"),))
        conn.close()
        self.client.post("/login", data={"korisnicko_ime": "pero", "lozinka": "tajna12345"})
        self.assertEqual(self.client.get("/migracija").status_code, 403)
        self.assertEqual(self.client.get("/izvoz/ulaz.csv").status_code, 403)

    # ---------------- izvoz, ispis, zalihe ----------------

    def test_izvoz_csv(self):
        self.uvezi("ulaz", CSV_ULAZ)
        r = self.client.get("/izvoz/ulaz.csv")
        self.assertEqual(r.status_code, 200)
        tekst = r.get_data(as_text=True)
        self.assertIn("C557-001", tekst)
        self.assertIn("lovački karabin", tekst)
        self.assertTrue(tekst.startswith("﻿"))

    def test_pdf_ispis_cijele_knjige_i_raspona(self):
        self.uvezi("ulaz", CSV_ULAZ)
        r = self.client.get("/ispis/ulaz.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/pdf")
        self.assertTrue(r.data.startswith(b"%PDF"))
        cijeli = len(r.data)
        r = self.client.get("/ispis/ulaz.pdf?rb_od=730&rb_do=730")
        self.assertEqual(r.status_code, 200)
        self.assertLess(len(r.data), cijeli + 1)  # raspon nije veći od cijele knjige

    def test_pdf_ispis_prodaje_i_streljiva(self):
        self.uvezi("ulaz", CSV_ULAZ)
        self.uvezi("prodaja", CSV_PRODAJA)
        self.uvezi("streljivo", CSV_STRELJIVO)
        for knjiga in ("prodaja", "streljivo"):
            r = self.client.get(f"/ispis/{knjiga}.pdf")
            self.assertEqual(r.status_code, 200, knjiga)
            self.assertTrue(r.data.startswith(b"%PDF"), knjiga)

    def test_zalihe(self):
        self.uvezi("ulaz", CSV_ULAZ)
        self.uvezi("prodaja", CSV_PRODAJA)  # proda C557-001
        html = self.client.get("/zalihe").get_data(as_text=True)
        self.assertIn("Beretta 686", html)
        self.assertNotIn("CZ 557", html)  # prodano — nije na zalihi
        self.assertIn("Ukupno na stanju: <strong>1</strong>", html)


if __name__ == "__main__":
    unittest.main()

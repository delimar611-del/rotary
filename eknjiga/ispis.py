"""eKnjiga Oružja — PDF ispis po propisanim obrascima (NN tiskanice).

Generira A4 landscape PDF s izgledom papirnate knjige: zaglavlje s oznakom
obrasca, naslov knjige, tablica s nazivima kolona i numeracijom 1.-10.,
podaci trgovine. DejaVu fontovi (u static/fonts/) jamče ispravnu hrvatsku
dijakritiku na svim platformama.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fpdf import FPDF

FONT_DIR = Path(__file__).resolve().parent / "static" / "fonts"

# (naziv kolone, širina u mm) — ukupno ≤ 277 mm (A4 landscape, margine 10 mm)
ULAZ_KOLONE = [
    ("Redni\nbroj", 13),
    ("Datum\nnabave", 21),
    ("Broj oružnog lista\nnabavljenog oružja", 25),
    ("Naziv i broj isprave na temelju\nkoje je oružje nabavljeno", 34),
    ("Od koga je oružje nabavljeno\n(ime i prezime / naziv, adresa, OIB)", 44),
    ("Vrsta i\nkategorija", 29),
    ("Marka i model", 30),
    ("Kalibar", 19),
    ("Tvornički broj", 26),
    ("Napomena", 36),
]

PRODAJA_KOLONE = [
    ("Redni\nbroj", 13),
    ("Datum\nprodaje", 21),
    ("Podaci o kupcu\n(naziv/ime, adresa, OIB)", 42),
    ("Vrsta i\nkategorija", 27),
    ("Marka i model", 28),
    ("Kalibar", 18),
    ("Tvornički broj", 24),
    ("Broj i datum odobrenja za promet\nili nabavu oružja", 62),
    ("Napomena", 42),
]

STRELJIVO_KOLONE = [
    ("Redni\nbroj", 13),
    ("Datum\nprodaje", 21),
    ("Podaci o kupcu\n(naziv/ime, adresa, OIB)", 42),
    ("Vrsta", 26),
    ("Marka", 22),
    ("Kalibar", 18),
    ("Broj lota /\nbroj pakiranja", 24),
    ("Količina", 16),
    ("Broj i datum odobrenja za promet\nili broj oružnog lista", 58),
    ("Napomena", 37),
]

NASLOVI = {
    "ulaz": ("KNJIGA NABAVLJENOG ORUŽJA", "Prilog II. — Obrazac 1", ULAZ_KOLONE),
    "prodaja": ("KNJIGA PRODANOG ORUŽJA", "Obrazac 2", PRODAJA_KOLONE),
    "streljivo": ("EVIDENCIJA O PRODANOM STRELJIVU", "Prilog VIII. — Obrazac 3", STRELJIVO_KOLONE),
}


def hr_datum(iso: str | None) -> str:
    """ISO datum → hrvatski format DD.MM.GGGG."""
    if not iso:
        return ""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y.")
    except ValueError:
        return iso


class ObrazacPDF(FPDF):
    """A4 landscape obrazac sa zaglavljem knjige i numeracijom stranica."""

    def __init__(self, naslov: str, oznaka: str, trgovina: dict, podnaslov: str):
        super().__init__(orientation="L", format="A4")
        self.naslov = naslov
        self.oznaka = oznaka
        self.trgovina = trgovina
        self.podnaslov = podnaslov
        self.add_font("DejaVu", "", FONT_DIR / "DejaVuSans.ttf")
        self.add_font("DejaVu", "B", FONT_DIR / "DejaVuSans-Bold.ttf")
        self.set_margins(10, 10, 10)
        self.set_auto_page_break(True, margin=14)

    def header(self):
        self.set_font("DejaVu", "", 8)
        lijevo = " · ".join(x for x in (
            self.trgovina.get("naziv"), self.trgovina.get("adresa"),
            f"OIB: {self.trgovina['oib']}" if self.trgovina.get("oib") else None) if x)
        self.cell(0, 4, lijevo, align="L")
        self.cell(0, 4, self.oznaka, align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(1)
        self.set_font("DejaVu", "B", 13)
        self.cell(0, 7, self.naslov, align="C", new_x="LMARGIN", new_y="NEXT")
        if self.podnaslov:
            self.set_font("DejaVu", "", 8)
            self.cell(0, 4, self.podnaslov, align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-11)
        self.set_font("DejaVu", "", 7)
        self.cell(0, 4, f"Stranica {self.page_no()}/{{nb}}", align="C")


def ispis_pdf(knjiga: str, retci: list[list[str]], trgovina: dict,
              podnaslov: str = "") -> bytes:
    """Generiraj PDF knjige. `retci` su već formatirani stringovi po kolonama."""
    naslov, oznaka, kolone = NASLOVI[knjiga]
    pdf = ObrazacPDF(naslov, oznaka, trgovina, podnaslov)
    pdf.add_page()
    pdf.set_font("DejaVu", "", 7.5)

    with pdf.table(
        col_widths=tuple(w for _, w in kolone),
        text_align="LEFT",
        line_height=3.6,
        padding=1.2,
        num_heading_rows=2,
        repeat_headings=1,
    ) as table:
        # 1. red zaglavlja: nazivi kolona
        pdf.set_font("DejaVu", "B", 7)
        red = table.row()
        for naziv, _ in kolone:
            red.cell(naziv, align="C")
        # 2. red zaglavlja: numeracija kolona 1.-N.
        red = table.row()
        for i in range(len(kolone)):
            red.cell(f"{i + 1}.", align="C")
        # podaci
        pdf.set_font("DejaVu", "", 7.5)
        for zapis in retci:
            red = table.row()
            for vrijednost in zapis:
                red.cell(str(vrijednost))

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Formatiranje redaka iz zapisa baze (spajanje strukturiranih polja za ispis)
# ---------------------------------------------------------------------------

def _partner(naziv, adresa, oib) -> str:
    dijelovi = [naziv]
    if adresa:
        dijelovi.append(adresa)
    if oib:
        dijelovi.append(f"OIB: {oib}")
    return "\n".join(dijelovi)


def _napomena(z) -> str:
    n = z["napomena"] or ""
    if z["status"] == "storno":
        n = (n + "\n" if n else "") + f"STORNIRANO: {z['storno_razlog']}"
    return n


def redak_ulaz(z) -> list[str]:
    return [
        z["redni_broj"], hr_datum(z["datum_nabave"]), z["broj_oruznog_lista"] or "",
        z["isprava"],
        _partner(z["dobavljac_naziv"], z["dobavljac_adresa"], z["dobavljac_oib"]),
        f"{z['vrsta']}, kat. {z['kategorija']}", z["marka_model"], z["kalibar"],
        z["tvornicki_broj"], _napomena(z),
    ]


ODOBRENJE_NAZIVI = {
    "odobrenje_za_nabavu": "odobrenje za nabavu",
    "oruzni_list": "oružni list",
    "odobrenje_za_promet": "odobrenje za promet",
}


def _odobrenje(z, s_oruzjem: bool = False) -> str:
    tekst = f"{ODOBRENJE_NAZIVI[z['odobrenje_vrsta']]} br. {z['odobrenje_broj']}"
    if z["odobrenje_datum"]:
        tekst += f" od {hr_datum(z['odobrenje_datum'])}"
    if z["odobrenje_izdavatelj"]:
        tekst += f", {z['odobrenje_izdavatelj']}"
    if s_oruzjem and z["oruzje_broj"]:
        tekst += f";\noružje tvor. br. {z['oruzje_broj']}"
    return tekst


def redak_prodaja(z) -> list[str]:
    return [
        z["redni_broj"], hr_datum(z["datum_prodaje"]),
        _partner(z["kupac_naziv"], z["kupac_adresa"], z["kupac_oib"]),
        f"{z['vrsta']}, kat. {z['kategorija']}", z["marka_model"], z["kalibar"],
        z["tvornicki_broj"], _odobrenje(z), _napomena(z),
    ]


def redak_streljivo(z) -> list[str]:
    return [
        z["redni_broj"], hr_datum(z["datum_prodaje"]),
        _partner(z["kupac_naziv"], z["kupac_adresa"], z["kupac_oib"]),
        z["vrsta"], z["marka"], z["kalibar"], z["lot_broj"], z["kolicina"],
        _odobrenje(z, s_oruzjem=True), _napomena(z),
    ]


REDAK = {"ulaz": redak_ulaz, "prodaja": redak_prodaja, "streljivo": redak_streljivo}

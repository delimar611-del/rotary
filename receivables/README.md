# DETONEX d.o.o. — dashboard za naplatu potraživanja

Lokalna aplikacija (Python + Flask + SQLite) koja tjedne izvoze iz
eposlovanje-naplata pipelinea pretvara u trajni pregled: aging razredi,
stanje po partneru i skice podsjetnika na hrvatskom koje eskaliraju s
kašnjenjem. **Aplikacija ništa ne šalje i ništa ne scrapea** — samo uvozi
`matched.json` koji sami generirate.

## Instalacija

```bash
pip install flask
python -m receivables init-config   # stvori config.json — upišite IBAN, banku, kontakt
```

## Tjedni tok rada

1. Ručno se prijavite na ePoslovanje i pokrenite svoje skripte → `matched.json`
   (i po želji JSON s nespojenim uplatama iz `match_payments.py`).
2. `python -m receivables ingest matched.json --unmatched unmatched.json`
3. `python -m receivables serve` → http://127.0.0.1:8077
4. Na kartici **Podsjetnici** skicirajte e-mailove; skice se spremaju u
   `outbox/*.txt`, kopirate ih u svoj mail program i tek onda u aplikaciji
   kliknete „Označi kao poslan".

Brzi tekstualni pregled bez browsera: `python -m receivables report --detail`

## Proba bez pravih podataka

```bash
python -m receivables ingest receivables/fixtures/matched_sample.json \
    --unmatched receivables/fixtures/unmatched_sample.json
python -m receivables serve
```

(Fixture je izmišljen; datumi su birani oko srpnja 2026., pa će s vremenom
svi „ostarjeti" u 90+ razred — za probu je to svejedno.)

## Što se gdje pamti

- `data/receivables.db` — SQLite: svi uvozi (povijest), računi, partneri,
  nespojene uplate, podsjetnici. Radite backup ove datoteke.
- `config.json` — podaci tvrtke (IBAN itd.), rokovi, razredi. Nije u gitu.
- `outbox/` — skice podsjetnika u .txt obliku. Nije u gitu.

## Imenik partnera

Puni se automatski iz kupaca pri prvom uvozu (OIB → naziv). E-mail, kontakt
osobu, rok plaćanja (zadano 30 dana) i bilješke upisujete na kartici
**Partneri** → klik na partnera.

## Razine podsjetnika

| Razina | Kada (najstarije kašnjenje) | Ton |
|---|---|---|
| 1. Podsjetnik | do 15 dana | ljubazan |
| 2. Požurnica | 16–45 dana | izravan, rok 8 dana |
| 3. Opomena | 45+ dana | formalan, najava zateznih kamata i prisilne naplate |

Pragove mijenjate u `config.json` (`reminder_level_bounds`), razred aginga u
`aging_bucket_bounds`, a predloženu razinu možete ručno pregaziti pri skiciranju.

## Kontrola („novac se ne smije skrivati")

Kartica **Kontrola** uvijek prikazuje storno račune (`N/A (storno)`) i
nespojene uplate. Nespojenu uplatu možete „odbaciti" (npr. kad ste je ručno
razjasnili); ako se u novijem uvozu više ne pojavi, sama prelazi u
„razriješeno".

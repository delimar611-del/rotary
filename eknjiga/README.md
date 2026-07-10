# eKnjiga Oružja

Web aplikacija koja digitalno zamjenjuje tri propisane papirnate knjige
evidencija hrvatskih trgovaca oružjem:

1. **Knjiga nabavljenog oružja** (Prilog II, Obrazac 1) — tablica `ulaz`
2. **Knjiga prodanog oružja** (Obrazac 2) — tablica `prodaja`
3. **Evidencija streljiva** (Prilog VIII, Obrazac 3) — tablica `streljivo`

## Stack

- **Python 3 + SQLite** (standardna biblioteka — sloj baze nema vanjskih
  ovisnosti), web sloj: Flask (korak 2+)
- Radi potpuno **lokalno/offline**, jedna instanca po trgovini
- **Backup jednim klikom** = konzistentna kopija jedne `.db` datoteke
  (`db.backup()`, koristi SQLite online-backup API)
- Hrvatski UI, UTF-8 svugdje

## Pokretanje

```bash
cd eknjiga
pip3 install -r requirements.txt   # samo Flask
python3 app.py                     # → http://127.0.0.1:5000
```

Prvo pokretanje otvara stranicu za kreiranje računa **vlasnika**.

## Korak 1 — shema baze

### Datoteke

| Datoteka | Sadržaj |
|---|---|
| `schema.sql` | Kompletna shema: 3 evidencije, šifrarnici, audit log, triggeri |
| `db.py` | Pristup bazi: init, postavke, sljedeći redni broj, audit, backup |
| `test_schema.py` | 11 testova pravila sheme (`python3 -m unittest test_schema`) |

### Ključne odluke u shemi

- **Kolone 1:1 s obrascima.** Polja evidencija prate točno propisane kolone;
  hrvatski nazivi i u bazi. Ono što je na obrascu jedna kolona, a traži se
  strukturirano, razdvojeno je i spaja se tek u ispisu:
  - *Vrsta i kategorija* → `vrsta` + `kategorija` (A/B/C)
  - *Broj i datum odobrenja* (prodaja, kolona 8; streljivo, kolona 9) →
    `odobrenje_vrsta` (odobrenje za nabavu / oružni list / odobrenje za
    promet) + `odobrenje_broj` + `odobrenje_datum` + `odobrenje_izdavatelj` (PU/PP)
- **Brisanja nema.** `DELETE` na evidencijama je blokiran triggerima na razini
  baze. Ispravak = **storno**: `status='storno'` + CHECK koji zahtijeva
  neprazno `storno_razlog` (obrazloženje je obavezno i tehnički).
- **Audit log je append-only** i na razini baze: triggeri blokiraju
  UPDATE/DELETE na `audit_log`. Zapis: tko (id + snapshot imena), kada, koja
  tablica/zapis, akcija (unos/izmjena/storno), staro→novo kao JSON.
- **Tvornički broj**: običan indeks, *ne* UNIQUE constraint — aplikacija
  upozorava na duplikat, ali dopušta override uz napomenu (točno po specu).
- **Redni brojevi**: `UNIQUE`, kontinuirani kroz godine
  (`MAX(redni_broj)+1`), s konfigurabilnim početkom po knjizi u tablici
  `postavke` (npr. `redni_broj.start.ulaz = 731` — nastavak papirnate knjige).
- **Križno povezivanje (killer feature #1)**: `prodaja.ulaz_id` → `ulaz.id`,
  uz parcijalni UNIQUE indeks koji dopušta **najviše jednu aktivnu
  (ne-storniranu) prodaju po ulazu** — storno prodaje automatski oslobađa
  komad za ponovnu prodaju. Podaci o oružju u prodaji su snapshot (obrazac ih
  traži u toj knjizi), status komada živi na ulazu:
  `na_stanju → prodano → (storno)`. "Na stanju" = ulazi bez vezane aktivne
  prodaje.
- **Legacy referenca** (`legacy_knjiga`, `legacy_stranica`,
  `legacy_redni_broj`) na svim trima evidencijama — popunjava se pri
  CSV/Excel migraciji postojećih papirnatih knjiga.
- **Šifrarnici** `kupci` i `dobavljaci`: samo polja koja traže obrasci
  (naziv/ime, adresa, OIB) — GDPR minimizacija. OIB validiran na 11 znamenki,
  nullable (npr. strani dobavljač), unique kad postoji. Zapisi se
  deaktiviraju (`aktivan=0`), ne brišu.
- **Role**: `korisnici.rola` ∈ {`vlasnik`, `prodavac`} — provedba prava
  (bez izmjena starih zapisa, bez izvoza za prodavača) ide u aplikacijskom
  sloju u koracima 2-3.
- Aplikacija **ništa ne šalje nikome automatski** — nema vanjskih poziva.

### Pokretanje testova

```bash
cd eknjiga
python3 -m unittest test_schema -v
```

## Korak 2 — ulazna knjiga (web aplikacija)

- **Flask aplikacija** (`app.py` + `templates/` + `static/`), hrvatski UI.
  Prvo pokretanje: kreiranje računa vlasnika; prijava sesijom.
- **Pojedinačni unos** (`/ulaz/novi`): sva polja obrasca 1.-10., redni broj
  se dodjeljuje automatski unutar `BEGIN IMMEDIATE` transakcije.
- **Duplikat tvorničkog broja**: upozorenje s popisom postojećih zapisa;
  unos prolazi tek uz izričitu potvrdu + obaveznu napomenu s obrazloženjem.
- **Bulk unos** (`/ulaz/bulk`): jedan set podataka + textarea s tvorničkim
  brojevima (redak ili zarez) → N redaka s uzastopnim rednim brojevima,
  sve u jednoj transakciji. Ponovljeni broj unutar liste se odbija.
- **Šifrarnik dobavljača**: autocomplete (naziv/OIB) u formi unosa + inline
  kreiranje novog dobavljača; isti OIB se ne duplicira nego veže postojećeg.
- **Pretraga**: jedan upit preko tvorničkog broja, marke, vrste, kalibra,
  dobavljača i isprave + raspon datuma + filtar statusa (indeksi iz koraka 1).
- **Role**: prodavač unosi i pretražuje; izmjena starih zapisa, storno i
  postavke samo vlasnik (HTTP 403). Svaka izmjena piše staro→novo u audit.
- **Storno** iz detalja zapisa (obavezno obrazloženje); dnevnik izmjena
  vidljiv na stranici svakog zapisa.
- **Postavke** (vlasnik): podaci trgovine za ispis + početni redni brojevi.
- Testovi: `python3 -m unittest test_app` (10 testova kroz HTTP sloj).

## Plan (svaki korak se potvrđuje)

1. ✅ Shema baze + šifrarnici + audit log
2. ✅ Ulazna knjiga: pojedinačni + bulk unos, pretraga
3. ⬜ Prodajna knjiga s križnim povezivanjem i statusima
4. ⬜ Streljivo
5. ⬜ PDF ispisi identični obrascima + CSV import za migraciju

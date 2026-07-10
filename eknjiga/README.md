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

## Korak 3 — prodajna knjiga s križnim povezivanjem

- **Prodaja se kreira iz ulaza** (killer feature #1): gumb „Prodaj” na retku
  ulazne knjige / detalju ulaza, ili unos tvorničkog broja na `/prodaja/nova`
  koji pronalazi komad na stanju (više kandidata → stranica izbora).
  Podaci o oružju se predispune iz ulaza (snapshot u prodajnoj knjizi,
  kako obrazac 2 traži), a `ulaz_id` čuva živu vezu.
- **Statusi**: prodaja atomarno (u istoj transakciji) prebacuje ulaz
  na_stanju → prodano; ponovna prodaja istog komada je odbijena i kod
  istovremenih zahtjeva (re-check unutar `BEGIN IMMEDIATE` + parcijalni
  unique indeks iz koraka 1). Pregled „na stanju” = filtar statusa u
  ulaznoj knjizi.
- **Napomena (kolona 9) auto-generirana**: `ul. r.br. {R}` za digitalne
  ulaze, odnosno `ul. knjiga {N}, str. {S}, r.br. {R}` kad ulaz nosi legacy
  referencu papirnate knjige; dodatni tekst korisnika se nadovezuje.
- **Kolona 8 strukturirana**: vrsta isprave (odobrenje za nabavu / oružni
  list / odobrenje za promet) + broj + datum + izdavatelj (PU/PP); u
  prikazu/ispisu se spaja u jedan string.
- **Storno prodaje vraća komad na stanje** (uz obavezno obrazloženje;
  oboje u audit logu) — komad se potom može ponovno prodati.
- **Šifrarnik kupaca**: autocomplete + inline unos novog kupca u formi
  prodaje (zajednička komponenta s dobavljačima), stranica `/kupci`.
- Pretraga prodajne knjige: kupac, tvornički broj, marka, vrsta, kalibar,
  broj odobrenja + raspon datuma.
- Testovi: `python3 -m unittest test_prodaja` (12 testova).

## Korak 4 — evidencija streljiva

- **Prodaja civilu na oružni list**: obavezno ime i prezime/naziv, adresa i
  OIB kupca (provjera prije upisa — nepotpun kupac iz šifrarnika se odbija
  s jasnom porukom), broj oružnog lista, policijska uprava/postaja koja ga
  je izdala i **tvornički broj oružja upisanog u oružni list** (novo polje
  `oruzje_broj`; automatska migracija starih baza ALTER-om). Kod prodaje
  trgovcu na odobrenje za promet broj oružja se ne traži (polje se skriva).
- Kolone obrasca 1.-10.: vrsta, marka (proizvođač), kalibar, broj lota /
  pakiranja, količina; kolona 9 se u prikazu spaja u jedan string
  („oružni list br. X od D, PP Y; oružje tvor. br. Z”).
- **Bulk po lotovima** (killer feature #2): jedan kupac + isprava, textarea
  redaka „broj lota; količina” → N zapisa s uzastopnim rednim brojevima u
  jednoj transakciji; neispravan redak odbija cijeli unos.
- Pretraga: kupac, marka, vrsta, kalibar, lot, broj isprave, broj oružja +
  raspon datuma. Storno uz obrazloženje, audit, role — kao ostale knjige.
- Testovi: `python3 -m unittest test_streljivo` (10 testova).

## Korak 5 — PDF ispisi, migracija (CSV uvoz), izvoz, zalihe

- **PDF ispis po obrascu** (`/ispis/<knjiga>.pdf`, gumb „Ispis PDF” na svakoj
  knjizi): A4 vodoravno, oznaka obrasca (Prilog II. — Obrazac 1 / Obrazac 2 /
  Prilog VIII. — Obrazac 3), naslov knjige, podaci trgovine iz postavki,
  tablica s nazivima kolona i numeracijom 1.-10., ponavljanje zaglavlja na
  svakoj stranici, numeracija stranica. DejaVu fontovi su priloženi u
  `static/fonts/` pa dijakritika radi na svakoj platformi. Raspon ispisa:
  od-do rednog broja i/ili datuma. Strukturirana polja spajaju se u jedan
  string točno kao u papirnatoj knjizi; stornirani zapisi nose oznaku
  „STORNIRANO: razlog” u napomeni.
- **Migracija** (`/migracija`, vlasnik): CSV predložak po knjizi + uvoz.
  Sve-ili-ništa uz popis grešaka po recima; redni brojevi iz papirnate
  knjige ili automatski; dobavljači/kupci se spajaju po OIB-u pa po nazivu;
  uvoz prodaje automatski povezuje ulaze po tvorničkom broju i označava ih
  prodanima; `legacy_*` kolone čuvaju referencu na papirnatu knjigu.
- **CSV izvoz** (vlasnik): sve tri knjige, UTF-8 s BOM-om (izravno u Excel),
  `;` separator.
- **Zalihe** (`/zalihe`): komadi na stanju grupirani po kategoriji, marki i
  kalibru + ukupni brojevi po kategoriji, s linkom na pojedinačne komade.
- Testovi: `python3 -m unittest test_ispis_uvoz` (10 testova).

## Plan (svaki korak se potvrđuje)

1. ✅ Shema baze + šifrarnici + audit log
2. ✅ Ulazna knjiga: pojedinačni + bulk unos, pretraga
3. ✅ Prodajna knjiga s križnim povezivanjem i statusima
4. ✅ Streljivo
5. ✅ PDF ispisi identični obrascima + CSV import za migraciju

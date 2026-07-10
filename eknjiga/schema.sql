-- ============================================================================
-- eKnjiga Oružja — shema baze (SQLite)
-- Korak 1: tri evidencije + šifrarnici kupaca/dobavljača + audit log
--
-- Načela:
--  * Brisanja NEMA — DELETE je blokiran triggerima na svim evidencijama.
--    Ispravak = storno uz obavezno obrazloženje (status 'storno').
--  * Audit log je append-only — UPDATE/DELETE na audit_log blokirani triggerima.
--  * Kolone evidencija prate 1:1 propisane obrasce (NN tiskanice); polja koja
--    obrazac prikazuje kao jednu kolonu, a spec traži strukturirano
--    (vrsta+kategorija, odobrenje), razdvojena su i spajaju se tek u ispisu.
--  * Tvornički broj NIJE UNIQUE constraint: aplikacija upozorava na duplikat,
--    ali dopušta override uz napomenu (zato samo običan indeks).
--  * Redni brojevi su kontinuirani kroz godine; početni broj po knjizi
--    (npr. 731 — nastavak papirnate knjige) drži se u tablici postavke.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Korisnici i role
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS korisnici (
    id              INTEGER PRIMARY KEY,
    korisnicko_ime  TEXT    NOT NULL UNIQUE,
    lozinka_hash    TEXT    NOT NULL,
    ime_prezime     TEXT    NOT NULL,
    rola            TEXT    NOT NULL CHECK (rola IN ('vlasnik', 'prodavac')),
    aktivan         INTEGER NOT NULL DEFAULT 1 CHECK (aktivan IN (0, 1)),
    kreirano        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ---------------------------------------------------------------------------
-- Postavke (ključ/vrijednost): podaci trgovine za zaglavlje ispisa,
-- početni redni brojevi po knjizi (nastavak papirnatih knjiga), itd.
-- Ključevi koje aplikacija koristi:
--   trgovina.naziv, trgovina.adresa, trgovina.oib
--   redni_broj.start.ulaz / .prodaja / .streljivo  (default 1)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS postavke (
    kljuc      TEXT PRIMARY KEY,
    vrijednost TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Šifrarnik dobavljača (autocomplete kod unosa nabave)
-- GDPR minimizacija: samo polja koja traži obrazac (naziv/ime, adresa, OIB).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dobavljaci (
    id       INTEGER PRIMARY KEY,
    naziv    TEXT NOT NULL,              -- ime i prezime / naziv tvrtke
    adresa   TEXT NOT NULL DEFAULT '',
    oib      TEXT,                       -- 11 znamenki; nullable (strani dobavljač)
    aktivan  INTEGER NOT NULL DEFAULT 1 CHECK (aktivan IN (0, 1)),
    kreirano TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (oib IS NULL OR (length(oib) = 11 AND oib GLOB '[0-9]*'))
);
CREATE INDEX IF NOT EXISTS idx_dobavljaci_naziv ON dobavljaci (naziv);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dobavljaci_oib
    ON dobavljaci (oib) WHERE oib IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Šifrarnik kupaca (zajednički za prodaju oružja i streljiva)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kupci (
    id       INTEGER PRIMARY KEY,
    naziv    TEXT NOT NULL,              -- ime i prezime / naziv tvrtke
    adresa   TEXT NOT NULL DEFAULT '',
    oib      TEXT,
    aktivan  INTEGER NOT NULL DEFAULT 1 CHECK (aktivan IN (0, 1)),
    kreirano TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (oib IS NULL OR (length(oib) = 11 AND oib GLOB '[0-9]*'))
);
CREATE INDEX IF NOT EXISTS idx_kupci_naziv ON kupci (naziv);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kupci_oib
    ON kupci (oib) WHERE oib IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 1. KNJIGA NABAVLJENOG ORUŽJA (Prilog II, Obrazac 1)
-- Kolone obrasca 1.-10. → polja označena brojem u komentaru.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ulaz (
    id                 INTEGER PRIMARY KEY,
    redni_broj         INTEGER NOT NULL UNIQUE,              -- 1. Redni broj (kontinuiran kroz godine)
    datum_nabave       TEXT    NOT NULL,                     -- 2. Datum nabave (ISO 8601: YYYY-MM-DD)
    broj_oruznog_lista TEXT,                                 -- 3. Broj oružnog lista (nullable — novo oružje ga nema)
    isprava            TEXT    NOT NULL,                     -- 4. Naziv i broj isprave na temelju koje je oružje nabavljeno
    dobavljac_id       INTEGER NOT NULL REFERENCES dobavljaci (id), -- 5. Od koga je oružje nabavljeno
    vrsta              TEXT    NOT NULL,                     -- 6a. Vrsta (npr. "lovački karabin")
    kategorija         TEXT    NOT NULL CHECK (kategorija IN ('A', 'B', 'C')), -- 6b. Kategorija
    marka_model        TEXT    NOT NULL,                     -- 7. Marka i model
    kalibar            TEXT    NOT NULL,                     -- 8. Kalibar
    tvornicki_broj     TEXT    NOT NULL,                     -- 9. Tvornički broj (duplikat = upozorenje, ne zabrana)
    napomena           TEXT    NOT NULL DEFAULT '',          -- 10. Napomena (ovdje ide i obrazloženje override-a duplikata)

    -- Status komada: na_stanju → prodano; storno = zapis poništen uz obrazloženje
    status             TEXT    NOT NULL DEFAULT 'na_stanju'
                       CHECK (status IN ('na_stanju', 'prodano', 'storno')),

    -- Legacy referenca na papirnatu knjigu (popunjava se pri migraciji)
    legacy_knjiga      TEXT,
    legacy_stranica    TEXT,
    legacy_redni_broj  TEXT,

    -- Storno (brisanja nema)
    storno_razlog      TEXT,
    storno_korisnik_id INTEGER REFERENCES korisnici (id),
    storno_vrijeme     TEXT,

    -- Meta
    kreirao_id         INTEGER NOT NULL REFERENCES korisnici (id),
    kreirano           TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    izmijenio_id       INTEGER REFERENCES korisnici (id),
    izmijenjeno        TEXT,

    CHECK (status != 'storno' OR (storno_razlog IS NOT NULL AND storno_razlog != ''))
);
CREATE INDEX IF NOT EXISTS idx_ulaz_tvornicki_broj ON ulaz (tvornicki_broj);
CREATE INDEX IF NOT EXISTS idx_ulaz_datum          ON ulaz (datum_nabave);
CREATE INDEX IF NOT EXISTS idx_ulaz_dobavljac      ON ulaz (dobavljac_id);
CREATE INDEX IF NOT EXISTS idx_ulaz_status         ON ulaz (status);
CREATE INDEX IF NOT EXISTS idx_ulaz_marka_model    ON ulaz (marka_model);

-- ---------------------------------------------------------------------------
-- 2. KNJIGA PRODANOG ORUŽJA (Obrazac 2)
-- Podaci o oružju (kolone 4.-7.) su SNAPSHOT u trenutku prodaje (obrazac ih
-- traži u ovoj knjizi), a ulaz_id je živa veza na ulaznu knjigu.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prodaja (
    id                   INTEGER PRIMARY KEY,
    redni_broj           INTEGER NOT NULL UNIQUE,            -- 1. Redni broj
    datum_prodaje        TEXT    NOT NULL,                   -- 2. Datum prodaje
    kupac_id             INTEGER NOT NULL REFERENCES kupci (id), -- 3. Podaci o kupcu
    vrsta                TEXT    NOT NULL,                   -- 4a. Vrsta
    kategorija           TEXT    NOT NULL CHECK (kategorija IN ('A', 'B', 'C')), -- 4b. Kategorija
    marka_model          TEXT    NOT NULL,                   -- 5. Marka i model
    kalibar              TEXT    NOT NULL,                   -- 6. Kalibar
    tvornicki_broj       TEXT    NOT NULL,                   -- 7. Tvornički broj

    -- 8. Broj i datum odobrenja — strukturirano; u ispisu se spaja u jedan string
    odobrenje_vrsta      TEXT    NOT NULL CHECK (odobrenje_vrsta IN
                             ('odobrenje_za_nabavu', 'oruzni_list', 'odobrenje_za_promet')),
    odobrenje_broj       TEXT    NOT NULL,
    odobrenje_datum      TEXT    NOT NULL,
    odobrenje_izdavatelj TEXT    NOT NULL,                   -- PU/PP

    napomena             TEXT    NOT NULL DEFAULT '',        -- 9. Napomena — auto: "ul. knjiga {N}, str. {S}, r.br. {R}"

    -- Križna veza na ulaz (killer feature #1); NULL samo za migrirane zapise
    -- čiji ulaz nije u digitalnoj knjizi. UNIQUE: jedan ulaz = najviše jedna
    -- aktivna prodaja (storno prodaje oslobađa ulaz — vidi aplikacijsku logiku).
    ulaz_id              INTEGER REFERENCES ulaz (id),

    status               TEXT    NOT NULL DEFAULT 'aktivno'
                         CHECK (status IN ('aktivno', 'storno')),

    -- Legacy referenca na papirnatu knjigu (migracija)
    legacy_knjiga        TEXT,
    legacy_stranica      TEXT,
    legacy_redni_broj    TEXT,

    -- Storno
    storno_razlog        TEXT,
    storno_korisnik_id   INTEGER REFERENCES korisnici (id),
    storno_vrijeme       TEXT,

    -- Meta
    kreirao_id           INTEGER NOT NULL REFERENCES korisnici (id),
    kreirano             TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    izmijenio_id         INTEGER REFERENCES korisnici (id),
    izmijenjeno          TEXT,

    CHECK (status != 'storno' OR (storno_razlog IS NOT NULL AND storno_razlog != ''))
);
-- Jedan ulaz smije imati samo jednu NE-storniranu prodaju:
CREATE UNIQUE INDEX IF NOT EXISTS idx_prodaja_ulaz_aktivna
    ON prodaja (ulaz_id) WHERE ulaz_id IS NOT NULL AND status = 'aktivno';
CREATE INDEX IF NOT EXISTS idx_prodaja_tvornicki_broj ON prodaja (tvornicki_broj);
CREATE INDEX IF NOT EXISTS idx_prodaja_datum          ON prodaja (datum_prodaje);
CREATE INDEX IF NOT EXISTS idx_prodaja_kupac          ON prodaja (kupac_id);
CREATE INDEX IF NOT EXISTS idx_prodaja_marka_model    ON prodaja (marka_model);

-- ---------------------------------------------------------------------------
-- 3. EVIDENCIJA STRELJIVA (Prilog VIII, Obrazac 3)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS streljivo (
    id                   INTEGER PRIMARY KEY,
    redni_broj           INTEGER NOT NULL UNIQUE,            -- 1. Redni broj
    datum_prodaje        TEXT    NOT NULL,                   -- 2. Datum prodaje
    kupac_id             INTEGER NOT NULL REFERENCES kupci (id), -- 3. Podaci o kupcu
    vrsta                TEXT    NOT NULL,                   -- 4. Vrsta
    marka                TEXT    NOT NULL,                   -- 5. Marka
    kalibar              TEXT    NOT NULL,                   -- 6. Kalibar
    lot_broj             TEXT    NOT NULL,                   -- 7. Broj lota / broj pakiranja
    kolicina             INTEGER NOT NULL CHECK (kolicina > 0), -- 8. Količina

    -- 9. Broj i datum odobrenja za promet ili broj oružnog lista — strukturirano
    odobrenje_vrsta      TEXT    NOT NULL CHECK (odobrenje_vrsta IN
                             ('odobrenje_za_promet', 'oruzni_list')),
    odobrenje_broj       TEXT    NOT NULL,
    odobrenje_datum      TEXT,
    odobrenje_izdavatelj TEXT    NOT NULL DEFAULT '',

    napomena             TEXT    NOT NULL DEFAULT '',        -- 10. Napomena

    status               TEXT    NOT NULL DEFAULT 'aktivno'
                         CHECK (status IN ('aktivno', 'storno')),

    -- Legacy referenca na papirnatu knjigu (migracija)
    legacy_knjiga        TEXT,
    legacy_stranica      TEXT,
    legacy_redni_broj    TEXT,

    -- Storno
    storno_razlog        TEXT,
    storno_korisnik_id   INTEGER REFERENCES korisnici (id),
    storno_vrijeme       TEXT,

    -- Meta
    kreirao_id           INTEGER NOT NULL REFERENCES korisnici (id),
    kreirano             TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    izmijenio_id         INTEGER REFERENCES korisnici (id),
    izmijenjeno          TEXT,

    CHECK (status != 'storno' OR (storno_razlog IS NOT NULL AND storno_razlog != ''))
);
CREATE INDEX IF NOT EXISTS idx_streljivo_datum   ON streljivo (datum_prodaje);
CREATE INDEX IF NOT EXISTS idx_streljivo_kupac   ON streljivo (kupac_id);
CREATE INDEX IF NOT EXISTS idx_streljivo_lot     ON streljivo (lot_broj);
CREATE INDEX IF NOT EXISTS idx_streljivo_kalibar ON streljivo (kalibar);

-- ---------------------------------------------------------------------------
-- AUDIT LOG — append-only
-- Svaka izmjena: tko, kada, staro→novo (JSON snimke retka).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY,
    vrijeme      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    korisnik_id  INTEGER REFERENCES korisnici (id),
    korisnik_ime TEXT    NOT NULL,                 -- snapshot imena (i ako se korisnik kasnije deaktivira)
    tablica      TEXT    NOT NULL,                 -- 'ulaz', 'prodaja', 'streljivo', 'kupci', 'dobavljaci', ...
    zapis_id     INTEGER NOT NULL,
    akcija       TEXT    NOT NULL CHECK (akcija IN ('unos', 'izmjena', 'storno')),
    staro_json   TEXT,                             -- NULL kod unosa
    novo_json    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_zapis ON audit_log (tablica, zapis_id);
CREATE INDEX IF NOT EXISTS idx_audit_vrijeme ON audit_log (vrijeme);

-- Append-only: zabrana izmjene i brisanja audit zapisa na razini baze
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'Audit log je append-only: izmjena nije dopuštena.');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'Audit log je append-only: brisanje nije dopušteno.');
END;

-- ---------------------------------------------------------------------------
-- Zabrana brisanja u evidencijama (brisanja nema — samo storno)
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS ulaz_no_delete
BEFORE DELETE ON ulaz
BEGIN
    SELECT RAISE(ABORT, 'Brisanje nije dopušteno — koristite storno uz obrazloženje.');
END;

CREATE TRIGGER IF NOT EXISTS prodaja_no_delete
BEFORE DELETE ON prodaja
BEGIN
    SELECT RAISE(ABORT, 'Brisanje nije dopušteno — koristite storno uz obrazloženje.');
END;

CREATE TRIGGER IF NOT EXISTS streljivo_no_delete
BEFORE DELETE ON streljivo
BEGIN
    SELECT RAISE(ABORT, 'Brisanje nije dopušteno — koristite storno uz obrazloženje.');
END;

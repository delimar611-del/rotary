"""eKnjiga Oružja — pristup bazi (SQLite).

Sve u standardnoj biblioteci Pythona: nema vanjskih ovisnosti za sloj baze,
radi potpuno lokalno/offline. Backup = kopija jedne .db datoteke.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "eknjiga.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"

# Naziv knjige -> ključ postavke s početnim rednim brojem (nastavak papirnate
# knjige, npr. 731). Ako postavka ne postoji, kreće se od 1.
_START_KEYS = {
    "ulaz": "redni_broj.start.ulaz",
    "prodaja": "redni_broj.start.prodaja",
    "streljivo": "redni_broj.start.streljivo",
}


def get_conn(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Otvori vezu s uključenim foreign keys i row factory-jem.

    isolation_level=None → autocommit; višekorakovne operacije otvaraju
    eksplicitnu transakciju s BEGIN IMMEDIATE (vidi app.py).
    """
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Kreiraj shemu ako ne postoji (idempotentno) i vrati vezu."""
    conn = get_conn(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def get_postavka(conn: sqlite3.Connection, kljuc: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT vrijednost FROM postavke WHERE kljuc = ?", (kljuc,)).fetchone()
    return row["vrijednost"] if row else default


def set_postavka(conn: sqlite3.Connection, kljuc: str, vrijednost: str) -> None:
    conn.execute(
        "INSERT INTO postavke (kljuc, vrijednost) VALUES (?, ?) "
        "ON CONFLICT(kljuc) DO UPDATE SET vrijednost = excluded.vrijednost",
        (kljuc, vrijednost),
    )


def sljedeci_redni_broj(conn: sqlite3.Connection, knjiga: str) -> int:
    """Sljedeći redni broj za knjigu ('ulaz'/'prodaja'/'streljivo').

    Kontinuiran kroz godine: MAX(redni_broj)+1, ali nikad manji od
    konfiguriranog početka (postavka omogućuje nastavak papirnate knjige).
    """
    if knjiga not in _START_KEYS:
        raise ValueError(f"Nepoznata knjiga: {knjiga!r}")
    start = int(get_postavka(conn, _START_KEYS[knjiga], "1") or "1")
    row = conn.execute(f"SELECT MAX(redni_broj) AS m FROM {knjiga}").fetchone()
    return max(start, (row["m"] or 0) + 1)


def audit(
    conn: sqlite3.Connection,
    korisnik_id: int | None,
    korisnik_ime: str,
    tablica: str,
    zapis_id: int,
    akcija: str,
    staro: dict | None,
    novo: dict,
) -> None:
    """Upiši append-only audit zapis (staro→novo kao JSON)."""
    conn.execute(
        "INSERT INTO audit_log (korisnik_id, korisnik_ime, tablica, zapis_id, akcija, staro_json, novo_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            korisnik_id,
            korisnik_ime,
            tablica,
            zapis_id,
            akcija,
            json.dumps(staro, ensure_ascii=False) if staro is not None else None,
            json.dumps(novo, ensure_ascii=False),
        ),
    )


def backup(db_path: Path | str = DB_PATH, backup_dir: Path | str | None = None) -> Path:
    """Backup jednim klikom: konzistentna kopija baze s vremenskim žigom."""
    db_path = Path(db_path)
    backup_dir = Path(backup_dir) if backup_dir else db_path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"eknjiga-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)  # konzistentno i dok je baza u upotrebi
        finally:
            dst.close()
    finally:
        src.close()
    return target

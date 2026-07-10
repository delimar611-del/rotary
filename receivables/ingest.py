import hashlib
import json
from datetime import date, datetime
from pathlib import Path

OPEN_STATUSES = ("UNPAID", "PARTIAL")
STORNO_STATUS = "N/A (storno)"


def parse_hr_date(value):
    """DD.MM.YYYY -> ISO YYYY-MM-DD. Returns None if unparseable."""
    if not value:
        return None
    value = str(value).strip().rstrip(".")
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def to_cents(value):
    if value is None or value == "":
        return 0
    if isinstance(value, str):
        # tolerate Croatian formatting: 1.234,56
        value = value.strip().replace("€", "").replace("EUR", "").strip()
        if "," in value:
            value = value.replace(".", "").replace(",", ".")
        value = float(value)
    return int(round(float(value) * 100))


def cents_to_eur(cents):
    return cents / 100.0


def fmt_eur(cents):
    """Croatian number formatting: 1.234,56 €"""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    whole, frac = divmod(cents, 100)
    whole_str = f"{whole:,}".replace(",", ".")
    return f"{sign}{whole_str},{frac:02d} €"


def _partner_key(oib, buyer):
    oib = (oib or "").strip()
    if oib:
        return oib
    return "NEMA-OIB|" + (buyer or "NEPOZNAT").strip()


def load_matched(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("invoices", "matched", "racuni"):
            if key in data and isinstance(data[key], list):
                return data[key]
        raise ValueError(
            f"{path}: JSON object has no recognizable invoice list "
            "(expected top-level list or an 'invoices'/'matched' key)"
        )
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of invoices")
    return data


def load_unmatched(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("unmatched", "unmatched_payments", "payments", "uplate"):
            if key in data and isinstance(data[key], list):
                return data[key]
        raise ValueError(f"{path}: JSON object has no recognizable payment list")
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of payments")
    return data


def _pick(record, *keys):
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return ""


def ingest(conn, matched_path, unmatched_path=None):
    """Ingest one pipeline run. Returns a summary dict for verification."""
    invoices = load_matched(matched_path)

    problems = []
    rows = []
    for rec in invoices:
        inv = str(rec.get("inv", "")).strip()
        if not inv:
            problems.append(f"preskočen zapis bez broja računa: {rec!r:.120}")
            continue
        issue_date = parse_hr_date(rec.get("date"))
        if issue_date is None:
            problems.append(f"račun {inv}: neispravan datum {rec.get('date')!r}")
            issue_date = "1900-01-01"
        buyer = str(rec.get("buyer", "")).strip() or "NEPOZNAT KUPAC"
        rows.append({
            "inv": inv,
            "buyer": buyer,
            "buyer_oib": _partner_key(rec.get("buyer_oib"), buyer),
            "issue_date": issue_date,
            "amount_cents": to_cents(rec.get("amount")),
            "pay_status": str(rec.get("pay_status", "UNPAID")).strip(),
            "paid_cents": to_cents(rec.get("paid_sum")),
            "payment_refs": json.dumps(rec.get("payment_refs", []), ensure_ascii=False),
        })

    open_rows = [r for r in rows if r["pay_status"] in OPEN_STATUSES]
    outstanding = sum(r["amount_cents"] - r["paid_cents"] for r in open_rows)

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO runs (imported_at, source_file, unmatched_file, invoice_count,"
        " open_count, total_amount_cents, outstanding_cents)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now().isoformat(timespec="seconds"),
            str(matched_path),
            str(unmatched_path) if unmatched_path else None,
            len(rows),
            len(open_rows),
            sum(r["amount_cents"] for r in rows),
            outstanding,
        ),
    )
    run_id = cur.lastrowid

    new_partners = 0
    for r in rows:
        cur.execute("SELECT 1 FROM partners WHERE oib = ?", (r["buyer_oib"],))
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO partners (oib, name) VALUES (?, ?)",
                (r["buyer_oib"], r["buyer"]),
            )
            new_partners += 1

        cur.execute(
            """
            INSERT INTO invoices (inv, buyer, buyer_oib, issue_date, amount_cents,
                                  pay_status, paid_cents, payment_refs,
                                  first_seen_run, last_seen_run)
            VALUES (:inv, :buyer, :buyer_oib, :issue_date, :amount_cents,
                    :pay_status, :paid_cents, :payment_refs, :run_id, :run_id)
            ON CONFLICT(inv) DO UPDATE SET
                buyer = excluded.buyer,
                buyer_oib = excluded.buyer_oib,
                issue_date = excluded.issue_date,
                amount_cents = excluded.amount_cents,
                pay_status = excluded.pay_status,
                paid_cents = excluded.paid_cents,
                payment_refs = excluded.payment_refs,
                last_seen_run = excluded.last_seen_run
            """,
            {**r, "run_id": run_id},
        )
        cur.execute(
            "INSERT OR REPLACE INTO invoice_history (run_id, inv, pay_status, paid_cents)"
            " VALUES (?, ?, ?, ?)",
            (run_id, r["inv"], r["pay_status"], r["paid_cents"]),
        )

    unmatched_summary = None
    if unmatched_path:
        unmatched_summary = _ingest_unmatched(cur, run_id, unmatched_path)

    conn.commit()
    return {
        "run_id": run_id,
        "invoices": len(rows),
        "open": len(open_rows),
        "storno": sum(1 for r in rows if r["pay_status"] == STORNO_STATUS),
        "total_cents": sum(r["amount_cents"] for r in rows),
        "outstanding_cents": outstanding,
        "new_partners": new_partners,
        "unmatched": unmatched_summary,
        "problems": problems,
    }


def _ingest_unmatched(cur, run_id, unmatched_path):
    payments = load_unmatched(unmatched_path)
    seen_fps = []
    for rec in payments:
        pay_date = parse_hr_date(_pick(rec, "date", "datum", "pay_date"))
        payer = str(_pick(rec, "payer", "platitelj", "name", "buyer", "uplatitelj"))
        amount_cents = to_cents(_pick(rec, "amount", "iznos"))
        reference = str(_pick(rec, "ref", "reference", "poziv", "desc", "opis", "description"))
        raw = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        fp = hashlib.sha1(
            f"{pay_date}|{payer}|{amount_cents}|{reference}".encode("utf-8")
        ).hexdigest()
        seen_fps.append(fp)
        cur.execute(
            """
            INSERT INTO unmatched_payments (fingerprint, pay_date, payer, amount_cents,
                                            reference, raw, status,
                                            first_seen_run, last_seen_run)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                last_seen_run = excluded.last_seen_run,
                status = CASE WHEN unmatched_payments.status = 'dismissed'
                              THEN 'dismissed' ELSE 'open' END
            """,
            (fp, pay_date, payer, amount_cents, reference, raw, run_id, run_id),
        )

    # Payments no longer reported as unmatched were matched in the meantime.
    placeholders = ",".join("?" * len(seen_fps)) or "''"
    cur.execute(
        f"UPDATE unmatched_payments SET status = 'resolved'"
        f" WHERE status = 'open' AND fingerprint NOT IN ({placeholders})",
        seen_fps,
    )
    return {"payments": len(payments), "resolved_now": cur.rowcount}

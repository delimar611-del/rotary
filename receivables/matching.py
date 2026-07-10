"""FIFO-by-buyer matching of bank payments to outgoing invoices.

Replaces the external match_payments.py: payments are attributed to a buyer
(by OIB when present, otherwise by normalized payer name) and consumed by
that buyer's invoices oldest-first. No invoice reference number is needed on
the payment. Whatever cannot be attributed ends up in the unmatched list —
money never disappears silently.
"""
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from .ingest import parse_hr_date, to_cents

LEGAL_SUFFIXES = re.compile(r"\b(d ?o ?o|j ?d ?o ?o|doo|jdoo|obrt|t ?d|vl [a-z ]+)\b")


def norm_name(name):
    """Normalize a company name for comparison: strip diacritics, case,
    punctuation and legal-form suffixes (d.o.o., j.d.o.o., obrt...)."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", " ", s.casefold())
    s = LEGAL_SUFFIXES.sub(" ", s)
    return " ".join(s.split())


def _buyer_key(oib, name):
    oib = str(oib or "").strip()
    if oib:
        return "oib:" + oib
    return "name:" + norm_name(name)


def _iso_to_hr(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")


def match(invoices_raw, payments_raw):
    """Returns (matched_records, unmatched_payments, problems).

    matched_records use the same schema as the old matched.json, so the
    existing ingest handles them unchanged.
    """
    problems = []

    invoices = []
    for rec in invoices_raw:
        date_iso = parse_hr_date(rec.get("date"))
        if date_iso is None:
            problems.append(f"račun {rec.get('inv')}: neispravan datum {rec.get('date')!r}")
            continue
        invoices.append({
            "inv": str(rec.get("inv", "")).strip(),
            "buyer": str(rec.get("buyer", "")).strip(),
            "buyer_oib": str(rec.get("buyer_oib", "")).strip(),
            "date_iso": date_iso,
            "amount_cents": to_cents(rec.get("amount")),
            "storno": bool(rec.get("storno")),
            "paid_cents": 0,
            "payment_refs": [],
        })

    # Index open invoices per buyer, oldest first (FIFO).
    by_buyer = {}
    name_index = {}
    for inv in invoices:
        if inv["storno"]:
            continue
        key = _buyer_key(inv["buyer_oib"], inv["buyer"])
        by_buyer.setdefault(key, []).append(inv)
        if inv["buyer_oib"]:
            # allow payments without OIB to reach OIB-keyed buyers by name
            name_index.setdefault("name:" + norm_name(inv["buyer"]), key)
    for queue in by_buyer.values():
        queue.sort(key=lambda i: (i["date_iso"], i["inv"]))

    payments = []
    for rec in payments_raw:
        payments.append({
            "date_iso": parse_hr_date(rec.get("date")),
            "payer": str(rec.get("payer", "")).strip(),
            "payer_oib": str(rec.get("payer_oib", "")).strip(),
            "amount_cents": to_cents(rec.get("amount")),
            "ref": str(rec.get("ref", "")).strip(),
        })
    payments.sort(key=lambda p: p["date_iso"] or "0000-00-00")

    unmatched = []
    for pay in payments:
        key = None
        if pay["payer_oib"]:
            key = "oib:" + pay["payer_oib"]
            if key not in by_buyer:
                key = None
        if key is None:
            name_key = "name:" + norm_name(pay["payer"])
            key = name_key if name_key in by_buyer else name_index.get(name_key)

        remaining = pay["amount_cents"]
        if key is not None:
            label = (pay["ref"] + " " if pay["ref"] else "") + \
                f"(uplata {_iso_to_hr(pay['date_iso']) if pay['date_iso'] else '?'})"
            for inv in by_buyer[key]:
                if remaining <= 0:
                    break
                due = inv["amount_cents"] - inv["paid_cents"]
                if due <= 0:
                    continue
                take = min(due, remaining)
                inv["paid_cents"] += take
                inv["payment_refs"].append(label.strip())
                remaining -= take

        if remaining > 0:
            unmatched.append({
                "date": _iso_to_hr(pay["date_iso"]) if pay["date_iso"] else "",
                "payer": pay["payer"],
                "amount": remaining / 100.0,
                "ref": pay["ref"] + (
                    " — višak nakon zatvaranja svih računa kupca"
                    if remaining < pay["amount_cents"] or key is not None else ""
                ),
            })

    matched = []
    for inv in invoices:
        if inv["storno"]:
            status, paid = "N/A (storno)", 0
        elif inv["paid_cents"] >= inv["amount_cents"]:
            status, paid = "PAID", inv["paid_cents"]
        elif inv["paid_cents"] > 0:
            status, paid = "PARTIAL", inv["paid_cents"]
        else:
            status, paid = "UNPAID", 0
        matched.append({
            "inv": inv["inv"],
            "buyer": inv["buyer"],
            "buyer_oib": inv["buyer_oib"],
            "date": _iso_to_hr(inv["date_iso"]),
            "amount": inv["amount_cents"] / 100.0,
            "pay_status": status,
            "paid_sum": paid / 100.0,
            "payment_refs": inv["payment_refs"],
        })
    return matched, unmatched, problems


def match_files(invoices_path, payments_path, out_dir=None):
    """Match two JSON files and write matched.json + unmatched.json.

    Returns (matched_path, unmatched_path, problems)."""
    invoices_path = Path(invoices_path)
    invoices_raw = json.loads(invoices_path.read_text(encoding="utf-8"))
    payments_raw = json.loads(Path(payments_path).read_text(encoding="utf-8"))
    matched, unmatched, problems = match(invoices_raw, payments_raw)

    out = Path(out_dir) if out_dir else invoices_path.parent
    out.mkdir(parents=True, exist_ok=True)
    matched_path = out / "matched.json"
    unmatched_path = out / "unmatched.json"
    matched_path.write_text(
        json.dumps(matched, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    unmatched_path.write_text(
        json.dumps(unmatched, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return matched_path, unmatched_path, problems

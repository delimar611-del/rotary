import json
from datetime import date, datetime, timedelta

from .ingest import OPEN_STATUSES, STORNO_STATUS


def bucket_labels(bounds):
    labels = ["Nije dospjelo"]
    prev = 0
    for b in bounds:
        labels.append(f"{prev + 1}–{b} dana")
        prev = b
    labels.append(f"{bounds[-1]}+ dana")
    return labels


def bucket_index(days_overdue, bounds):
    """0 = not yet due, 1..n = overdue buckets, n+1 = beyond last bound."""
    if days_overdue <= 0:
        return 0
    for i, b in enumerate(bounds):
        if days_overdue <= b:
            return i + 1
    return len(bounds) + 1


def open_invoices(conn, cfg, today=None):
    """All UNPAID/PARTIAL invoices with due date, days overdue and bucket."""
    today = today or date.today()
    default_terms = cfg["default_payment_terms_days"]
    bounds = cfg["aging_bucket_bounds"]
    rows = conn.execute(
        """
        SELECT i.*, p.name AS partner_name, p.email AS partner_email,
               p.payment_terms_days AS partner_terms
        FROM invoices i
        LEFT JOIN partners p ON p.oib = i.buyer_oib
        WHERE i.pay_status IN (?, ?)
        ORDER BY i.issue_date
        """,
        OPEN_STATUSES,
    ).fetchall()

    result = []
    for r in rows:
        terms = r["partner_terms"] if r["partner_terms"] is not None else default_terms
        issued = datetime.strptime(r["issue_date"], "%Y-%m-%d").date()
        due = issued + timedelta(days=terms)
        days_overdue = (today - due).days
        outstanding = r["amount_cents"] - r["paid_cents"]
        result.append({
            "inv": r["inv"],
            "buyer": r["buyer"],
            "buyer_oib": r["buyer_oib"],
            "issue_date": issued,
            "due_date": due,
            "terms_days": terms,
            "days_overdue": days_overdue,
            "bucket": bucket_index(days_overdue, bounds),
            "pay_status": r["pay_status"],
            "amount_cents": r["amount_cents"],
            "paid_cents": r["paid_cents"],
            "outstanding_cents": outstanding,
            "payment_refs": json.loads(r["payment_refs"]),
        })
    return result


def bucket_totals(invoices, bounds):
    labels = bucket_labels(bounds)
    totals = [{"label": lbl, "count": 0, "cents": 0} for lbl in labels]
    for inv in invoices:
        totals[inv["bucket"]]["count"] += 1
        totals[inv["bucket"]]["cents"] += inv["outstanding_cents"]
    return totals


def by_partner(invoices):
    """Group open invoices per partner, sorted by outstanding desc."""
    partners = {}
    for inv in invoices:
        p = partners.setdefault(inv["buyer_oib"], {
            "oib": inv["buyer_oib"],
            "name": inv["buyer"],
            "outstanding_cents": 0,
            "invoice_count": 0,
            "max_days_overdue": None,
            "invoices": [],
        })
        p["outstanding_cents"] += inv["outstanding_cents"]
        p["invoice_count"] += 1
        p["invoices"].append(inv)
        if inv["days_overdue"] > 0:
            cur = p["max_days_overdue"]
            p["max_days_overdue"] = max(cur or 0, inv["days_overdue"])
    return sorted(partners.values(), key=lambda p: -p["outstanding_cents"])


def storno_invoices(conn):
    return conn.execute(
        "SELECT * FROM invoices WHERE pay_status = ? ORDER BY issue_date DESC",
        (STORNO_STATUS,),
    ).fetchall()


def open_unmatched(conn):
    return conn.execute(
        "SELECT * FROM unmatched_payments WHERE status = 'open' ORDER BY pay_date DESC"
    ).fetchall()

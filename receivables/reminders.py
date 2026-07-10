import json
import re
import unicodedata
from datetime import date, datetime

from flask import abort, redirect, render_template, request, send_file, url_for

from . import aging, config
from .ingest import fmt_eur

LEVEL_NAMES = {1: "Podsjetnik", 2: "Požurnica", 3: "Opomena"}

SUBJECTS = {
    1: "Podsjetnik na otvorene stavke — {company}",
    2: "Požurnica — dospjela potraživanja — {company}",
    3: "Opomena — nepodmirena dospjela potraživanja — {company}",
}

INTROS = {
    1: (
        "slobodni smo Vas podsjetiti da prema našoj evidenciji na dan {today} "
        "imamo sljedeće otvorene stavke:"
    ),
    2: (
        "unatoč ranijem podsjetniku, prema našoj evidenciji sljedeće stavke još "
        "uvijek nisu podmirene, a rok dospijeća im je prošao:"
    ),
    3: (
        "i pored ranijih podsjetnika, niže navedena potraživanja ostaju "
        "nepodmirena, od čega najstarije već {max_overdue} dana nakon dospijeća:"
    ),
}

CLOSINGS = {
    1: (
        "Ako ste uplatu u međuvremenu izvršili, molimo zanemarite ovaj podsjetnik.\n"
        "Za sva pitanja i usklađenje stanja stojimo Vam na raspolaganju."
    ),
    2: (
        "Molimo Vas da dospjeli iznos podmirite u roku od 8 dana od primitka ove "
        "požurnice, s pozivom na broj računa.\n"
        "Ako ste uplatu već izvršili, molimo da nam dostavite potvrdu kako bismo "
        "uskladili evidenciju."
    ),
    3: (
        "Molimo da cjelokupni dospjeli iznos podmirite najkasnije u roku od 8 dana "
        "od primitka ove opomene. U protivnom ćemo biti prisiljeni razmotriti "
        "daljnje korake naplate, uključujući obračun zakonskih zateznih kamata i "
        "pokretanje postupka prisilne naplate.\n"
        "Vjerujemo da situaciju možemo riješiti bez daljnjih koraka i ostajemo "
        "otvoreni za dogovor."
    ),
}


def suggest_level(max_days_overdue, bounds):
    """bounds = [max days for level 1, max days for level 2]"""
    if max_days_overdue <= bounds[0]:
        return 1
    if max_days_overdue <= bounds[1]:
        return 2
    return 3


def _hrdate(d):
    return d.strftime("%d.%m.%Y.")


def _invoice_table(invoices):
    header = f"  {'Račun':<14}{'Izdan':<13}{'Dospijeće':<13}{'Kasni':>7}  {'Otvoreno':>14}"
    lines = [header, "  " + "-" * (len(header) - 2)]
    for i in invoices:
        lines.append(
            f"  {i['inv']:<14}{_hrdate(i['issue_date']):<13}"
            f"{_hrdate(i['due_date']):<13}{str(i['days_overdue']) + ' d.':>7}"
            f"  {fmt_eur(i['outstanding_cents']):>14}"
        )
    return "\n".join(lines)


def build_draft(partner, invoices, level, cfg, today=None):
    """Compose subject and body for a reminder. Pure function — no side effects."""
    today = today or date.today()
    overdue = sorted(
        (i for i in invoices if i["days_overdue"] > 0),
        key=lambda i: i["due_date"],
    )
    if not overdue:
        return None
    company = cfg["company"]
    total = sum(i["outstanding_cents"] for i in overdue)
    max_overdue = max(i["days_overdue"] for i in overdue)

    subject = SUBJECTS[level].format(company=company["name"])
    intro = INTROS[level].format(today=_hrdate(today), max_overdue=max_overdue)
    salutation = "Poštovani,"
    if partner["contact"]:
        salutation = f"Poštovani ({partner['contact']}),"

    body = "\n".join([
        salutation,
        "",
        intro,
        "",
        _invoice_table(overdue),
        "",
        f"  Ukupno dospjelo: {fmt_eur(total)}",
        "",
        f"Uplatu molimo izvršiti na račun:",
        f"  IBAN: {company['iban']} ({company['bank']})",
        f"  Model i poziv na broj: broj računa iz tablice",
        "",
        CLOSINGS[level],
        "",
        "S poštovanjem,",
        "",
        f"{company['contact_name']}",
        f"{company['name']}",
        f"{company['address']}",
        f"OIB: {company['oib']}",
        f"tel: {company['contact_phone']} · e-mail: {company['contact_email']}",
    ])
    return {
        "subject": subject,
        "body": body,
        "level": level,
        "invoices": [i["inv"] for i in overdue],
        "outstanding_cents": total,
        "max_days_overdue": max_overdue,
    }


def _slug(name):
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-").lower() or "partner"


def save_draft(conn, partner, draft, email=""):
    cur = conn.execute(
        "INSERT INTO reminders (oib, level, invoices, subject, body,"
        " outstanding_cents, drafted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            partner["oib"], draft["level"],
            json.dumps(draft["invoices"], ensure_ascii=False),
            draft["subject"], draft["body"], draft["outstanding_cents"],
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    rid = cur.lastrowid
    path = outbox_path(rid, partner, draft)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"Za: {email or '(e-mail nije upisan — dopunite u imeniku partnera)'}\n"
        f"Predmet: {draft['subject']}\n"
        f"{'-' * 60}\n{draft['body']}\n"
    )
    path.write_text(content, encoding="utf-8")
    return rid, path


def outbox_path(rid, partner, draft):
    stamp = date.today().isoformat()
    return config.OUTBOX_DIR / f"{stamp}_r{draft['level']}_{_slug(partner['name'])}_{rid}.txt"


def register_routes(app, get_conn):

    @app.route("/podsjetnici")
    def reminders_view():
        cfg = config.load_config()
        conn = get_conn()
        partners = aging.by_partner(aging.open_invoices(conn, cfg))
        candidates = []
        for p in partners:
            if not p["max_days_overdue"]:
                continue
            row = conn.execute("SELECT * FROM partners WHERE oib = ?",
                               (p["oib"],)).fetchone()
            last = conn.execute(
                "SELECT * FROM reminders WHERE oib = ? ORDER BY id DESC LIMIT 1",
                (p["oib"],),
            ).fetchone()
            candidates.append({
                "partner": row,
                "outstanding_cents": sum(
                    i["outstanding_cents"] for i in p["invoices"]
                    if i["days_overdue"] > 0
                ),
                "overdue_count": sum(1 for i in p["invoices"] if i["days_overdue"] > 0),
                "max_days_overdue": p["max_days_overdue"],
                "level": suggest_level(p["max_days_overdue"],
                                       cfg["reminder_level_bounds"]),
                "last": last,
            })
        candidates.sort(key=lambda c: (-c["level"], -c["outstanding_cents"]))
        drafts = conn.execute(
            "SELECT r.*, p.name AS partner_name FROM reminders r"
            " JOIN partners p ON p.oib = r.oib ORDER BY r.id DESC LIMIT 50"
        ).fetchall()
        return render_template("reminders.html", candidates=candidates,
                               drafts=drafts, level_names=LEVEL_NAMES)

    @app.route("/podsjetnici/nacrt/<path:oib>", methods=["GET", "POST"])
    def reminder_draft(oib):
        cfg = config.load_config()
        conn = get_conn()
        partner = conn.execute("SELECT * FROM partners WHERE oib = ?", (oib,)).fetchone()
        if partner is None:
            abort(404)
        invoices = [i for i in aging.open_invoices(conn, cfg) if i["buyer_oib"] == oib]
        suggested = suggest_level(
            max((i["days_overdue"] for i in invoices), default=0),
            cfg["reminder_level_bounds"],
        )
        level = request.values.get("level", type=int) or suggested
        level = min(max(level, 1), 3)
        draft = build_draft(partner, invoices, level, cfg)
        if draft is None:
            return render_template("reminder_draft.html", partner=partner,
                                   draft=None, level=level, suggested=suggested,
                                   level_names=LEVEL_NAMES)
        if request.method == "POST":
            rid, path = save_draft(conn, partner, draft, email=partner["email"])
            return redirect(url_for("reminder_detail", rid=rid, saved=1))
        return render_template("reminder_draft.html", partner=partner, draft=draft,
                               level=level, suggested=suggested,
                               level_names=LEVEL_NAMES)

    @app.route("/podsjetnici/<int:rid>")
    def reminder_detail(rid):
        conn = get_conn()
        rem = conn.execute(
            "SELECT r.*, p.name AS partner_name, p.email AS partner_email"
            " FROM reminders r JOIN partners p ON p.oib = r.oib WHERE r.id = ?",
            (rid,),
        ).fetchone()
        if rem is None:
            abort(404)
        return render_template("reminder_detail.html", rem=rem,
                               invoices=json.loads(rem["invoices"]),
                               level_names=LEVEL_NAMES,
                               saved=request.args.get("saved"))

    @app.route("/podsjetnici/<int:rid>/poslan", methods=["POST"])
    def reminder_mark_sent(rid):
        conn = get_conn()
        undo = request.form.get("undo")
        conn.execute(
            "UPDATE reminders SET sent_at = ? WHERE id = ?",
            (None if undo else datetime.now().isoformat(timespec="seconds"), rid),
        )
        conn.commit()
        return redirect(url_for("reminder_detail", rid=rid))

    @app.route("/podsjetnici/<int:rid>/preuzmi")
    def reminder_download(rid):
        conn = get_conn()
        rem = conn.execute(
            "SELECT r.*, p.name AS partner_name, p.email AS partner_email"
            " FROM reminders r JOIN partners p ON p.oib = r.oib WHERE r.id = ?",
            (rid,),
        ).fetchone()
        if rem is None:
            abort(404)
        content = (
            f"Za: {rem['partner_email'] or '(e-mail nije upisan)'}\n"
            f"Predmet: {rem['subject']}\n{'-' * 60}\n{rem['body']}\n"
        )
        fname = f"podsjetnik_{rid}_{_slug(rem['partner_name'])}.txt"
        from io import BytesIO
        return send_file(BytesIO(content.encode("utf-8")), mimetype="text/plain",
                         as_attachment=True, download_name=fname)

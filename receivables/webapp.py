import json
from datetime import date, datetime

from flask import Flask, abort, g, redirect, render_template, request, url_for

from . import aging, config
from . import db as dbmod
from .ingest import OPEN_STATUSES, STORNO_STATUS, fmt_eur


def create_app(db_path=None):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path

    def get_conn():
        if "conn" not in g:
            g.conn = dbmod.connect(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def close_conn(exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.template_filter("eur")
    def eur_filter(cents):
        return fmt_eur(cents or 0)

    @app.template_filter("hrdate")
    def hrdate_filter(value):
        if not value:
            return "—"
        if isinstance(value, str):
            try:
                value = datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return value
        return value.strftime("%d.%m.%Y.")

    @app.context_processor
    def inject_globals():
        cfg = config.load_config()
        return {
            "cfg": cfg,
            "company": cfg["company"],
            "bucket_names": aging.bucket_labels(cfg["aging_bucket_bounds"]),
            "today": date.today(),
        }

    @app.route("/")
    def overview():
        cfg = config.load_config()
        conn = get_conn()
        invoices = aging.open_invoices(conn, cfg)
        totals = aging.bucket_totals(invoices, cfg["aging_bucket_bounds"])
        outstanding = sum(t["cents"] for t in totals)
        overdue = sum(t["cents"] for t in totals[1:])
        partners = aging.by_partner(invoices)
        unmatched = aging.open_unmatched(conn)
        stornos = aging.storno_invoices(conn)
        runs = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 8").fetchall()
        max_bucket = max((t["cents"] for t in totals), default=0)
        return render_template(
            "overview.html",
            totals=totals, outstanding=outstanding, overdue=overdue,
            open_count=len(invoices), partners=partners[:10],
            partner_count=len(partners),
            unmatched_count=len(unmatched),
            unmatched_sum=sum(u["amount_cents"] for u in unmatched),
            storno_count=len(stornos),
            runs=runs, max_bucket=max_bucket,
        )

    @app.route("/racuni")
    def invoices_view():
        cfg = config.load_config()
        conn = get_conn()
        invoices = aging.open_invoices(conn, cfg)
        bucket = request.args.get("bucket")
        if bucket is not None and bucket.isdigit():
            invoices = [i for i in invoices if i["bucket"] == int(bucket)]
        sort = request.args.get("sort", "overdue")
        keys = {
            "overdue": lambda i: -i["days_overdue"],
            "amount": lambda i: -i["outstanding_cents"],
            "buyer": lambda i: (i["buyer"].casefold(), -i["days_overdue"]),
            "due": lambda i: i["due_date"],
        }
        invoices.sort(key=keys.get(sort, keys["overdue"]))
        return render_template("invoices.html", invoices=invoices,
                               bucket=bucket, sort=sort)

    @app.route("/partneri")
    def partners_view():
        cfg = config.load_config()
        conn = get_conn()
        open_by_partner = {p["oib"]: p for p in
                           aging.by_partner(aging.open_invoices(conn, cfg))}
        rows = conn.execute("SELECT * FROM partners ORDER BY name COLLATE NOCASE").fetchall()
        partners = []
        for r in rows:
            open_info = open_by_partner.get(r["oib"])
            partners.append({
                "row": r,
                "outstanding_cents": open_info["outstanding_cents"] if open_info else 0,
                "invoice_count": open_info["invoice_count"] if open_info else 0,
                "max_days_overdue": open_info["max_days_overdue"] if open_info else None,
            })
        partners.sort(key=lambda p: -p["outstanding_cents"])
        missing_email = sum(1 for p in partners
                            if p["outstanding_cents"] and not p["row"]["email"])
        return render_template("partners.html", partners=partners,
                               missing_email=missing_email)

    @app.route("/partneri/<path:oib>", methods=["GET", "POST"])
    def partner_detail(oib):
        cfg = config.load_config()
        conn = get_conn()
        partner = conn.execute("SELECT * FROM partners WHERE oib = ?", (oib,)).fetchone()
        if partner is None:
            abort(404)
        if request.method == "POST":
            terms = request.form.get("payment_terms_days", "").strip()
            conn.execute(
                "UPDATE partners SET email = ?, contact = ?, payment_terms_days = ?,"
                " notes = ? WHERE oib = ?",
                (
                    request.form.get("email", "").strip(),
                    request.form.get("contact", "").strip(),
                    int(terms) if terms.isdigit() else None,
                    request.form.get("notes", "").strip(),
                    oib,
                ),
            )
            conn.commit()
            return redirect(url_for("partner_detail", oib=oib, saved=1))

        invoices = [i for i in aging.open_invoices(conn, cfg) if i["buyer_oib"] == oib]
        all_rows = conn.execute(
            "SELECT * FROM invoices WHERE buyer_oib = ? ORDER BY issue_date DESC", (oib,)
        ).fetchall()
        reminders = conn.execute(
            "SELECT * FROM reminders WHERE oib = ? ORDER BY id DESC", (oib,)
        ).fetchall()
        return render_template(
            "partner_detail.html",
            partner=partner, invoices=invoices, all_rows=all_rows,
            reminders=reminders,
            outstanding=sum(i["outstanding_cents"] for i in invoices),
            saved=request.args.get("saved"),
        )

    @app.route("/kontrola")
    def control_view():
        conn = get_conn()
        stornos = aging.storno_invoices(conn)
        unmatched = conn.execute(
            "SELECT * FROM unmatched_payments"
            " ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'dismissed' THEN 1 ELSE 2 END,"
            " pay_date DESC"
        ).fetchall()
        return render_template(
            "control.html", stornos=stornos, unmatched=unmatched,
            storno_sum=sum(s["amount_cents"] for s in stornos),
            open_sum=sum(u["amount_cents"] for u in unmatched if u["status"] == "open"),
        )

    @app.route("/uplate/<int:pid>/status", methods=["POST"])
    def unmatched_status(pid):
        new_status = request.form.get("status")
        if new_status not in ("open", "dismissed"):
            abort(400)
        conn = get_conn()
        conn.execute("UPDATE unmatched_payments SET status = ? WHERE id = ?",
                     (new_status, pid))
        conn.commit()
        return redirect(url_for("control_view"))

    from . import reminders as rem
    rem.register_routes(app, get_conn)

    return app

import argparse
import sys

from . import aging, config, db, ingest


def cmd_ingest(args):
    conn = db.connect(args.db)
    summary = ingest.ingest(conn, args.matched, args.unmatched)
    print(f"Uvoz #{summary['run_id']} iz {args.matched}")
    print(f"  računa:        {summary['invoices']}")
    print(f"  otvorenih:     {summary['open']}")
    print(f"  storno:        {summary['storno']}")
    print(f"  ukupno:        {ingest.fmt_eur(summary['total_cents'])}")
    print(f"  nenaplaćeno:   {ingest.fmt_eur(summary['outstanding_cents'])}")
    print(f"  novih partnera:{summary['new_partners']:>4}")
    if summary["unmatched"]:
        u = summary["unmatched"]
        print(f"  nespojene uplate: {u['payments']} (razriješeno od ranije: {u['resolved_now']})")
    for p in summary["problems"]:
        print(f"  UPOZORENJE: {p}", file=sys.stderr)


def cmd_report(args):
    cfg = config.load_config()
    conn = db.connect(args.db)
    invoices = aging.open_invoices(conn, cfg)

    print("=" * 72)
    print(f"{cfg['company']['name']} — otvorena potraživanja")
    print("=" * 72)

    totals = aging.bucket_totals(invoices, cfg["aging_bucket_bounds"])
    total_cents = sum(t["cents"] for t in totals)
    print("\nDospijeće (aging):")
    for t in totals:
        print(f"  {t['label']:<16} {t['count']:>3} rn.  {ingest.fmt_eur(t['cents']):>14}")
    print(f"  {'UKUPNO':<16} {len(invoices):>3} rn.  {ingest.fmt_eur(total_cents):>14}")

    print("\nPo partnerima:")
    for p in aging.by_partner(invoices):
        overdue = (
            f"najstarije kasni {p['max_days_overdue']} d."
            if p["max_days_overdue"] else "ništa nije dospjelo"
        )
        print(f"  {p['name']:<32} {ingest.fmt_eur(p['outstanding_cents']):>14}"
              f"  ({p['invoice_count']} rn., {overdue})")
        if args.detail:
            for inv in p["invoices"]:
                print(f"      {inv['inv']:<12} {inv['issue_date']}  dospijeće {inv['due_date']}"
                      f"  {max(inv['days_overdue'], 0):>4} d.  "
                      f"{ingest.fmt_eur(inv['outstanding_cents']):>12}  {inv['pay_status']}")

    stornos = aging.storno_invoices(conn)
    if stornos:
        print("\nStorno računi (N/A):")
        for s in stornos:
            print(f"  {s['inv']:<12} {s['issue_date']}  {s['buyer']:<32}"
                  f"  {ingest.fmt_eur(s['amount_cents']):>12}")

    unmatched = aging.open_unmatched(conn)
    if unmatched:
        print("\nNespojene uplate (novac bez računa!):")
        for u in unmatched:
            print(f"  {u['pay_date'] or '?':<12} {u['payer']:<32}"
                  f"  {ingest.fmt_eur(u['amount_cents']):>12}  {u['reference']}")


def cmd_serve(args):
    from .webapp import create_app
    app = create_app(args.db)
    print(f"Dashboard: http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)


def cmd_init_config(args):
    if config.write_default_config():
        print(f"Stvoren {config.CONFIG_PATH} — upišite IBAN, banku i kontakt podatke.")
    else:
        print(f"{config.CONFIG_PATH} već postoji, ništa nije mijenjano.")


def main():
    parser = argparse.ArgumentParser(
        prog="receivables",
        description="DETONEX d.o.o. — lokalni dashboard za naplatu potraživanja",
    )
    parser.add_argument("--db", default=None,
                        help=f"putanja do SQLite baze (zadano: {config.DEFAULT_DB_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="uvezi matched.json (i opcionalno nespojene uplate)")
    p_ingest.add_argument("matched", help="putanja do matched.json")
    p_ingest.add_argument("--unmatched", default=None,
                          help="putanja do JSON datoteke s nespojenim uplatama")
    p_ingest.set_defaults(func=cmd_ingest)

    p_report = sub.add_parser("report", help="tekstualni pregled potraživanja")
    p_report.add_argument("--detail", action="store_true", help="ispiši i pojedinačne račune")
    p_report.set_defaults(func=cmd_report)

    p_serve = sub.add_parser("serve", help="pokreni web dashboard (lokalno)")
    p_serve.add_argument("--port", type=int, default=8077)
    p_serve.add_argument("--debug", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_cfg = sub.add_parser("init-config", help="stvori config.json s placeholderima")
    p_cfg.set_defaults(func=cmd_init_config)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

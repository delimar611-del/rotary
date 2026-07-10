#!/usr/bin/env python3
"""Chain parse_invoices.py -> match_payments.py -> receivables ingest.

The three eposlovanje-naplata scripts are NOT bundled yet — drop them into
this directory. Until then this runner only validates the setup and, if a
ready-made matched.json is passed, hands it straight to ingest.
"""
import argparse
import subprocess
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent.parent
REQUIRED = ("parse_invoices.py", "match_payments.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matched", help="preskoči parse/match i uvezi gotov matched.json")
    parser.add_argument("--unmatched", help="JSON s nespojenim uplatama (opcionalno)")
    args = parser.parse_args()

    if args.matched:
        cmd = [sys.executable, "-m", "receivables", "ingest", args.matched]
        if args.unmatched:
            cmd += ["--unmatched", args.unmatched]
        sys.exit(subprocess.call(cmd, cwd=REPO_ROOT))

    missing = [s for s in REQUIRED if not (PIPELINE_DIR / s).exists()]
    if missing:
        print("Nedostaju skripte u receivables/pipeline/: " + ", ".join(missing))
        print("Odložite ih ovdje pa ćemo ulančavanje dovršiti (vidi README.md).")
        print("U međuvremenu: run_pipeline.py --matched put/do/matched.json")
        sys.exit(1)

    # TODO (integracija, korak 5): pozvati parse_invoices.py i match_payments.py
    # s parametriziranim putanjama pa uvesti njihov matched.json + unmatched.json.
    print("Skripte su prisutne, ali ulančavanje još nije konfigurirano (korak 5).")
    sys.exit(1)


if __name__ == "__main__":
    main()

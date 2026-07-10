import json
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"
DEFAULT_DB_PATH = ROOT_DIR / "data" / "receivables.db"
OUTBOX_DIR = ROOT_DIR / "outbox"

DEFAULTS = {
    "company": {
        "name": "DETONEX d.o.o.",
        "oib": "{{VAŠ_OIB}}",
        "iban": "{{VAŠ_IBAN}}",
        "bank": "{{NAZIV_BANKE}}",
        "address": "{{ADRESA_TVRTKE}}",
        "contact_name": "{{IME_I_PREZIME}}",
        "contact_email": "{{VAŠ_EMAIL}}",
        "contact_phone": "{{VAŠ_TELEFON}}",
    },
    "default_payment_terms_days": 30,
    # Upper bounds (days overdue) of the aging buckets; final bucket is open-ended.
    "aging_bucket_bounds": [15, 30, 60, 90],
    # Max days overdue for reminder level 1 and 2; beyond that -> level 3.
    "reminder_level_bounds": [15, 45],
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if CONFIG_PATH.exists():
        user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for key, value in user_cfg.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


def write_default_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULTS, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    return False

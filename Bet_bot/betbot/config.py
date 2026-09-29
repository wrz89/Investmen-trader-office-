"""Percorsi e configurazione di Bet_bot.

Bet_bot è una cartella autonoma (es. D:\claude\Bet_bot): codice, configurazione,
dashboard e dati stanno tutti lì dentro. Tutto ciò che il bot produce (database,
storico, report, chiavi) finisce in runtime/, che non va mai su GitHub.
Spostabile con la variabile BETBOT_RUNTIME_DIR.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DASHBOARD_FILE = ROOT / "dashboard" / "index.html"
SAMPLE_DATA_DIR = ROOT / "data"          # esempio del formato CSV generico per il backtest

RUNTIME_DIR = Path(os.environ.get("BETBOT_RUNTIME_DIR", ROOT / "runtime"))
REPORTS_DIR = RUNTIME_DIR / "reports"
DB_PATH = RUNTIME_DIR / "betbot.db"            # paper: soldi finti
DB_LIVE_PATH = RUNTIME_DIR / "betbot_live.db"  # live: SOLO soldi veri, bankroll dal saldo Betfair
STOP_FILE = RUNTIME_DIR / "ferma.richiesta"    # `betbot.py ferma` lo crea: il ciclo chiude i trade ed esce
LOCAL_SETTINGS = RUNTIME_DIR / "local_settings.json"


def ensure_dirs() -> None:
    for d in (RUNTIME_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


LOCAL_OVERRIDE = RUNTIME_DIR / "impostazioni.yaml"   # le TUE scelte: aggiorna.bat non le tocca mai


def _deep_merge(base: dict, extra: dict) -> dict:
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_settings(overrides: dict | None = None) -> dict:
    settings = load_yaml("settings.yaml")
    if LOCAL_OVERRIDE.exists():
        with open(LOCAL_OVERRIDE, encoding="utf-8") as fh:
            _deep_merge(settings, yaml.safe_load(fh) or {})
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        section, _, field = key.partition(".")
        if field:
            settings.setdefault(section, {})[field] = value
        else:
            settings[section] = value
    if settings.get("mode") not in ("paper", "live"):
        raise RuntimeError("mode deve essere 'paper' oppure 'live'.")
    return settings


def api_key() -> str:
    """Chiave di The Odds API: variabile d'ambiente o runtime/local_settings.json."""
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if key:
        return key
    if LOCAL_SETTINGS.exists():
        import json
        try:
            return (json.loads(LOCAL_SETTINGS.read_text(encoding="utf-8")).get("odds_api_key") or "").strip()
        except (OSError, ValueError):
            return ""
    return ""

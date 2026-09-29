"""Percorsi e configurazione dell'ufficio sportivo.

Tutto ciò che l'ufficio produce finisce in runtime/sport/ (separato dal
runtime dell'ufficio crypto). Spostabile con SPORT_RUNTIME_DIR.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config" / "sport"
DASHBOARD_FILE = ROOT / "dashboard" / "sport.html"
SAMPLE_DATA_DIR = ROOT / "data" / "sport"          # esempio del formato CSV generico per il backtest

RUNTIME_DIR = Path(os.environ.get("SPORT_RUNTIME_DIR", ROOT / "runtime" / "sport"))
REPORTS_DIR = RUNTIME_DIR / "reports"
DB_PATH = RUNTIME_DIR / "sport.db"
LOCAL_SETTINGS = RUNTIME_DIR / "local_settings.json"


def ensure_dirs() -> None:
    for d in (RUNTIME_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_settings(overrides: dict | None = None) -> dict:
    settings = load_yaml("settings.yaml")
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
    """Chiave di The Odds API: variabile d'ambiente o runtime/sport/local_settings.json."""
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

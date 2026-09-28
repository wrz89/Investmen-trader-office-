"""Caricamento configurazione e percorsi dell'ufficio."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DASHBOARD_DIR = ROOT / "dashboard"

# Tutto ciò che il sistema produce (database, storico, registro, report)
# finisce in runtime/. Si può spostare con la variabile OFFICE_RUNTIME_DIR.
RUNTIME_DIR = Path(os.environ.get("OFFICE_RUNTIME_DIR", ROOT / "runtime"))
DATA_DIR = RUNTIME_DIR / "data"
REGISTRY_DIR = RUNTIME_DIR / "registry"
REPORTS_DIR = RUNTIME_DIR / "reports"
DB_PATH = RUNTIME_DIR / "office.db"


def ensure_dirs() -> None:
    for d in (RUNTIME_DIR, DATA_DIR, REGISTRY_DIR, REPORTS_DIR):
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
    return settings

"""Impostazioni personali modificabili dalla dashboard (Telegram, notifiche, PC sveglio).

Stanno in runtime/local_settings.json: restano sul tuo PC e non vanno mai su GitHub.
"""
from __future__ import annotations

import copy
import json

from .config import RUNTIME_DIR

PATH = RUNTIME_DIR / "local_settings.json"

DEFAULTS = {
    "telegram": {"token": "", "chat_id": "", "chat_name": ""},
    "notify": {
        "trades": True,         # acquisti, vendite, trade chiusi
        "vetoes": True,         # veti su strategie AUTORIZZATE (quelle che potrebbero operare)
        "vetoes_all": False,    # anche i veti sulle strategie rifiutate (molto rumoroso)
        "daily_report": True,
        "alerts": True,         # kill switch, errori, dati anomali
    },
    "keep_awake": True,         # impedisce la sospensione del PC mentre l'ufficio lavora
}


def load() -> dict:
    data = copy.deepcopy(DEFAULTS)
    if PATH.exists():
        try:
            saved = json.loads(PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        for key, value in saved.items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key].update(value)
            else:
                data[key] = value
    return data


def save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def public(data: dict) -> dict:
    """Versione per il browser: il token non esce mai per intero."""
    out = copy.deepcopy(data)
    token = data["telegram"].get("token") or ""
    out["telegram"]["token"] = f"{token[:6]}…{token[-4:]}" if len(token) > 12 else ""
    out["telegram"]["configured"] = bool(token and data["telegram"].get("chat_id"))
    out["telegram"]["has_token"] = bool(token)
    return out

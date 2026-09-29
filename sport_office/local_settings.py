"""Impostazioni personali dell'ufficio sportivo (chiavi API, Telegram, notifiche).

Stanno in runtime/sport/local_settings.json: restano sul PC, mai su GitHub.
Al browser arrivano solo versioni mascherate: segreti e password mai.
"""
from __future__ import annotations

import copy
import json

from .config import LOCAL_SETTINGS as PATH

DEFAULTS = {
    # Se vuoto, l'ufficio sportivo usa il bot Telegram già collegato all'ufficio crypto.
    "telegram": {"token": "", "chat_id": "", "chat_name": ""},
    "notify": {
        "bets": True,          # ogni puntata piazzata (richiesta esplicita dell'utente)
        "settles": True,       # ogni puntata chiusa (vinta, persa, cash-out, green-up)
        "breakers": True,      # kill switch, perdita giornaliera, serie negativa, limiti manomessi
        "sentiment": True,     # blocchi e cautele dell'analista sentiment
        "errors": True,        # feed giù, errori di ciclo
        "daily_report": True,
    },
    "odds_api_key": "",        # https://the-odds-api.com
    "api_football_key": "",    # https://www.api-football.com (API-Sports)
    # Betfair Exchange Italia (betfair.it, concessione ADM). Senza "live_enabled" si resta in paper.
    "betfair": {"app_key": "", "username": "", "password": "", "cert_file": "", "key_file": "",
                "verified": False, "live_enabled": False, "test_done": False},
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


def _mask(v: str, head: int = 4, tail: int = 3) -> str:
    return f"{v[:head]}…{v[-tail:]}" if v and len(v) > head + tail + 2 else ("•••" if v else "")


def public(data: dict) -> dict:
    out = copy.deepcopy(data)
    tg = data["telegram"]
    out["telegram"]["token"] = _mask(tg.get("token") or "", 6, 4)
    out["telegram"]["has_token"] = bool(tg.get("token"))
    out["telegram"]["configured"] = bool(tg.get("token") and tg.get("chat_id"))
    out["odds_api_key"] = _mask(data.get("odds_api_key") or "")
    out["api_football_key"] = _mask(data.get("api_football_key") or "")
    bf = data["betfair"]
    out["betfair"].update(app_key=_mask(bf.get("app_key") or ""), password="",
                          has_login=bool(bf.get("app_key") and bf.get("username") and bf.get("password")))
    return out

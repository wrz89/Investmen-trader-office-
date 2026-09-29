"""Notifiche Telegram dell'ufficio sportivo.

Regola d'oro (come nell'ufficio crypto): una notifica che fallisce non ferma
MAI l'ufficio. Ogni invio parte in un thread separato con timeout.

Canale: il bot configurato nelle impostazioni dell'ufficio sportivo; se manca,
quello dell'ufficio crypto (stesso telefono, un solo bot).
"""
from __future__ import annotations

import threading
import time

import requests

from . import local_settings

API = "https://api.telegram.org/bot{token}/{method}"
ICON = {"bet": "🎯", "settle": "📒", "kill_switch": "🚨", "circuit": "🛑", "sentiment": "📰", "error": "⚠️",
        "report": "📊", "no_data": "📡", "cashout": "💸"}


class TelegramError(Exception):
    pass


def _escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def call(token: str, method: str, payload: dict | None = None, timeout: int = 10) -> dict:
    try:
        r = requests.post(API.format(token=token, method=method), json=payload or {}, timeout=timeout)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"Telegram non raggiungibile: {exc}") from exc
    if not data.get("ok"):
        desc = data.get("description") or "risposta non valida da Telegram"
        if "Unauthorized" in desc or "Not Found" in desc:
            desc = "Token non valido: ricopialo per intero dal messaggio di BotFather."
        elif "chat not found" in desc.lower():
            desc = "Chat non trovata: apri il bot su Telegram, premi AVVIA e poi 'Trova la mia chat'."
        raise TelegramError(desc)
    return data["result"]


def detect_chat(token: str) -> dict:
    me = call(token, "getMe")
    updates = call(token, "getUpdates", {"timeout": 0})
    chats = [u["message"]["chat"] for u in updates if "message" in u and u["message"]["chat"]["type"] == "private"]
    if not chats:
        raise TelegramError(f"Non trovo ancora la tua chat. Su Telegram apri @{me.get('username')}, scrivigli "
                            "un messaggio qualsiasi e riprova.")
    chat = chats[-1]
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username", "")
    return {"chat_id": str(chat["id"]), "chat_name": name, "bot": me.get("username")}


def channel() -> dict | None:
    """Token e chat da usare: prima quelli dell'ufficio sportivo, poi quelli dell'ufficio crypto."""
    tg = local_settings.load()["telegram"]
    if tg.get("token") and tg.get("chat_id"):
        return tg
    try:
        from office import local_settings as crypto_settings
        ctg = crypto_settings.load()["telegram"]
        if ctg.get("token") and ctg.get("chat_id"):
            return ctg
    except Exception:
        pass
    return None


def send_now(text: str) -> None:
    ch = channel()
    if not ch:
        raise TelegramError("Telegram non configurato")
    call(ch["token"], "sendMessage", {"chat_id": ch["chat_id"], "text": text, "parse_mode": "HTML",
                                      "disable_web_page_preview": True})


class Notifier:
    COOLDOWN = 1800            # lo stesso errore al massimo ogni 30 minuti

    def __init__(self, store=None, enabled: bool = True):
        self.store = store
        self.enabled = enabled
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.sent: list[str] = []            # per i test

    def _send(self, text: str) -> None:
        self.sent.append(text)
        if not self.enabled or channel() is None:
            return                                   # Telegram non collegato: nessun messaggio, nessun errore

        def run():
            try:
                send_now(text)
            except Exception as exc:
                if self.store is not None:
                    try:
                        self.store.event("auditor", f"Notifica Telegram non inviata: {exc}", "WARN", "notify_error")
                    except Exception:
                        pass
        threading.Thread(target=run, daemon=True).start()

    def _cooldown_ok(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            if now - self._last.get(key, 0) < self.COOLDOWN:
                return False
            self._last[key] = now
            return True

    def on_event(self, who: str, level: str, kind: str, message: str) -> None:
        if kind == "notify_error":
            return
        n = local_settings.load()["notify"]
        head = f"{ICON.get(kind, 'ℹ️')} <b>{_escape(who)}</b>\n"
        if kind == "bet" and n.get("bets"):
            self._send(head + _escape(message))
        elif kind in ("settle", "cashout") and n.get("settles"):
            self._send(head + _escape(message))
        elif kind in ("kill_switch", "circuit") and n.get("breakers"):
            self._send(head + _escape(message))
        elif kind == "sentiment" and n.get("sentiment") and level in ("WARN", "ERROR"):
            self._send(head + _escape(message))
        elif kind == "report" and n.get("daily_report"):
            self._send(head + _escape(message))
        elif level in ("ERROR", "CRITICAL") and n.get("errors") and self._cooldown_ok(f"{kind}|{message[:50]}"):
            self._send(ICON["error"] + " " + head + _escape(message))

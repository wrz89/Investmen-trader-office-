"""Notifiche Telegram.

Regola d'oro: una notifica che fallisce non deve MAI fermare o rallentare l'ufficio.
Ogni invio avviene in un thread separato, con timeout, e gli errori vengono solo annotati.
"""
from __future__ import annotations

import threading
import time

import requests

from . import local_settings

API = "https://api.telegram.org/bot{token}/{method}"

WHO = {
    "portfolio_manager": "Marco · Portfolio Manager",
    "market_scanner": "Sofia · Market Scanner",
    "strategy_researcher": "Luca · Strategy Researcher",
    "quant_researcher": "Giulia · Quant Researcher",
    "risk_manager": "Franco · Risk Manager",
    "execution": "Paolo · Execution",
    "auditor": "Elena · Auditor",
    "news_analyst": "Nora · News Analyst",
}
ICON = {"news_alert": "📰", "fill": "💱", "trade": "📒", "veto": "⛔", "kill_switch": "🚨", "alert": "⚠️", "report": "📊"}


def _escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramError(Exception):
    pass


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
        elif "blocked" in desc.lower():
            desc = "Hai bloccato il bot su Telegram: sbloccalo e riprova."
        raise TelegramError(desc)
    return data["result"]


def detect_chat(token: str) -> dict:
    """Trova la chat dell'utente: serve che abbia premuto Avvia (/start) sul bot."""
    me = call(token, "getMe")
    updates = call(token, "getUpdates", {"timeout": 0})
    chats = [u["message"]["chat"] for u in updates if "message" in u and u["message"]["chat"]["type"] == "private"]
    if not chats:
        raise TelegramError(f"Non trovo ancora la tua chat. Su Telegram apri @{me.get('username')}, scrivigli "
                            "un messaggio qualsiasi (es. ciao) e premi di nuovo 'Trova la mia chat'.")
    chat = chats[-1]
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username", "")
    return {"chat_id": str(chat["id"]), "chat_name": name, "bot": me.get("username")}


def send_now(text: str, settings: dict | None = None) -> None:
    s = settings or local_settings.load()
    tg = s["telegram"]
    if not (tg.get("token") and tg.get("chat_id")):
        raise TelegramError("Telegram non configurato")
    call(tg["token"], "sendMessage", {"chat_id": tg["chat_id"], "text": text, "parse_mode": "HTML",
                                      "disable_web_page_preview": True})


class Notifier:
    ALERT_COOLDOWN = 3600      # lo stesso allarme al massimo una volta l'ora

    def __init__(self, store=None):
        self.store = store
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _send(self, text: str) -> None:
        def run():
            try:
                send_now(text)
            except Exception as exc:                  # mai propagare
                if self.store is not None:
                    try:
                        self.store.event("auditor", f"Notifica Telegram non inviata: {exc}", "WARN", "notify_error")
                    except Exception:
                        pass
        threading.Thread(target=run, daemon=True).start()

    def _cooldown_ok(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            if now - self._last.get(key, 0) < self.ALERT_COOLDOWN:
                return False
            self._last[key] = now
            return True

    def on_event(self, agent: str, level: str, kind: str, message: str, payload: dict | None) -> None:
        if kind == "notify_error":
            return
        s = local_settings.load()
        tg, n = s["telegram"], s["notify"]
        if not (tg.get("token") and tg.get("chat_id")):
            return
        who = WHO.get(agent, agent)
        text = None
        if kind in ("fill", "trade") and n.get("trades"):
            text = f"{ICON[kind]} <b>{who}</b>\n{_escape(message)}"
        elif kind == "veto" and n.get("vetoes"):
            authorized = (payload or {}).get("strategy_status") == "PAPER"
            if authorized or n.get("vetoes_all"):
                text = f"{ICON['veto']} <b>{who}</b>\n{_escape(message)}"
        elif kind == "reminder":                                   # promemoria: sempre, se Telegram è collegato
            text = f"🗓️ <b>{who}</b>\n{_escape(message)}"
        elif kind == "news_alert" and n.get("news", True):
            text = f"{ICON['news_alert']} <b>{who}</b>\n{_escape(message)}"
        elif kind == "kill_switch" and n.get("alerts"):
            text = f"{ICON['kill_switch']} <b>{who}</b>\n{_escape(message)}"
        elif (level in ("ERROR", "CRITICAL") or kind == "anomaly") and n.get("alerts"):
            if self._cooldown_ok(f"{kind}|{message[:60]}"):
                text = f"{ICON['alert']} <b>{who}</b>\n{_escape(message)}"
        if text:
            self._send(text)

    def daily_report(self, r: dict) -> None:
        s = local_settings.load()
        if not s["notify"].get("daily_report") or not s["telegram"].get("chat_id"):
            return
        wr = "—" if r.get("win_rate") is None else f"{r['win_rate']:.0%}"
        lines = [
            f"{ICON['report']} <b>Report del {r['day']}</b> (Elena · Auditor)",
            f"Capitale: {r['capitale_iniziale']:.2f} → {r['capitale_finale']:.2f}",
            f"P&amp;L netto: {r['pnl_netto']:+.2f} (commissioni {r['commissioni']:.2f})",
            f"Trade: {r['numero_trade']} · win rate {wr} · max DD {r['max_drawdown']:.2%}",
            f"Veti: {r['opportunita_bloccate']} · anomalie: {r['anomalie']} · errori: {r['errori']}",
            f"Strategie attive: {', '.join(r['strategie_attive']) or 'nessuna'}",
        ]
        self._send("\n".join(lines))

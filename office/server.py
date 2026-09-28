"""Mini server locale per la dashboard (solo sul tuo PC: http://localhost:8765).

Sicurezza delle impostazioni:
  • ascolta solo su 127.0.0.1: non raggiungibile da altri computer;
  • accetta modifiche (POST) solo se l'host è localhost e la richiesta arriva dalla
    dashboard stessa (header dedicato + JSON): un altro sito aperto nel browser non può
    cambiare nulla;
  • il token Telegram non viene mai restituito per intero.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import local_settings, notifier, system
from .config import DASHBOARD_DIR, DB_PATH, load_yaml
from .state import build_state
from .store import Store


def settings_view() -> dict:
    return {
        **local_settings.public(local_settings.load()),
        "autostart": system.autostart_status(),
        "windows": system.is_windows(),
        "promotion": load_yaml("promotion_criteria.yaml"),
    }


def handle_action(path: str, body: dict) -> dict:
    s = local_settings.load()
    if path == "/api/settings/telegram":
        token = (body.get("token") or "").strip()
        if token and ":" not in token:
            raise ValueError("Il token non sembra valido: deve contenere i due punti (es. 123456:ABC...).")
        if token and token != s["telegram"]["token"]:
            s["telegram"] = {"token": token, "chat_id": "", "chat_name": ""}
        if body.get("clear"):
            s["telegram"] = dict(local_settings.DEFAULTS["telegram"])
        local_settings.save(s)
        return {"message": "Token salvato. Ora apri il bot su Telegram, premi AVVIA e poi 'Trova la mia chat'."
                if token else "Impostazioni Telegram aggiornate."}
    if path == "/api/telegram/detect":
        if not s["telegram"]["token"]:
            raise ValueError("Prima incolla e salva il token del bot.")
        found = notifier.detect_chat(s["telegram"]["token"])
        s["telegram"].update(chat_id=found["chat_id"], chat_name=found["chat_name"])
        local_settings.save(s)
        try:
            notifier.send_now(f"👋 Ciao {found['chat_name']}! Collegamento riuscito: da ora l'ufficio ti scrive qui.", s)
            extra = "Ti ho appena scritto su Telegram: controlla il bot."
        except notifier.TelegramError as exc:
            extra = f"Chat salvata, ma il messaggio di benvenuto non è partito: {exc}"
        return {"message": f"Chat trovata: {found['chat_name']} (bot @{found['bot']}). {extra}"}
    if path == "/api/telegram/test":
        notifier.send_now("✅ <b>Crypto Trading Office</b>\nCiao! Da ora ti avviso qui su trade, veti, "
                          "allarmi e report giornaliero.\n— Marco, Sofia, Luca, Giulia, Franco, Paolo, Elena", s)
        return {"message": "Messaggio di prova inviato: controlla Telegram."}
    if path == "/api/settings/notify":
        for key in local_settings.DEFAULTS["notify"]:
            if key in body:
                s["notify"][key] = bool(body[key])
        if "keep_awake" in body:
            s["keep_awake"] = bool(body["keep_awake"])
        local_settings.save(s)
        return {"message": "Preferenze salvate."}
    if path == "/api/settings/autostart":
        st = system.set_autostart(bool(body.get("enabled")))
        return {"message": "Avvio automatico attivato: l'ufficio partirà da solo quando accedi a Windows."
                if st["enabled"] else "Avvio automatico disattivato."}
    raise LookupError(path)


def make_handler(store: Store, port: int):
    allowed_hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data, default=str).encode(), "application/json")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "host non consentito"})
            if self.path.startswith("/api/state"):
                self._send(200, json.dumps(build_state(store), default=str).encode(), "application/json")
            elif self.path.startswith("/api/settings"):
                self._json(200, settings_view())
            elif self.path in ("/", "/index.html"):
                self._send(200, (DASHBOARD_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            else:
                self.send_error(404)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (not self._host_ok() or self.headers.get("X-Office") != "1"
                    or (origin and origin not in allowed_origins)
                    or "application/json" not in (self.headers.get("Content-Type") or "")):
                return self._json(403, {"error": "richiesta non consentita"})
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 10_000)
                body = json.loads(self.rfile.read(length) or b"{}")
                result = handle_action(self.path, body)
                store.event("portfolio_manager", f"Impostazioni: {result['message']}", "INFO", "settings")
                self._json(200, {**result, "settings": settings_view()})
            except LookupError:
                self._json(404, {"error": "azione sconosciuta"})
            except (ValueError, OSError, notifier.TelegramError) as exc:
                self._json(400, {"error": str(exc), "settings": settings_view()})

        def log_message(self, *args):
            pass

    return Handler


class _Server(ThreadingHTTPServer):
    # Su Windows SO_REUSEADDR permetterebbe a un secondo ufficio di usare la stessa porta:
    # lo disattiviamo, così la porta occupata segnala che l'ufficio è già acceso.
    allow_reuse_address = not sys.platform.startswith("win")
    daemon_threads = True


def office_running(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def serve(port: int, background: bool = False) -> ThreadingHTTPServer:
    store = Store(DB_PATH)
    httpd = _Server(("127.0.0.1", port), make_handler(store, port))
    if background:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    else:
        httpd.serve_forever()
    return httpd

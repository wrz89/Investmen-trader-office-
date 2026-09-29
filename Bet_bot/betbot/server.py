"""Server locale della dashboard sportiva (http://localhost:8766, solo sul tuo PC).

Regole di sicurezza:
  • ascolta solo su 127.0.0.1;
  • le modifiche (POST) arrivano solo dalla dashboard stessa (host localhost, header X-Office, JSON);
  • segreti (token, password) mai restituiti al browser.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import local_settings, notifier, system
from .config import DASHBOARD_FILE, DB_PATH, ensure_dirs, load_settings
from .state import build_state
from .store import Store

API_VERSION = 1


def settings_view() -> dict:
    s = load_settings()
    return {"version": API_VERSION, **local_settings.public(local_settings.load()),
            "mode": s.get("mode"), "feed": s["feed"]["provider"], "live_stats": s["feed"].get("live_stats"),
            "execution": (s.get("execution") or {}).get("provider"),
            "reference": s["feed"].get("reference"), "record": bool(s["feed"].get("record")),
            "live_strategies": s.get("live_strategies") or [],
            "autostart": system.autostart_status(), "windows": system.is_windows()}


def handle_action(path: str, body: dict) -> dict:
    s = local_settings.load()
    if path == "/api/settings/telegram":
        if body.get("clear"):
            s["telegram"] = dict(local_settings.DEFAULTS["telegram"])
            local_settings.save(s)
            return {"message": "Bot Telegram scollegato."}
        token = (body.get("token") or "").strip()
        if ":" not in token:
            raise ValueError("Il token non sembra valido: deve contenere i due punti (es. 123456:ABC...).")
        s["telegram"] = {"token": token, "chat_id": "", "chat_name": ""}
        local_settings.save(s)
        return {"message": "Token salvato. Apri il bot su Telegram, premi AVVIA e poi 'Trova la mia chat'."}
    if path == "/api/telegram/detect":
        if not s["telegram"]["token"]:
            raise ValueError("Prima incolla e salva il token del bot.")
        found = notifier.detect_chat(s["telegram"]["token"])
        s["telegram"].update(chat_id=found["chat_id"], chat_name=found["chat_name"])
        local_settings.save(s)
        return {"message": f"Chat trovata: {found['chat_name']} (bot @{found['bot']})."}
    if path == "/api/telegram/test":
        notifier.send_now("🏟️ <b>Sports Betting Office</b>\nCollegamento riuscito: ti scrivo a ogni puntata, "
                          "chiusura e blocco di sicurezza.\n— Carlo, Sara, Davide, Matteo, Giorgia, Bruno, Pietro, Anna, Irene")
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
        return {"message": "Avvio automatico attivato: Bet_bot partirà da solo, ridotto a icona, quando accedi a Windows."
                if st["enabled"] else "Avvio automatico disattivato."}
    if path == "/api/settings/keys":
        for key in ("odds_api_key", "api_football_key"):
            if body.get(key) is not None:
                v = str(body[key]).strip()
                if v and not v.replace("-", "").isalnum():
                    raise ValueError(f"{key}: la chiave contiene caratteri non validi.")
                s[key] = v
        local_settings.save(s)
        return {"message": "Chiavi salvate sul tuo PC. Per usarle imposta il feed in config/sport/settings.yaml e riavvia."}
    if path == "/api/settings/betfair":
        if body.get("clear"):
            s["betfair"] = dict(local_settings.DEFAULTS["betfair"])
            local_settings.save(s)
            return {"message": "Credenziali Betfair rimosse dall'ufficio."}
        bf = s["betfair"]
        for k in ("app_key", "username", "password", "cert_file", "key_file"):
            if body.get(k):
                bf[k] = str(body[k]).strip()
        bf.update(verified=False, test_done=False, live_enabled=False)
        local_settings.save(s)
        return {"message": "Credenziali salvate sul tuo PC. Ora premi 'Verifica il conto'."}
    if path == "/api/betfair/verify":
        from .feeds.betfair import BetfairClient
        c = BetfairClient(s["betfair"])
        c.login()
        funds = c.account_funds()
        s["betfair"]["verified"] = True
        local_settings.save(s)
        return {"message": f"Accesso riuscito. Saldo disponibile: {funds.get('availableToBetBalance', 0):.2f} €. "
                           "Ora fai l'ordine di prova (non costa nulla: viene annullato subito)."}
    if path == "/api/betfair/test_order":
        if not s["betfair"].get("verified"):
            raise ValueError("Prima verifica il conto.")
        import uuid
        from .feeds.betfair import SOCCER, BetfairClient
        c = BetfairClient(s["betfair"])
        cat = [m for m in c.catalogue(SOCCER, "MATCH_ODDS", 72, None, 10, lookback_hours=0) if m.get("runners")]
        if not cat:
            raise ValueError("Nessuna partita di calcio disponibile ora per la prova: riprova più tardi.")
        m = cat[0]
        ref = f"prova{uuid.uuid4().hex[:12]}"
        # back 2 € a quota 1000: non si abbina mai (vincita potenziale 2.000 €, sotto il limite ADM di 10.000 €)
        r = c.place(m["marketId"], m["runners"][0]["selectionId"], "BACK", 1000.0, 2.0, fill_or_kill=False, order_ref=ref)
        c.cancel(m["marketId"], r["bet_id"])
        left = [o for o in c.current_orders(order_refs=[ref]) if o.get("status") == "EXECUTABLE"]
        if left:
            raise ValueError("L'ordine di prova è ancora aperto su Betfair: annullalo da 'Le mie scommesse' e riprova.")
        s["betfair"]["test_done"] = True
        local_settings.save(s)
        return {"message": f"Ordine di prova (2 € a quota 1000 su {m['event']['name']}) accettato e annullato. "
                           "Il collegamento per le puntate funziona."}
    if path == "/api/settings/betfair_live":
        on = bool(body.get("enabled"))
        bf = s["betfair"]
        if on and not (bf.get("verified") and bf.get("test_done")):
            raise ValueError("Per accendere le puntate reali servono conto verificato e ordine di prova riuscito.")
        bf["live_enabled"] = on
        local_settings.save(s)
        return {"message": "PUNTATE REALI ACCESE per le strategie in live_strategies (con mode: live e execution: betfair)."
                if on else "Puntate reali spente: si torna in paper."}
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

        def do_GET(self):
            if self.headers.get("Host", "") not in allowed_hosts:
                return self._json(403, {"error": "host non consentito"})
            if self.path.startswith("/api/state"):
                self._json(200, build_state(store))
            elif self.path.startswith("/api/settings"):
                self._json(200, settings_view())
            elif self.path in ("/", "/index.html"):
                self._send(200, DASHBOARD_FILE.read_bytes(), "text/html; charset=utf-8")
            else:
                self.send_error(404)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (self.headers.get("Host", "") not in allowed_hosts or self.headers.get("X-Office") != "1"
                    or (origin and origin not in allowed_origins)
                    or "application/json" not in (self.headers.get("Content-Type") or "")):
                return self._json(403, {"error": "richiesta non consentita"})
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 10_000)
                body = json.loads(self.rfile.read(length) or b"{}")
                result = handle_action(self.path, body)
                store.event("direttore", f"Impostazioni: {result['message']}", "INFO", "settings")
                self._json(200, {**result, "settings": settings_view()})
            except LookupError:
                self._json(404, {"error": "azione sconosciuta"})
            except Exception as exc:
                self._json(400, {"error": str(exc), "settings": settings_view()})

        def log_message(self, *args):
            pass

    return Handler


class _Server(ThreadingHTTPServer):
    allow_reuse_address = not sys.platform.startswith("win")
    daemon_threads = True


def office_running(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def serve(port: int, background: bool = False, db_path=None) -> ThreadingHTTPServer:
    ensure_dirs()
    store = Store(db_path or DB_PATH)
    httpd = _Server(("127.0.0.1", port), make_handler(store, port))
    if background:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    else:
        httpd.serve_forever()
    return httpd

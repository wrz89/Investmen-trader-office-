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


API_VERSION = 2          # la pagina controlla di parlare con un ufficio aggiornato


def settings_view() -> dict:
    return {
        "version": API_VERSION,
        **local_settings.public(local_settings.load()),
        "autostart": system.autostart_status(),
        "windows": system.is_windows(),
        "promotion": load_yaml("promotion_criteria.yaml"),
    }


def handle_action(path: str, body: dict, store: Store | None = None) -> dict:
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
    if path.startswith("/api/settings/bybit") or path.startswith("/api/bybit/"):
        try:
            return _bybit_action(path, body, s, store)
        except (ValueError, LookupError):
            raise
        except Exception as exc:                       # errori di rete o di Bybit: messaggio leggibile
            text = str(exc)
            if "10002" in text:
                raise ValueError("L'orologio del PC non coincide con quello di Bybit. Sincronizzalo: Impostazioni di "
                                 "Windows → Ora e lingua → Data e ora → 'Sincronizza ora', poi riprova.") from exc
            if "10010" in text or "IP" in text and "whitelist" in text.lower():
                raise ValueError("Bybit rifiuta l'IP di questo PC: controlla che nella chiave ci sia l'IP mostrato "
                                 "da 'Mostra il mio IP'.") from exc
            if "10003" in text or "10004" in text:
                raise ValueError("Bybit non riconosce chiave o secret: ricopiali (attenzione a non invertirli).") from exc
            raise ValueError(f"Bybit ha risposto con un errore: {text}") from exc
    raise LookupError(path)


def _refresh_balance(live, store: Store | None) -> None:
    """Aggiorna subito i saldi reali mostrati in cima alla dashboard."""
    if store is None:
        return
    try:
        prices = (store.get("accumulation_quote") or {}).get("prices") or {}
        store.set("live_balance", live.balances(prices))
    except Exception:
        pass


def _bybit_action(path: str, body: dict, s: dict, store: Store | None) -> dict:
    from .accumulation import allocation_of, record_buy
    from .config import load_settings
    from .live_exchange import LiveExchange, fill_of
    b = s["bybit"]
    allowed = list(allocation_of(load_yaml("accumulation.yaml")))
    if path == "/api/settings/bybit":
        if body.get("clear"):
            s["bybit"] = dict(local_settings.DEFAULTS["bybit"])
            local_settings.save(s)
            return {"message": "Chiave di Bybit rimossa dall'ufficio. Ricordati di cancellarla anche su Bybit."}
        import re
        key = re.sub(r"\s", "", body.get("key") or "")          # spazi o a capo presi copiando
        secret = re.sub(r"\s", "", body.get("secret") or "")
        if not (key.isalnum() and 10 <= len(key) <= 64):
            raise ValueError(f"La API key non sembra valida ({len(key)} caratteri): ricopiala da Bybit, solo lettere e numeri.")
        if not (secret.isalnum() and 20 <= len(secret) <= 128):
            raise ValueError(f"Il secret non sembra valido ({len(secret)} caratteri): ricopialo da Bybit. "
                             "Attenzione a non invertire key e secret: il secret è quello più lungo.")
        s["bybit"] = {**local_settings.DEFAULTS["bybit"], "key": key, "secret": secret}
        local_settings.save(s)
        return {"message": "Chiave salvata sul tuo PC. Ora premi 'Verifica la chiave'."}
    if path == "/api/bybit/ip":
        import requests
        ip = requests.get("https://api.ipify.org?format=json", timeout=8).json()["ip"]
        return {"message": f"L'IP pubblico del tuo PC è {ip}: incollalo su Bybit in 'Solo IP consentiti'.", "ip": ip}
    live = LiveExchange(load_settings(), allowed)
    if not live.configured:
        raise ValueError("Prima incolla e salva chiave e segreto di Bybit.")
    if path == "/api/bybit/verify":
        v = live.verify()
        b.update(verified=v["ok"], problems=v["problems"], ips=v["ips"])
        if not v["ok"]:
            b.update(live=False, test_done=False)
        local_settings.save(s)
        if not v["ok"]:
            raise ValueError("Chiave NON accettata: " + "; ".join(v["problems"]) + ".")
        b["transfer"] = v["transfer"]
        _refresh_balance(live, store)
        local_settings.save(s)
        move = ("Posso spostare da solo gli euro dal conto Fondi al conto di trading." if v["transfer"] else
                "Gli euro nel conto Fondi vanno spostati a mano nel conto di trading (o aggiungi alla chiave il "
                "permesso 'trasferimento tra conti').")
        return {"message": f"Chiave verificata: solo trading spot, prelievi disattivati, IP {', '.join(v['ips'])}. "
                           f"Euro: {v['eur_free']:.2f} € nel conto di trading, {v['eur_funding']:.2f} € nel conto Fondi. "
                           f"{move} Ora fai l'ordine di prova."}
    if path == "/api/bybit/test_order":
        if not b.get("verified"):
            raise ValueError("Prima verifica la chiave.")
        if b.get("test_done"):
            return {"message": "Ordine di prova già fatto: puoi accendere gli acquisti reali."}
        sym, eur = allowed[0], 5.0
        if live.ensure_eur(eur) < eur:
            raise ValueError("Servono almeno 5 € nel conto di trading (Unificato). Spostali dal conto Fondi (Asset → "
                             "Trasferisci), oppure dai alla chiave il permesso 'trasferimento tra conti' e lo faccio io.")
        oid = live.market_buy(sym, eur)
        import time as _t
        for _ in range(5):
            o = live.order(sym, oid)
            if o.get("status") in ("closed", "canceled", "rejected", "expired"):
                break
            _t.sleep(1)
        f = fill_of(o, sym)
        if f["qty"] <= 0:
            raise ValueError(f"L'ordine di prova non è stato eseguito (stato: {o.get('status')}).")
        if store is not None:
            record_buy(store, "prova", "live", "diretta (mercato)", sym, f["eur"], f["eur"] / f["qty"], f["qty"],
                       f["fee_eur"], f"ordine di prova · ordine Bybit {oid}")
            store.event("execution", f"ORDINE DI PROVA con soldi veri: {f['qty']:.8f} {sym.split('/')[0]} per "
                                     f"{f['eur']:.2f} € (commissione {f['fee_eur']:.4f} €). Collegamento funzionante.",
                        "INFO", "fill")
        b["test_done"] = True
        _refresh_balance(live, store)
        local_settings.save(s)
        return {"message": f"Ordine di prova riuscito: {f['qty']:.8f} {sym.split('/')[0]} per {f['eur']:.2f} €. "
                           "Ora puoi accendere gli acquisti reali."}
    if path == "/api/settings/bybit_live":
        on = bool(body.get("enabled"))
        if on and not (b.get("verified") and b.get("test_done")):
            raise ValueError("Per accendere gli acquisti reali servono chiave verificata e ordine di prova riuscito.")
        b["live"] = on
        local_settings.save(s)
        return {"message": "ACQUISTI REALI ACCESI: dal prossimo acquisto dovuto l'ufficio compra con i tuoi soldi su Bybit."
                if on else "Acquisti reali spenti: l'accumulo torna in prova (paper)."}
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
                result = handle_action(self.path, body, store)
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

"""Server locale della dashboard sportiva (http://localhost:8766, solo sul tuo PC).

Regole di sicurezza:
  • ascolta solo su 127.0.0.1; con `dashboard_lan: true` in runtime/impostazioni.yaml ascolta anche sulla rete di casa,
    ma dal telefono si può solo guardare (pagina e /api/state, host = IP privato);
  • le modifiche (POST) e le impostazioni arrivano solo dal PC stesso (client 127.0.0.1, host localhost,
    header X-Office, JSON);
  • segreti (token, password) mai restituiti al browser.
"""
from __future__ import annotations

import errno
import ipaddress
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import local_settings, notifier, system
from pathlib import Path

from .config import DASHBOARD_FILE, DB_LIVE_PATH, DB_PATH, ensure_dirs, load_settings
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
        return {"message": "Chiavi salvate sul tuo PC. Per usarle imposta il feed in runtime/impostazioni.yaml "
                           "(non in config/settings.yaml, che gli aggiornamenti sovrascrivono) e riavvia."}
    if path == "/api/settings/betfair":
        if body.get("clear"):
            s["betfair"] = dict(local_settings.DEFAULTS["betfair"])
            local_settings.save(s)
            return {"message": "Credenziali Betfair rimosse dall'ufficio."}
        bf = s["betfair"]
        for k in ("app_key", "username", "password", "cert_file", "key_file"):
            if body.get(k):
                v = str(body[k]).strip()
                if k in ("cert_file", "key_file"):
                    v = v.strip('"').strip()           # "Copia come percorso" di Windows aggiunge le virgolette
                    if not Path(v).expanduser().is_file():
                        raise ValueError(f"File del certificato non trovato: {v}. Controlla il percorso "
                                         "(es. C:\\certs\\client-2048.crt).")
                bf[k] = v
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
        from .feeds.betfair import BetfairClient
        from .live_switch import test_order
        event = test_order(BetfairClient(s["betfair"]))
        s["betfair"]["test_done"] = True
        local_settings.save(s)
        return {"message": f"Ordine di prova (2 € a quota 1000 su {event}) accettato e annullato. "
                           "Il collegamento per le puntate funziona."}
    if path == "/api/live/on":
        from . import live_switch
        said: list[str] = []
        if not live_switch.enable(ask=lambda q: str(body.get("confirm") or ""), out=said.append):
            raise ValueError(next((x for x in reversed(said) if x.strip()), "Live non acceso."))
        live_switch.request_restart()
        return {"message": "PUNTATE REALI ACCESE (" + ", ".join(live_switch.LIVE_STRATEGIES) + "). Il bot si riavvia da "
                           "solo in modalità LIVE: tra un minuto ricarica la pagina."}
    if path == "/api/live/off":
        from . import live_switch
        live_switch.disable(out=lambda *a: None)
        live_switch.request_restart()
        return {"message": "Puntate reali spente. Il bot si riavvia da solo in PAPER: tra un minuto ricarica la pagina. "
                           "Le puntate vere già aperte si chiudono da sole su Betfair."}
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


def _lan_host(host: str, port: int) -> bool:
    """Host di un IP privato della rete di casa (192.168.x.x, 10.x.x.x, 172.16-31.x.x) sulla porta giusta."""
    name, _, p = host.rpartition(":")
    if p != str(port):
        return False
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_private and not ip.is_loopback


def lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.1.1", 9))          # nessun pacchetto parte: serve solo a sapere l'interfaccia di casa
            return s.getsockname()[0]
    except OSError:
        return None


def make_handler(store: Store, port: int, lan: bool = False, office: bool = False):
    """office=True solo nel processo di `betbot.py avvia`: /api/ping dice se sulla porta c'è l'ufficio vero
    (e non solo la dashboard di una simulazione o di un replay)."""
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

        def _from_pc(self) -> bool:
            return self.client_address[0] in ("127.0.0.1", "::1") and self.headers.get("Host", "") in allowed_hosts

        def do_GET(self):
            host = self.headers.get("Host", "")
            if not (self._from_pc() or (lan and _lan_host(host, port))):
                return self._json(403, {"error": "host non consentito"})
            if self.path.startswith("/api/ping"):
                if not self._from_pc():
                    return self._json(403, {"error": "solo dal PC"})
                self._json(200, {"office": office})
            elif self.path.startswith("/api/state"):
                self._json(200, build_state(store))
            elif self.path.startswith("/api/leo/pronostici"):
                from . import leo_pronostico
                self._json(200, leo_pronostico.summary(store))
            elif self.path.startswith("/api/calendario"):
                from urllib.parse import parse_qs, urlparse
                from . import calendario
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    if u.path == "/api/calendario/sport":
                        data = {"sports": calendario.sports_list()}
                    elif u.path == "/api/calendario/competizioni":
                        data = calendario.competitions(q.get("sport", "soccer"), store)
                    else:
                        data = calendario.matches(q.get("sport", "soccer"), q.get("comp") or None, store)
                except Exception as exc:
                    data = {"source": "none", "matches": [], "items": [], "note": f"Calendario non disponibile: {exc}"}
                self._json(200, data)
            elif self.path.startswith("/api/settings"):
                if not self._from_pc():
                    return self._json(403, {"error": "le impostazioni si vedono solo dal PC"})
                self._json(200, settings_view())
            elif self.path in ("/", "/index.html"):
                self._send(200, DASHBOARD_FILE.read_bytes(), "text/html; charset=utf-8")
            elif self.path.startswith("/vendor/") and self.path.split("?")[0].endswith(".js"):
                # librerie 3D salvate in locale: l'ufficio 3D funziona anche senza internet
                # solo file che stanno DAVVERO in vendor/: niente "C:\…", "\\host\…", "C:x.js" o "..\" su Windows
                name = self.path.split("?")[0].rsplit("/", 1)[-1]
                vendor = (DASHBOARD_FILE.parent / "vendor").resolve()
                try:
                    f = (vendor / name).resolve()
                except (OSError, ValueError):
                    f = None
                if f is not None and f.parent == vendor and f.is_file():
                    self._send(200, f.read_bytes(), "application/javascript; charset=utf-8")
                else:
                    self.send_error(404)
            else:
                self.send_error(404)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (not self._from_pc() or self.headers.get("X-Office") != "1"
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


def port_in_use(port: int) -> bool:
    """Qualcuno ascolta sulla porta (l'ufficio, una dashboard di simulazione/replay o un altro programma)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def office_running(port: int) -> bool:
    """True solo se sulla porta c'è l'ufficio vero (`betbot.py avvia`): la sola dashboard di `simula.bat` o di
    `dashboard --replay` non conta, altrimenti `avvia` uscirebbe credendo il bot già acceso."""
    if not port_in_use(port):
        return False
    import http.client
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/ping", headers={"Host": f"localhost:{port}"})
        r = conn.getresponse()
        data = json.loads(r.read() or b"{}") if r.status == 200 else {}
        conn.close()
        return data.get("office") is True
    except (OSError, ValueError, http.client.HTTPException):
        return False


def is_addr_in_use(exc: OSError) -> bool:
    """Porta già occupata (Linux 98, macOS 48, Windows 10048), da distinguere da una porta vietata (WinError 10013)."""
    return exc.errno in (errno.EADDRINUSE, 10048) or getattr(exc, "winerror", None) == 10048


def default_db_path(settings: dict | None = None) -> Path:
    """Il database che la dashboard deve mostrare: in live quello dei soldi veri, come fa SportOffice."""
    s = settings if settings is not None else load_settings()
    return DB_LIVE_PATH if s.get("mode") == "live" else DB_PATH


def serve(port: int, background: bool = False, db_path=None, office: bool = False) -> ThreadingHTTPServer:
    ensure_dirs()
    settings = load_settings()
    store = Store(db_path or default_db_path(settings))
    lan = bool(settings.get("dashboard_lan"))
    httpd = _Server(("0.0.0.0" if lan else "127.0.0.1", port), make_handler(store, port, lan, office))
    if lan:
        ip = lan_address()
        print(f"Dal telefono (stessa rete Wi-Fi, solo lettura): http://{ip or '<IP del PC>'}:{port}")
    if background:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    else:
        httpd.serve_forever()
    return httpd

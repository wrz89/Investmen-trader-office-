"""Perline - progettazione schemi per perline (peyote / telaio).

Avvia un piccolo server locale (solo 127.0.0.1) e apre l'app nel browser.
Nessuna libreria esterna: serve solo Python 3.8+.
I progetti restano nella cartella "dati" accanto all'app e non vengono
toccati dagli aggiornamenti.
"""
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

VERSIONE = "0.1.0"
PORTA_PREFERITA = 8765

if getattr(sys, "frozen", False):  # eseguibile PyInstaller
    WEB_DIR = Path(sys._MEIPASS) / "web"
    DATI_DIR = Path(sys.executable).resolve().parent / "dati"
else:
    WEB_DIR = Path(__file__).resolve().parent / "web"
    DATI_DIR = Path(__file__).resolve().parent / "dati"

TIPI = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png"}
MAX_BYTES = 5 * 1024 * 1024


def nome_sicuro(nome):
    """Nome file ammesso per un progetto, oppure None."""
    nome = unquote(nome).strip()
    if not nome or len(nome) > 80 or not re.fullmatch(r"[\w .()\-]+", nome) or nome.startswith("."):
        return None
    return nome


def crea_server(porta, dati_dir=None, web_dir=None):
    dati = Path(dati_dir or DATI_DIR)
    web = Path(web_dir or WEB_DIR)
    dati.mkdir(parents=True, exist_ok=True)

    class Gestore(BaseHTTPRequestHandler):
        server_version = "Perline/" + VERSIONE

        def log_message(self, *args):
            pass

        def _invia(self, codice, corpo=b"", tipo="application/json; charset=utf-8"):
            self.send_response(codice)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, codice, oggetto):
            self._invia(codice, json.dumps(oggetto, ensure_ascii=False).encode("utf-8"))

        def _file_progetto(self, percorso):
            nome = nome_sicuro(percorso[len("/api/progetti/"):])
            return (dati / (nome + ".json")) if nome else None

        def do_GET(self):
            percorso = urlparse(self.path).path
            if percorso == "/api/versione":
                return self._json(200, {"versione": VERSIONE})
            if percorso == "/api/progetti":
                nomi = sorted(p.stem for p in dati.glob("*.json"))
                return self._json(200, nomi)
            if percorso.startswith("/api/progetti/"):
                f = self._file_progetto(percorso)
                if f is None:
                    return self._json(400, {"errore": "nome non valido"})
                if not f.is_file():
                    return self._json(404, {"errore": "non trovato"})
                return self._invia(200, f.read_bytes())
            rel = "index.html" if percorso in ("/", "") else percorso.lstrip("/")
            f = (web / rel).resolve()
            if web.resolve() not in f.parents or not f.is_file():
                return self._json(404, {"errore": "non trovato"})
            self._invia(200, f.read_bytes(), TIPI.get(f.suffix, "application/octet-stream"))

        def do_PUT(self):
            percorso = urlparse(self.path).path
            if not percorso.startswith("/api/progetti/"):
                return self._json(404, {"errore": "non trovato"})
            f = self._file_progetto(percorso)
            if f is None:
                return self._json(400, {"errore": "nome non valido"})
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BYTES:
                return self._json(400, {"errore": "dimensione non valida"})
            corpo = self.rfile.read(n)
            try:
                json.loads(corpo)
            except ValueError:
                return self._json(400, {"errore": "JSON non valido"})
            f.write_bytes(corpo)
            self._json(200, {"ok": True})

        def do_DELETE(self):
            percorso = urlparse(self.path).path
            f = self._file_progetto(percorso) if percorso.startswith("/api/progetti/") else None
            if f is None:
                return self._json(400, {"errore": "nome non valido"})
            if f.is_file():
                f.unlink()
            self._json(200, {"ok": True})

        def do_POST(self):
            if urlparse(self.path).path == "/api/esci":
                self._json(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self._json(404, {"errore": "non trovato"})

    return ThreadingHTTPServer(("127.0.0.1", porta), Gestore)


def main():
    server = None
    for porta in (PORTA_PREFERITA, 0):
        try:
            server = crea_server(porta)
            break
        except OSError:
            continue
    if server is None:
        print("Impossibile avviare il server locale.")
        return 1
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    print("Perline %s attivo su %s" % (VERSIONE, url))
    print("Progetti salvati in: %s" % DATI_DIR)
    print("Lascia aperta questa finestra; usa il pulsante 'Chiudi app' oppure Ctrl+C per fermare.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

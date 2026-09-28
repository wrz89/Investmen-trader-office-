"""Mini server locale per la dashboard (solo sul tuo PC: http://localhost:8765)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import DASHBOARD_DIR, DB_PATH
from .state import build_state
from .store import Store


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/state"):
                body = json.dumps(build_state(store), default=str).encode()
                ctype = "application/json"
            elif self.path in ("/", "/index.html"):
                body = (DASHBOARD_DIR / "index.html").read_bytes()
                ctype = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def serve(port: int, background: bool = False) -> ThreadingHTTPServer:
    store = Store(DB_PATH)
    # solo 127.0.0.1: la dashboard non è raggiungibile da altri computer
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))
    if background:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    else:
        httpd.serve_forever()
    return httpd

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import perline  # noqa: E402


def _avvia(tmp_path):
    srv = perline.crea_server(0, dati_dir=tmp_path / "dati")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def _chiama(metodo, url, corpo=None):
    req = urllib.request.Request(url, method=metodo, data=corpo)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_salva_leggi_elenca_elimina(tmp_path):
    srv, base = _avvia(tmp_path)
    try:
        prog = json.dumps({"w": 3, "celle": [[0, 1, 0]]}).encode()
        assert _chiama("PUT", base + "/api/progetti/Rombi%201", prog)[0] == 200
        assert json.loads(_chiama("GET", base + "/api/progetti")[1]) == ["Rombi 1"]
        assert json.loads(_chiama("GET", base + "/api/progetti/Rombi%201")[1])["w"] == 3
        assert _chiama("DELETE", base + "/api/progetti/Rombi%201")[0] == 200
        assert json.loads(_chiama("GET", base + "/api/progetti")[1]) == []
    finally:
        srv.shutdown()


def test_nomi_pericolosi_rifiutati(tmp_path):
    srv, base = _avvia(tmp_path)
    try:
        for nome in ("..%2F..%2Fx", "%2E%2E", ".nascosto", "a%2Fb"):
            assert _chiama("PUT", base + "/api/progetti/" + nome, b"{}")[0] in (400, 404)
        assert not any(tmp_path.glob("*.json"))
        assert _chiama("PUT", base + "/api/progetti/ok", b"non json")[0] == 400
    finally:
        srv.shutdown()


def test_pagina_e_file_statici(tmp_path):
    srv, base = _avvia(tmp_path)
    try:
        st, corpo = _chiama("GET", base + "/")
        assert st == 200 and b"<canvas" in corpo
        assert _chiama("GET", base + "/..%2Fperline.py")[0] == 404
    finally:
        srv.shutdown()

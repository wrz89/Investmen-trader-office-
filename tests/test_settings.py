"""Impostazioni dal browser, notifiche e avvio automatico."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from office import local_settings, notifier, server, system


@pytest.fixture()
def tmp_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(local_settings, "PATH", tmp_path / "local_settings.json")
    return tmp_path


def test_token_never_exposed(tmp_settings):
    s = local_settings.load()
    s["telegram"]["token"] = "123456789:ABCDEFGHIJKLMNOPQRSTUV"
    s["telegram"]["chat_id"] = "42"
    local_settings.save(s)
    pub = local_settings.public(local_settings.load())
    assert "ABCDEFGHIJKLMNOPQ" not in json.dumps(pub)
    assert pub["telegram"]["configured"]


def test_notifier_filters(tmp_settings, monkeypatch):
    s = local_settings.load()
    s["telegram"].update(token="1:x", chat_id="42")
    local_settings.save(s)
    sent = []
    n = notifier.Notifier()
    monkeypatch.setattr(n, "_send", sent.append)
    n.on_event("risk_manager", "INFO", "veto", "BLOCK x", {"strategy_status": "REJECTED"})
    assert not sent                                     # veto su strategia rifiutata: silenzio
    n.on_event("risk_manager", "INFO", "veto", "BLOCK y", {"strategy_status": "PAPER"})
    n.on_event("execution", "INFO", "fill", "FILL BUY", None)
    n.on_event("market_scanner", "WARN", "anomaly", "dati vecchi", None)
    n.on_event("market_scanner", "WARN", "anomaly", "dati vecchi", None)   # ripetuto: cooldown
    assert len(sent) == 3 and "Franco" in sent[0]


def test_settings_actions(tmp_settings):
    with pytest.raises(ValueError):
        server.handle_action("/api/settings/telegram", {"token": "senza-due-punti"})
    server.handle_action("/api/settings/telegram", {"token": "1:abc"})
    server.handle_action("/api/settings/notify", {"vetoes_all": True, "keep_awake": False})
    s = local_settings.load()
    assert s["telegram"]["token"] == "1:abc" and s["notify"]["vetoes_all"] and not s["keep_awake"]


def test_post_requires_dashboard_headers(tmp_settings, tmp_path):
    from office.store import Store
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(Store(tmp_path / "t.db"), 0))
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = server.make_handler(Store(tmp_path / "t.db"), port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/api/settings/notify"

    def post(headers):
        req = urllib.request.Request(url, data=b'{"trades": false}', headers=headers, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code

    assert post({"Content-Type": "text/plain"}) == 403                     # form da un altro sito
    assert post({"Content-Type": "application/json", "X-Office": "1",
                 "Origin": "https://sito-malevolo.example"}) == 403
    assert post({"Content-Type": "application/json", "X-Office": "1"}) == 200
    assert local_settings.load()["notify"]["trades"] is False
    httpd.shutdown()


def test_startup_script_runs_office_minimized():
    vbs = system.startup_script()
    assert "ufficio.py" in vbs and "avvia --no-browser" in vbs and ", 7, False" in vbs

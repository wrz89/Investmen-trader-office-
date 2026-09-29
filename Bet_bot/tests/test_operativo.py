"""Test del gruppo operativo: comandi di betbot.py, dashboard (database giusto, porta, vendor/), segreti,
Telegram, avvio automatico, simulazione, file .bat."""
import asyncio
import base64
import importlib.util
import json
import socket
import sqlite3
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── strumenti comuni ─────────────────────────────────────────
def _cli():
    """betbot.py (il file dei comandi) caricato come modulo: il nome `betbot` è già del pacchetto."""
    spec = importlib.util.spec_from_file_location("betbot_cli", ROOT / "betbot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cli(monkeypatch, *args):
    cli = _cli()
    monkeypatch.setattr(cli, "_write_pid", lambda: None)
    monkeypatch.setattr(sys, "argv", ["betbot.py", *args])
    return cli.main()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port: int, path: str):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": f"localhost:{port}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def _settings(**over):
    from betbot.config import load_settings
    s = load_settings()
    s.update(over)
    return s


@pytest.fixture
def servers():
    started = []
    yield started
    for h in started:
        h.shutdown()
        h.server_close()


@pytest.fixture
def fake_dpapi(monkeypatch, tmp_path):
    """DPAPI simulato (i test girano su Linux, dove i segreti restano in chiaro)."""
    from betbot import local_settings, secrets
    monkeypatch.setattr(local_settings, "PATH", tmp_path / "local_settings.json")
    monkeypatch.setattr(secrets, "available", lambda: True)
    monkeypatch.setattr(secrets, "protect", lambda t: t if not t or t.startswith("dpapi:")
                        else "dpapi:" + base64.b64encode(b"BLOB" + t.encode()).decode())
    monkeypatch.setattr(secrets, "unprotect", lambda v: v if not v or not v.startswith("dpapi:")
                        else base64.b64decode(v[6:])[4:].decode())
    return local_settings


# ── n.26: la chiave di The Odds API arriva al feed decifrata ─────────────
def test_odds_api_key_is_decrypted_before_reaching_the_feed(fake_dpapi, monkeypatch):
    from betbot import config
    from betbot.feeds.odds_api import OddsApiFeed
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    s = fake_dpapi.load()
    s["odds_api_key"] = "abc123def456"
    fake_dpapi.save(s)
    assert "dpapi:" in fake_dpapi.PATH.read_text(encoding="utf-8")        # sul disco è cifrata
    assert config.api_key() == "abc123def456"
    assert OddsApiFeed(config.load_settings()).key == "abc123def456"


# ── n.24/27: in live la dashboard legge runtime/betbot_live.db ─────────────
def test_dashboard_without_options_shows_live_db_in_live_mode(monkeypatch, tmp_path, servers):
    from betbot import server
    from betbot.store import Store
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "betbot.db")
    monkeypatch.setattr(server, "DB_LIVE_PATH", tmp_path / "betbot_live.db")
    monkeypatch.setattr(server, "load_settings", lambda: _settings(mode="live"))
    Store(tmp_path / "betbot_live.db").set("kill_switch", "saldo Betfair che non torna")
    Store(tmp_path / "betbot.db").set("kill_switch", None)
    port = _free_port()
    servers.append(server.serve(port, background=True))
    code, body = _get(port, "/api/state")
    assert code == 200
    assert "saldo Betfair che non torna" in body.decode()
    assert server.default_db_path(_settings(mode="paper")) == tmp_path / "betbot.db"


class _FakeStore:
    path = "/percorso/betbot_live.db"


class _FakeOffice:
    """SportOffice finto per provare `avvia` senza feed né database."""
    instances = []

    def __init__(self, connect_feed=True, **kw):
        self.store = _FakeStore()
        self.settings = {"mode": "live", "feed": {"provider": "betfair"}}
        self.shutdown_called = self.report_called = False
        self.auditor = self
        _FakeOffice.instances.append(self)

    async def run_forever(self):
        raise KeyboardInterrupt                   # l'utente preme CTRL+C

    async def shutdown(self, timeout_s=120.0):
        self.shutdown_called = True

    def daily_report(self, day=None):
        self.report_called = True
        return {}


def test_avvia_serves_office_db_and_ctrl_c_runs_orderly_shutdown(monkeypatch):
    from betbot import core, server
    seen = {}
    monkeypatch.setattr(server, "office_running", lambda port: False)
    monkeypatch.setattr(server, "serve", lambda port, **kw: seen.update(kw))
    monkeypatch.setattr(core, "SportOffice", _FakeOffice)
    _FakeOffice.instances.clear()
    assert _run_cli(monkeypatch, "avvia", "--no-browser") == 0
    office = _FakeOffice.instances[-1]
    assert seen["db_path"] == "/percorso/betbot_live.db"     # n.24/27: stesso database dell'ufficio
    assert seen["office"] is True
    assert office.shutdown_called and office.report_called    # n.37: CTRL+C = spegnimento ordinato


# ── n.32: la porta occupata da una sola dashboard non è "bot già acceso" ─────────────
def test_office_running_only_for_the_real_office(monkeypatch, tmp_path, servers):
    from betbot import server
    p1, p2 = _free_port(), _free_port()
    servers.append(server.serve(p1, background=True, db_path=tmp_path / "sim.db"))               # simula.bat
    servers.append(server.serve(p2, background=True, db_path=tmp_path / "o.db", office=True))    # avvia
    assert server.port_in_use(p1) and not server.office_running(p1)
    assert server.office_running(p2)
    assert not server.office_running(_free_port())


def test_avvia_exits_with_error_when_port_is_taken(monkeypatch, tmp_path):
    from betbot import config, core, server
    port = _free_port()
    real = _settings(dashboard_port=port)
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: real)
    monkeypatch.setattr(core, "SportOffice", _FakeOffice)
    monkeypatch.setattr(server, "Store", lambda path: None)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", port))
        busy.listen()
        assert _run_cli(monkeypatch, "avvia", "--no-browser") == 1      # prima: "già acceso", codice 0


# ── n.40: `ferma` a bot spento lascia comunque la richiesta per avvia.bat ─────────────
def test_ferma_creates_stop_file_even_when_office_is_off(monkeypatch, tmp_path):
    from betbot import config, server
    stop = tmp_path / "ferma.richiesta"
    monkeypatch.setattr(config, "STOP_FILE", stop)
    monkeypatch.setattr(server, "office_running", lambda port: False)
    assert _run_cli(monkeypatch, "ferma") == 0
    assert stop.exists()


# ── n.30: il token Telegram non finisce nei messaggi d'errore ─────────────
def test_telegram_error_does_not_contain_token(monkeypatch):
    from betbot import notifier
    port = _free_port()                                                    # porta chiusa: connessione rifiutata
    monkeypatch.setattr(notifier, "API", f"http://127.0.0.1:{port}/bot{{token}}/{{method}}")
    token = "123456:TOKEN-SEGRETO"
    with pytest.raises(notifier.TelegramError) as ei:
        notifier.call(token, "sendMessage", {"text": "x"}, timeout=2)
    assert "TOKEN-SEGRETO" not in str(ei.value) and "***" in str(ei.value)
    assert ei.value.__cause__ is None
    assert notifier.hide_secret("url /bot123456%3ATOKEN-SEGRETO/x", token) == "url /bot***/x"


# ── n.36: un comando Telegram che fallisce non spegne l'ascolto ─────────────
@pytest.fixture
def office(tmp_path, monkeypatch):
    from betbot import local_settings
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS})
    from betbot.core import SportOffice
    from betbot.feeds.mock import MockFeed
    st = _settings()
    st["feed"]["mock"]["seed"] = 5
    feed = MockFeed(st)
    feed.speed = 1.0
    return SportOffice(db_path=tmp_path / "o.db", feed=feed)


def test_telegram_loop_survives_a_failing_command(office, monkeypatch):
    from betbot import notifier
    from betbot.telegram_bot import TelegramCommands
    cmds = TelegramCommands(office)
    chat = {"id": 7}
    batches = [[{"update_id": 10, "message": {"chat": chat, "text": "/oggi"}},
                {"update_id": 11, "message": {"chat": chat, "text": "/pausa ²"}},
                {"update_id": 12, "message": {"chat": chat, "text": "/stop"}}]]
    replies = []

    def fake_call(token, method, payload=None, timeout=10):
        if method == "getUpdates":
            if batches:
                return batches.pop(0)
            cmds.stop()
            return []
        replies.append(payload["text"])
        return {}

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(notifier, "channel", lambda: {"token": "t", "chat_id": "7"})
    monkeypatch.setattr(notifier, "call", fake_call)
    monkeypatch.setattr(office.auditor, "daily_report", locked)
    cmds._loop()
    assert replies[0].startswith("Errore nel comando")
    assert replies[1] == "In pausa per 60 minuti."                # "²" non è un numero: 60 minuti predefiniti
    assert office.store.get("kill_switch")                         # /stop funziona ancora
    assert cmds.offset == 13


# ── n.35: lo script di avvio automatico è in UTF-16 con BOM ─────────────
def test_autostart_vbs_is_utf16_with_bom(monkeypatch, tmp_path):
    from betbot import system
    root = tmp_path / "Nicolò" / "Bet_bot"
    monkeypatch.setattr(system, "is_windows", lambda: True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(system, "ROOT", root)
    st = system.set_autostart(True)
    raw = Path(st["path"]).read_bytes()
    assert raw[:2] == b"\xff\xfe"
    text = raw.decode("utf-16")
    assert f'sh.CurrentDirectory = "{root}"' in text and "Nicolò" in text
    assert "\r\r\n" not in text


# ── n.38: simulazione con il database ancora aperto → messaggio chiaro ─────────────
def test_simulation_db_in_use_gives_clear_error(monkeypatch, tmp_path):
    from betbot import simulate
    monkeypatch.setattr(simulate, "RUNTIME_DIR", tmp_path)
    (tmp_path / "simulazione.db").write_bytes(b"")
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "simulazione.db":
            raise PermissionError(32, "Il file è in uso da un altro processo")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(simulate.DbInUse, match="chiudi la finestra"):
        asyncio.run(simulate.run(hours=0))

    def busy(*a, **k):
        raise simulate.DbInUse("chiudi la finestra")

    monkeypatch.setattr(simulate, "main", busy)
    assert _run_cli(monkeypatch, "simula", "--ore", "1") == 1


# ── n.39: /vendor/ serve solo file che stanno davvero in vendor/ ─────────────
def test_vendor_serves_only_files_inside_vendor(monkeypatch, tmp_path, servers):
    from betbot import server
    dash = tmp_path / "dashboard"
    (dash / "vendor").mkdir(parents=True)
    (dash / "index.html").write_text("<html></html>", encoding="utf-8")
    (dash / "vendor" / "three.js").write_text("// ok", encoding="utf-8")
    (tmp_path / "segreto.js").write_text("// fuori", encoding="utf-8")
    (dash / "vendor" / "link.js").symlink_to(tmp_path / "segreto.js")
    monkeypatch.setattr(server, "DASHBOARD_FILE", dash / "index.html")
    port = _free_port()
    servers.append(server.serve(port, background=True, db_path=tmp_path / "o.db"))
    assert _get(port, "/vendor/three.js") == (200, b"// ok")
    assert _get(port, "/vendor/link.js")[0] == 404
    assert _get(port, "/vendor/..%2Fsegreto.js")[0] == 404


# ── n.42: percorso del certificato con le virgolette di "Copia come percorso" ─────────────
def test_betfair_cert_path_quotes_are_removed(monkeypatch, tmp_path):
    from betbot import local_settings, server
    saved = {}
    monkeypatch.setattr(local_settings, "load", lambda: json.loads(json.dumps(local_settings.DEFAULTS)))
    monkeypatch.setattr(local_settings, "save", lambda s: saved.update(s))
    crt = tmp_path / "client-2048.crt"
    crt.write_text("x", encoding="utf-8")
    server.handle_action("/api/settings/betfair", {"cert_file": f'"{crt}"'})
    assert saved["betfair"]["cert_file"] == str(crt)
    with pytest.raises(ValueError, match="non trovato"):
        server.handle_action("/api/settings/betfair", {"cert_file": f'"{tmp_path / "manca.crt"}"'})


# ── n.31/33/40: file .bat ─────────────
def _depths(path: Path):
    """Profondità dei blocchi ( ) all'inizio di ogni riga, ignorando il testo tra virgolette e le righe REM."""
    depth, out = 0, []
    for line in path.read_bytes().decode("ascii").split("\r\n"):
        out.append((depth, line))
        if line.strip().upper().startswith("REM"):
            continue
        quoted = False
        for ch in line:
            if ch == '"':
                quoted = not quoted
            elif not quoted and ch == "(":
                depth += 1
            elif not quoted and ch == ")":
                depth -= 1
    return out


def test_bat_files_are_crlf_and_safe():
    for bat in ROOT.glob("*.bat"):
        raw = bat.read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), bat.name
        assert _depths(bat)[-1][0] == 0, bat.name
    # n.31: %cd% mai dentro un blocco ( ): una ) nel percorso lo chiuderebbe
    for depth, line in _depths(ROOT / "installa.bat"):
        if "%cd%" in line:
            assert depth == 0 and "(" not in line, line
    # n.33: robocopy, pip e l'uscita stanno nello stesso blocco di aggiorna.bat
    lines = _depths(ROOT / "aggiorna.bat")
    for key in ("robocopy", "pip install", "exit /b 0"):
        assert any(d > 0 and key in line for d, line in lines), key
    # n.40: avvia.bat ricontrolla la richiesta di spegnimento dopo l'attesa, sulla stessa riga
    assert any("timeout /t 30" in line and "ferma.richiesta" in line for _, line in _depths(ROOT / "avvia.bat"))
    assert any("ferma.richiesta" in line for _, line in lines)

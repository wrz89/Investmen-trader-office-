"""I .bat spostati in strumenti\\ devono risalire alla cartella principale, quelli nella principale restare lì."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_bat_nella_principale_e_in_strumenti():
    for p in ROOT.glob("*.bat"):
        assert b'cd /d "%~dp0"\r\n' in p.read_bytes(), p.name
    tools = list((ROOT / "strumenti").glob("*.bat"))
    assert len(tools) >= 10
    for p in tools:
        assert b'cd /d "%~dp0.."\r\n' in p.read_bytes(), p.name
        assert not (ROOT / p.name).exists(), f"{p.name} è doppio"


def test_aggiorna_toglie_i_doppioni():
    assert b"strumenti" in (ROOT / "aggiorna.bat").read_bytes()

"""Integrazione con Windows: avvio automatico all'accesso e PC sveglio mentre il bot lavora."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import ROOT

STARTUP_NAME = "Bet_bot.vbs"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def startup_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _python_exe() -> Path:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return venv if venv.exists() else Path(sys.executable)


def startup_script() -> str:
    """Script VBS che avvia Bet_bot in una finestra ridotta a icona, senza aprire il browser."""
    bat = str(ROOT / "avvia.bat")
    return (
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{ROOT}"\r\n'
        f'sh.Run """{bat}"" --no-browser", 7, False\r\n'
    )


def autostart_status() -> dict:
    folder = startup_dir()
    supported = is_windows() and folder is not None
    enabled = bool(supported and (folder / STARTUP_NAME).exists())
    return {"supported": supported, "enabled": enabled, "path": str(folder / STARTUP_NAME) if folder else None}


def set_autostart(enabled: bool) -> dict:
    status = autostart_status()
    if not status["supported"]:
        raise OSError("L'avvio automatico è disponibile solo su Windows.")
    target = Path(status["path"])
    if enabled:
        target.parent.mkdir(parents=True, exist_ok=True)
        # UTF-16 con BOM: Windows Script Host legge i .vbs come ANSI o UTF-16, NON come UTF-8
        # (con una cartella tipo C:\Users\Nicolò\… l'avvio automatico fallirebbe)
        target.write_text(startup_script(), encoding="utf-16", newline="")
    elif target.exists():
        target.unlink()
    return autostart_status()


def keep_awake(on: bool) -> bool:
    """Chiede a Windows di non andare in sospensione finché il bot è acceso. Nessun permesso di amministratore."""
    if not is_windows():
        return False
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        return False

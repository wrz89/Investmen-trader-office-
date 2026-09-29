"""Cifratura dei segreti su Windows con DPAPI (CryptProtectData), senza librerie esterne.

I segreti (password Betfair, app key, token Telegram, chiavi API) vengono salvati come
"dpapi:<base64>": solo lo stesso utente Windows, sullo stesso PC, può decifrarli. Un file
local_settings.json copiato altrove è inutilizzabile. Su altri sistemi restano in chiaro.
"""
from __future__ import annotations

import base64
import sys

PREFIX = "dpapi:"


def _blob(data: bytes):
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf, DATA_BLOB


def available() -> bool:
    return sys.platform.startswith("win")


def protect(text: str) -> str:
    if not text or text.startswith(PREFIX) or not available():
        return text
    try:
        import ctypes
        blob_in, _keep, DATA_BLOB = _blob(text.encode("utf-8"))
        blob_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob_in), "Bet_bot", None, None, None, 0,
                                                      ctypes.byref(blob_out)):
            return text
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return PREFIX + base64.b64encode(raw).decode("ascii")
    except Exception:
        return text


def unprotect(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value
    if not available():
        return ""                                   # cifrato su un altro PC Windows: non leggibile qui
    try:
        import ctypes
        blob_in, _keep, DATA_BLOB = _blob(base64.b64decode(value[len(PREFIX):]))
        blob_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0,
                                                        ctypes.byref(blob_out)):
            return ""
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return raw.decode("utf-8")
    except Exception:
        return ""

"""Storico del tennis per il backtest: tennis-data.co.uk (ATP e WTA).

Un file Excel per anno e circuito, con una riga per partita: vincitore, perdente, ranking, set,
"Comment" (Completed / Retired / Walkover) e quote di più bookmaker (B365W/B365L, PSW/PSL,
MaxW/MaxL, AvgW/AvgL). Dal 2025-2026 ci sono anche le quote Betfair Exchange (BFEW/BFEL),
quelle che servono a Bet_bot. Le partite finite per ritiro o walkover restano fuori dal test:
su Betfair le regole di annullamento cambiano da sport a sport e il test sarebbe falsato.

Nota: da alcuni server esteri il sito risponde 403; dal PC in Italia di solito scarica senza problemi.
In alternativa si possono mettere a mano i file .xlsx in runtime/history/ (nome tennis_atp_2026.xlsx).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

INDEX = "http://www.tennis-data.co.uk/alldata.php"
URLS = {"atp": "{base}/{year}/{year}.xlsx", "wta": "{base}/{year}w/{year}.xlsx"}


def _base_url() -> str:
    """Il sito ha aggiunto un prefisso variabile ai link dei file: lo si legge dalla pagina dei dati."""
    import re

    import requests
    try:
        html = requests.get(INDEX, headers=UA, timeout=20).text
        m = re.search(r'href="([^"]*?)/\d{4}/\d{4}\.xlsx"', html)
        if m:
            href = m.group(1)
            return href if href.startswith("http") else "http://www.tennis-data.co.uk/" + href.lstrip("/")
    except Exception:
        pass
    return "http://www.tennis-data.co.uk"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Bet_bot backtest"}


def tennis_years(today: date | None = None) -> list[int]:
    today = today or date.today()
    return [today.year - 1, today.year]             # le colonne Betfair Exchange ci sono dagli ultimi anni


def tennis_paths(years: list[int] | None = None) -> list[Path]:
    import time

    import requests

    from .backtest import HISTORY_DIR
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    out, errors = [], []
    base = None
    for year in years or tennis_years():
        for tour, url in URLS.items():
            path = HISTORY_DIR / f"tennis_{tour}_{year}.xlsx"
            stale = year == date.today().year and path.exists() and time.time() - path.stat().st_mtime > 86400
            if not path.exists() or stale:
                try:
                    base = base or _base_url()
                    r = requests.get(url.format(base=base.rstrip("/"), year=year), headers=UA, timeout=30)
                    if r.status_code == 200 and r.content[:2] == b"PK":      # un vero file Excel (zip)
                        path.write_bytes(r.content)
                    else:
                        errors.append(f"{tour.upper()} {year}: risposta {r.status_code}")
                except Exception as exc:
                    errors.append(f"{tour.upper()} {year}: {exc}")
            if path.exists():
                out.append(path)
    if errors and not out:
        raise RuntimeError("tennis-data.co.uk non raggiungibile (" + "; ".join(errors) + ")")
    return out

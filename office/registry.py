"""Registro immutabile delle strategie.

Ogni versione ha:
  registry/<ID>.json              definizione (parametri, impronta del codice)
  registry/<ID>.validation.json   esito della validazione (scritto UNA volta)

I file si aprono in modalità "x": se esistono già, non vengono mai sovrascritti.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import REGISTRY_DIR, file_sha256


def _path(strategy_id: str, suffix: str = "") -> Path:
    return REGISTRY_DIR / f"{strategy_id}{suffix}.json"


def code_hash(module) -> str:
    return file_sha256(Path(module.__file__))


def register(module) -> dict:
    """Registra la versione se nuova. Restituisce {'status': new|ok|tampered, ...}."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(module.STRATEGY_ID)
    current = code_hash(module)
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        status = "ok" if saved["code_sha256"] == current else "tampered"
        return {"status": status, "definition": saved}
    definition = {
        "strategy_id": module.STRATEGY_ID,
        "name": module.NAME,
        "family": module.FAMILY,
        "description": module.DESCRIPTION,
        "param_grid": module.PARAM_GRID,
        "timeframe": getattr(module, "TIMEFRAME", None),
        "code_file": Path(module.__file__).name,
        "code_sha256": current,
        "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(definition, fh, indent=2, ensure_ascii=False)
    return {"status": "new", "definition": definition}


def load_validation(strategy_id: str) -> dict | None:
    path = _path(strategy_id, ".validation")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_validation(strategy_id: str, result: dict) -> None:
    with open(_path(strategy_id, ".validation"), "x", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False, default=str)


def total_trials() -> int:
    """Numero di combinazioni di parametri testate finora su TUTTE le versioni
    validate: serve alla correzione per i test multipli."""
    total = 0
    for p in REGISTRY_DIR.glob("*.validation.json"):
        total += json.loads(p.read_text(encoding="utf-8")).get("n_combos", 0)
    return total

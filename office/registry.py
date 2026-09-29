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


def _audit_tag(per_side: float) -> str:
    return f".costaudit-{round(per_side * 10_000)}bp"


def is_audit_file(path: Path) -> bool:
    return ".costaudit-" in path.name


def needs_cost_audit(validation: dict, per_side: float) -> bool:
    """La validazione è stata fatta con costi più bassi di quelli attuali?"""
    return validation["costs"]["per_side"] < per_side - 1e-9


def load_cost_audit(strategy_id: str, per_side: float) -> dict | None:
    path = _path(strategy_id, _audit_tag(per_side))
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_cost_audit(strategy_id: str, per_side: float, result: dict) -> None:
    """Riverifica con i costi reali: file separato, scritto UNA volta. La validazione originale resta intatta."""
    with open(_path(strategy_id, _audit_tag(per_side)), "x", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False, default=str)


def cost_blocked(strategy_id: str, validation: dict | None, per_side: float) -> dict | None:
    """Esito della verifica costi se ha BOCCIATO la strategia (può solo bocciare, mai promuovere)."""
    if not validation or not needs_cost_audit(validation, per_side):
        return None
    audit = load_cost_audit(strategy_id, per_side)
    return audit if audit and audit["verdict"] != "PASSED" else None


def total_trials() -> int:
    """Numero di combinazioni di parametri testate finora su TUTTE le versioni
    validate: serve alla correzione per i test multipli."""
    total = 0
    for p in REGISTRY_DIR.glob("*.validation.json"):
        total += json.loads(p.read_text(encoding="utf-8")).get("n_combos", 0)
    return total

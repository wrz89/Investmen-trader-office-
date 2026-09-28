"""AGENTE 3 — STRATEGY RESEARCHER.

Mantiene la libreria delle strategie e il registro delle versioni.
Non mette MAI capitale a rischio: ogni nuova versione passa dal Quant.
"""
from __future__ import annotations

from .. import registry, strategies
from .base import Agent

# Idee in coda di ricerca: diventano nuove versioni solo dopo essere state scritte
# come file separati (mai modificando una versione esistente).
BACKLOG = [
    {"idea": "Rotazione (STRATEGY_04_v1): promettente ma non provata (67 trade, Sharpe 0,83). "
             "Proposta: osservazione in ombra sui dati nuovi invece di ritoccarla", "family": "rotation"},
    {"idea": "Mean reversion 4h (STRATEGY_05_v1): rifiutata, perde anche a 4h. Famiglia da abbandonare", "family": "mean_reversion"},
    {"idea": "Filtro funding: niente long quando il funding perpetuo è estremo", "family": "filtro"},
    {"idea": "Pairs BTC/ETH — BLOCCATA: richiede short (non autorizzato)", "family": "stat_arb"},
    {"idea": "Grid — ESCLUSA: equivale a mediare al ribasso (vietato)", "family": "grid"},
]


class StrategyResearcher(Agent):
    key = "strategy_researcher"
    name = "Strategy Researcher"
    role = "Genera e cataloga le strategie (senza capitale)"

    def sync_registry(self) -> list[dict]:
        """Registra le nuove versioni e verifica che quelle esistenti non siano state modificate."""
        out = []
        for module in strategies.discover():
            reg = registry.register(module)
            if reg["status"] == "new":
                self.say(f"Nuova versione registrata: {module.STRATEGY_ID} ({module.NAME}). "
                         "Inviata al Quant per la validazione.", "working", "registry")
            elif reg["status"] == "tampered":
                self.say(f"{module.STRATEGY_ID}: codice modificato dopo la registrazione! "
                         "Crea una nuova versione invece di sovrascrivere.", "alert", "tampered", level="ERROR")
            out.append({"module": module, "registry": reg})
        self.status("ok", f"Libreria: {len(out)} versioni registrate, {len(BACKLOG)} idee in coda.",
                    stats={"versions": [m["module"].STRATEGY_ID for m in out], "backlog": BACKLOG})
        return out

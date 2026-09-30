"""Classe base: ogni agente aggiorna la propria scrivania e scrive nel registro.
Gli eventi rilevanti (puntate, chiusure, circuit breaker, sentiment, errori,
report) partono anche su Telegram tramite il Notifier dell'ufficio."""
from __future__ import annotations

WHO = {
    "direttore": "Carlo · Direttore Sportivo",
    "quote": "Sara · Osservatrice Quote",
    "analista": "Davide · Analista Strategie",
    "cavalli": "Matteo · Trader Cavalli",
    "risk": "Bruno · Risk Manager",
    "banco": "Pietro · Banco Scommesse",
    "tesoriere": "Anna · Tesoriera",
    "auditor": "Irene · Auditor",
    "coach": "Leo · Allenatore",
}


class Agent:
    key = "agent"
    name = "Agente"
    role = ""

    def __init__(self, office):
        self.office = office
        self.store = office.store
        self.settings = office.settings

    def status(self, state: str, message: str, stats: dict | None = None) -> None:
        """state: idle | working | ok | blocked | alert"""
        self.store.set_status(self.key, state, message, stats)

    def log(self, message: str, level: str = "INFO", kind: str = "info", payload: dict | None = None) -> None:
        self.store.event(self.key, message, level, kind, payload)
        notifier = getattr(self.office, "notifier", None)
        if notifier is not None:
            try:
                notifier.on_event(WHO.get(self.key, self.key), level, kind, message)
            except Exception:
                pass                                   # una notifica non ferma mai l'ufficio

    def say(self, message: str, state: str = "working", kind: str = "info", payload: dict | None = None,
            level: str = "INFO", stats: dict | None = None) -> None:
        self.status(state, message, stats)
        self.log(message, level, kind, payload)

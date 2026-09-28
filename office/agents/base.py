"""Classe base degli agenti: ogni agente aggiorna la propria "scrivania"
(stato visibile in dashboard) e scrive nel registro eventi dell'ufficio."""
from __future__ import annotations


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

    def log(self, message: str, level: str = "INFO", kind: str = "info",
            payload: dict | None = None) -> None:
        self.store.event(self.key, message, level, kind, payload)

    def say(self, message: str, state: str = "working", kind: str = "info",
            payload: dict | None = None, level: str = "INFO", stats: dict | None = None) -> None:
        self.status(state, message, stats)
        self.log(message, level, kind, payload)

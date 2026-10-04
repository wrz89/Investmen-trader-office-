import sqlite3
from datetime import date

import pytest

from office.config import load_yaml
from office.nexus import APPROVED, CLOSED, OPEN, Nexus, evaluate, render_report, trading_round_trip_cost
from office.store import Store

COSTS = {"taker_fee": 0.0025, "maker_fee": 0.001, "slippage_bps": 3, "default_spread_bps": 6}


def test_trading_costs_eat_the_budget():
    cost = trading_round_trip_cost(30, COSTS)
    assert 0.18 < cost < 0.19                                  # 0,62% di 30 € per un solo giro


def test_evaluate_rejects_everything_without_an_edge():
    cands = {c["id"]: c for c in evaluate(load_yaml("nexus.yaml"), COSTS)}
    assert cands["bybit_spot_trading"]["verdict"] == "bocciata" and cands["bybit_spot_trading"]["expected_net_eur"] < 0
    assert cands["paid_ads"]["verdict"] == "bocciata"          # 30 € bruciano tutta la riserva
    assert cands["bybit_earn_usdc"]["verdict"] == "bocciata"   # 3 centesimi non sono un ritorno
    assert cands["domain_landing"]["verdict"] == "in attesa del cancello"
    assert cands["mvp_zero_cost"]["verdict"] == "ipotesi attiva"
    assert cands["accumulation_topup"]["verdict"] == "riserva"
    assert not any(c["verdict"] == "spesa approvabile" for c in cands.values())


def _nexus(tmp_path):
    store = Store(tmp_path / "n.db")
    return Nexus(type("O", (), {"store": store, "settings": {"costs": COSTS}})())


def test_first_decision_holds_cash_and_opens_zero_cost_probe(tmp_path):
    n = _nexus(tmp_path)
    s = n.decide(date(2026, 10, 4))
    assert s["balance"] == 30.0 and s["spent"] == 0.0
    assert s["posture"].startswith("Liquidità al 100%")
    v = n.venture("mvp_zero_cost")
    assert v["status"] == OPEN and v["review_by"] == "2026-11-03"
    assert n.venture("paid_ads")["status"] == "bocciata"
    assert "30.00 €" in render_report(s) and "Prossimo traguardo" in render_report(s)
    n.decide(date(2026, 10, 5))                                # una seconda dotazione non viene mai aggiunta
    assert n.totals()["deposit"] == 30.0


def test_spend_is_blocked_until_the_gate_opens(tmp_path):
    n = _nexus(tmp_path)
    n.decide(date(2026, 10, 4))
    with pytest.raises(ValueError):                            # dominio: cancello chiuso
        n.spend("domain_landing", 12, "dominio")
    with pytest.raises(ValueError):                            # ipotesi aperta ma senza trazione
        n.spend("mvp_zero_cost", 1, "qualcosa")
    n.record_traction("mvp_zero_cost", 5)
    n.decide(date(2026, 10, 20))
    assert n.venture("mvp_zero_cost")["status"] == APPROVED
    assert n.venture("domain_landing")["status"] == APPROVED  # il cancello si è aperto
    with pytest.raises(ValueError):                            # oltre il tetto per singola spesa
        n.spend("domain_landing", 13, "dominio premium")
    t = n.spend("domain_landing", 12, "dominio .it 1 anno")
    assert t["balance"] == 18.0
    with pytest.raises(ValueError):                            # scenderebbe sotto la riserva di 10 €
        n.spend("domain_landing", 9, "altro")
    n.income("domain_landing", 4.5, "primo cliente")
    assert n.totals()["balance"] == 22.5


def test_probe_is_closed_after_review_without_traction(tmp_path):
    n = _nexus(tmp_path)
    n.decide(date(2026, 10, 4))
    s = n.decide(date(2026, 11, 4))
    assert n.venture("mvp_zero_cost")["status"] == CLOSED
    assert "liquidi" in s["last"]["action"]


def test_nexus_ledger_is_immutable(tmp_path):
    n = _nexus(tmp_path)
    n.decide(date(2026, 10, 4))
    with pytest.raises(sqlite3.DatabaseError):
        n.store.execute("UPDATE nexus_ledger SET eur=1000")
    with pytest.raises(sqlite3.DatabaseError):
        n.store.execute("DELETE FROM nexus_decisions")

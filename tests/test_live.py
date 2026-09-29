"""Collegamento reale a Bybit, provato con un Bybit FINTO (nessuna rete, nessun soldo)."""
import time
from datetime import datetime

import pytest

from office import local_settings
from office.live_exchange import LiveError, fill_of, key_problems, parse_permissions

GOOD_KEY = {"result": {"readOnly": 0, "ips": ["93.40.1.2"], "permissions": {"Spot": ["SpotTrade"], "Wallet": []}}}


class FakeBybit:
    def __init__(self, perms=GOOD_KEY, eur=500.0):
        self.perms, self.eur = perms, eur
        self.orders, self.calls = {}, []

    def privateGetV5UserQueryApi(self):
        return self.perms

    def fetch_balance(self):
        return {"free": {"EUR": self.eur}}

    def amount_to_precision(self, s, a):
        return f"{a:.6f}"

    def price_to_precision(self, s, p):
        return f"{p:.2f}"

    def _new(self, symbol, side, typ, amount, price, cost):
        oid = str(len(self.orders) + 1)
        self.calls.append((symbol, side, typ))
        closed = typ == "market"
        filled = (cost / price) if closed else 0.0
        self.orders[oid] = {"id": oid, "symbol": symbol, "status": "closed" if closed else "open", "price": price,
                            "amount": amount, "filled": filled, "cost": cost if closed else 0.0, "average": price,
                            "fees": [{"currency": "EUR", "cost": cost * 0.0025}] if closed else []}
        if closed:
            self.eur -= cost
        return {"id": oid}

    def create_order(self, symbol, typ, side, amount, price, params=None):
        assert side == "buy" and params == {"postOnly": True}
        return self._new(symbol, side, typ, amount, price, amount * price)

    def create_market_buy_order_with_cost(self, symbol, cost):
        return self._new(symbol, "buy", "market", None, 100.0, cost)

    def fetch_order(self, oid, symbol, params=None):
        return dict(self.orders[oid])

    def cancel_order(self, oid, symbol):
        self.orders[oid]["status"] = "canceled"

    def fill(self, oid):                               # l'exchange esegue l'ordine limite
        o = self.orders[oid]
        o.update(status="closed", filled=o["amount"], cost=o["amount"] * o["price"],
                 fees=[{"currency": o["symbol"].split("/")[0], "cost": o["amount"] * 0.001}])
        self.eur -= o["cost"]


def test_key_rules():
    assert key_problems(parse_permissions(GOOD_KEY)) == []
    wd = {"result": {"readOnly": 0, "ips": ["1.2.3.4"], "permissions": {"Spot": ["SpotTrade"], "Withdraw": ["Withdraw"]}}}
    assert any("PRELEVARE" in p for p in key_problems(parse_permissions(wd)))
    no_ip = {"result": {"readOnly": 0, "ips": ["*"], "permissions": {"Spot": ["SpotTrade"]}}}
    assert any("IP" in p for p in key_problems(parse_permissions(no_ip)))
    ro = {"result": {"readOnly": 1, "ips": ["1.2.3.4"], "permissions": {"Spot": ["SpotTrade"]}}}
    assert any("Spot" in p for p in key_problems(parse_permissions(ro)))


def test_fill_of_handles_fees_in_coin_and_euro():
    f = fill_of({"filled": 0.001, "cost": 60.0, "average": 60_000, "fees": [{"currency": "BTC", "cost": 0.000001}]}, "BTC/EUR")
    assert abs(f["qty"] - 0.000999) < 1e-12 and f["eur"] == 60.0 and abs(f["fee_eur"] - 0.06) < 1e-9
    f = fill_of({"filled": 0.5, "cost": 50.0, "average": 100, "fees": [{"currency": "EUR", "cost": 0.125}]}, "SOL/EUR")
    assert f["qty"] == 0.5 and f["eur"] == 50.125


@pytest.fixture
def live_office(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(local_settings, "PATH", tmp_path / "local_settings.json")
    s = local_settings.load()
    s["bybit"].update(key="ABCDEFGHIJKL", secret="S" * 30, verified=True, test_done=True, live=True)
    local_settings.save(s)
    import importlib
    import pandas as pd
    import office.config
    importlib.reload(office.config)
    import office.core
    importlib.reload(office.core)
    o = office.core.Office(connect_market=False)
    now = time.time() * 1000
    quotes = {"BTC/EUR": {"ask": 100.0, "bid": 99.99, "timestamp": now},
              "SOL/EUR": {"ask": 100.0, "bid": 99.99, "timestamp": now},
              "USDC/EUR": {"ask": 0.86, "bid": 0.859, "timestamp": now}}
    o.market = type("M", (), {"ticker": lambda self, s: quotes[s],
                              "candles": lambda self, *a, **k: pd.DataFrame({"ts": [], "low": []})})()
    o.fake = FakeBybit()
    o.live_factory = lambda k, sec, h: o.fake
    acc = o.accumulation
    acc.cfg.update(start_month=datetime.now().strftime("%Y-%m"), day_of_month=1, initial_eur=0, amount_eur=70,
                   allocation={"BTC/EUR": 0.7, "SOL/EUR": 0.3}, execution={"order_type": "limit", "limit_timeout_hours": 24})
    return o


SNAP = {"symbols": {"BTC/USDC": {"ask": 116.0, "anomalies": []}, "SOL/USDC": {"ask": 116.0, "anomalies": []}},
        "health": {"error_rate": 0}}


def test_live_limit_then_fill_records_real_buys_and_never_sells(live_office):
    acc, fake = live_office.accumulation, live_office.fake
    assert acc.mode() == "live"
    acc.run(SNAP)                                           # due ordini limite su Bybit, nessun acquisto ancora
    assert [c[2] for c in fake.calls] == ["limit", "limit"] and acc.summary()["buys"] == 0
    acc.run(SNAP)                                           # ancora aperti: nessun nuovo ordine
    assert len(fake.calls) == 2
    for oid in list(fake.orders):
        fake.fill(oid)
    acc.run(SNAP)
    s = acc.summary()
    assert s["mode"] == "live" and s["buys"] == 2 and abs(s["eur_in"] - 70) < 0.05 and s["due_now"] is False
    assert all(side == "buy" for _, side, _ in fake.calls)  # l'ufficio non vende mai


def test_live_timeout_cancels_and_buys_rest_at_market(live_office):
    acc, fake = live_office.accumulation, live_office.fake
    acc.run(SNAP)
    orders = live_office.store.get("accum_orders_live")
    for o in orders.values():
        o["placed"] -= 25 * 3600
    live_office.store.set("accum_orders_live", orders)
    acc.run(SNAP)
    assert [c[2] for c in fake.calls] == ["limit", "limit", "market", "market"]
    assert all(fake.orders[i]["status"] == "canceled" for i in ("1", "2"))
    assert acc.summary()["buys"] == 2


def test_live_postpones_without_euros_and_places_nothing(live_office):
    live_office.fake.eur = 10.0
    live_office.accumulation.run(SNAP)
    assert live_office.fake.calls == []
    msgs = [e["message"] for e in live_office.store.query("SELECT message FROM events WHERE kind='accum_alert'")]
    assert any("conto di trading" in m for m in msgs)


def test_live_refuses_symbols_outside_the_plan(live_office):
    live = live_office.accumulation.live_exchange()
    with pytest.raises(LiveError):
        live.market_buy("DOGE/EUR", 5)


def test_live_is_off_until_switched_on(live_office):
    s = local_settings.load()
    s["bybit"]["live"] = False
    local_settings.save(s)
    live_office.accumulation.run(SNAP)
    assert live_office.fake.calls == [] and live_office.accumulation.mode() == "paper"


def test_server_bybit_flow(tmp_path, monkeypatch):
    from office import server
    monkeypatch.setattr(local_settings, "PATH", tmp_path / "ls.json")
    fake = FakeBybit()
    monkeypatch.setattr("ccxt.bybit", lambda cfg: fake)
    with pytest.raises(ValueError):
        server.handle_action("/api/settings/bybit", {"key": "abc", "secret": "x"})
    server.handle_action("/api/settings/bybit", {"key": "ABCDEFGHIJKL", "secret": "S" * 30})
    pub = local_settings.public(local_settings.load())["bybit"]
    assert pub["secret"] == "" and "ABCDEFGHIJKL" not in pub["key"] and pub["has_key"]
    with pytest.raises(ValueError):                         # niente reale senza verifica e prova
        server.handle_action("/api/settings/bybit_live", {"enabled": True})
    server.handle_action("/api/bybit/verify", {})
    server.handle_action("/api/bybit/test_order", {})
    assert fake.calls == [("BTC/EUR", "buy", "market")]
    server.handle_action("/api/settings/bybit_live", {"enabled": True})
    assert local_settings.load()["bybit"]["live"] is True
    fake.perms = {"result": {"readOnly": 0, "ips": ["*"], "permissions": {"Spot": ["SpotTrade"]}}}
    with pytest.raises(ValueError):                         # chiave peggiorata: reale spento subito
        server.handle_action("/api/bybit/verify", {})
    assert local_settings.load()["bybit"]["live"] is False


def test_ensure_eur_moves_only_the_missing_amount_from_funding():
    from office.live_exchange import LiveExchange

    class F(FakeBybit):
        def __init__(self):
            super().__init__(eur=2.0)
            self.funding, self.transfers = 200.0, []

        def fetch_balance(self, params=None):
            return {"free": {"EUR": self.funding if (params or {}).get("type") == "funding" else self.eur}}

        def transfer(self, code, amount, frm, to):
            assert (code, frm, to) == ("EUR", "funding", "unified")
            self.transfers.append(amount)
            self.funding -= amount
            self.eur += amount

    fake = F()
    live = LiveExchange({"exchange": {}}, ["BTC/EUR"], lambda *a: fake)
    live.key, live.secret = "K" * 12, "S" * 30
    assert live.ensure_eur(5.0) >= 5.0 and fake.transfers == [3.01]
    assert live.ensure_eur(1.0) >= 1.0 and fake.transfers == [3.01]      # già sufficiente: nessun trasferimento
    assert parse_permissions({"result": {"permissions": {"Wallet": ["AccountTransfer"]}}})["transfer"] is True

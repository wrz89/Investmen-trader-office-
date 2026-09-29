import time

from office.agents.news_analyst import classify, parse_feed
from office.config import load_yaml

CFG = load_yaml("news.yaml")


def test_classify_high_risk_on_universe_asset():
    c = classify("Solana DEX drained in $40M exploit", CFG)
    assert c["severity"] == "high" and "SOL" in c["assets"]


def test_classify_exchange_and_quote_currency():
    assert "BYBIT" in classify("Bybit halts withdrawals after security incident", CFG)["assets"]
    c = classify("USDC briefly depegs as Circle reserves questioned", CFG)
    assert c["severity"] == "high" and "USDC" in c["assets"]


def test_classify_neutral_and_positive():
    assert classify("Ethereum developers schedule next call", CFG)["severity"] == "neutral"
    assert classify("Bitcoin hits all-time high on ETF inflows", CFG)["severity"] == "positive"


def test_parse_rss():
    xml = b"""<?xml version="1.0"?><rss><channel><item><title>BTC exploit</title>
      <link>https://example.com/a</link><pubDate>Tue, 29 Sep 2026 08:00:00 +0000</pubDate></item></channel></rss>"""
    items = parse_feed(xml)
    assert items[0]["title"] == "BTC exploit" and items[0]["link"] == "https://example.com/a"
    assert items[0]["published"] is not None


def test_news_block_stops_new_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_RUNTIME_DIR", str(tmp_path))
    import importlib
    import office.config
    importlib.reload(office.config)
    import office.core
    importlib.reload(office.core)
    office_ = office.core.Office(connect_market=False)
    office_.market = type("M", (), {"amount_to_precision": lambda self, s, a: round(a, 6),
                                    "market_info": lambda self, s: {"min_cost": 5, "min_amount": 0}})()
    office_.cycle_id = "test"
    snapshot = {"symbols": {"BTC/USDC": {"ok": True, "anomalies": [], "bid": 100, "ask": 100.01,
                                         "spread_bps": 1, "depth_ask": 1e6, "data_age_s": 1}},
                "correlations": {}, "health": {"error_rate": 0}}
    opp = {"id": "x", "symbol": "BTC/USDC", "direction": "LONG", "price": 100.01, "strategy_id": "S",
           "strategy_status": "PAPER", "stop": 98, "risk_pct": 0.02, "net_pct": None,
           "fees_pct": 0.002, "slippage_pct": 0.0006}
    label = "Nessun allarme notizie (Nora)"

    def news_check():
        d = office_.risk.evaluate(opp, snapshot, office_.account, None)
        return next(c for c in d["checks"] if c["label"] == label)

    assert news_check()["ok"]
    office_.store.set("news_blocks", {"ALL": {"until": time.time() + 3600, "reason": "Bybit hacked"}})
    assert not news_check()["ok"]
    office_.store.set("news_blocks", {"ETH": {"until": time.time() + 3600, "reason": "x"}})
    assert news_check()["ok"]                       # l'allarme su ETH non ferma BTC
    office_.store.set("news_blocks", {"BTC": {"until": time.time() - 1, "reason": "scaduto"}})
    assert news_check()["ok"]                       # i blocchi scaduti non contano

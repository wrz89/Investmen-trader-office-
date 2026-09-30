"""Chiavi Betfair: le chiamate dell'API Accounts per le app key partono senza X-Application; una chiave appena creata
si aspetta (1-3 minuti) prima di dichiarare il conto verificato."""
import json
import time

import pytest

from betbot.feeds import betfair
from betbot.feeds.betfair import BetfairClient


class FakeResp:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def _client(responses):
    c = BetfairClient({"app_key": "BetBot", "username": "u", "password": "p"})
    c.token, c.token_ts = "SESSIONE", time.time()           # sessione già aperta: nessun login durante il test
    sent = []

    def post(url, data=None, headers=None, timeout=None):
        method = json.loads(data)["method"].split("/")[-1]
        sent.append((method, dict(headers)))
        return FakeResp(responses(method))
    c.http.post = post
    return c, sent


def test_app_key_calls_have_no_x_application_but_others_do():
    c, sent = _client(lambda m: {"result": [] if m == "getDeveloperAppKeys" else
                                 {"appVersions": [{"applicationKey": "K", "delayData": True}]} if m == "createDeveloperAppKeys"
                                 else {"availableToBetBalance": 30} if m == "getAccountFunds" else []})
    c.developer_app_keys()
    c.create_developer_app_keys("BetBotx")
    c.account_funds()
    c.rpc("listEventTypes", {"filter": {}})
    h = {m: hd for m, hd in sent}
    assert "X-Application" not in h["getDeveloperAppKeys"] and "X-Application" not in h["createDeveloperAppKeys"]
    assert h["getDeveloperAppKeys"]["X-Authentication"] == "SESSIONE"
    assert h["getAccountFunds"]["X-Application"] == "BetBot" and h["listEventTypes"]["X-Application"] == "BetBot"


def test_invalid_app_key_is_readable():
    c, _ = _client(lambda m: {"error": {"code": -32099, "message": "AANGX-0004",
                                        "data": {"AccountAPINGException": {"errorCode": "INVALID_APP_KEY"}}}})
    with pytest.raises(betfair.BetfairError) as e:
        c.account_funds()
    assert "non ancora attiva" in str(e.value) and "1-3 minuti" in str(e.value)


def _run(tmp_path, monkeypatch, fails):
    from betbot import certificato, collega_betfair, config, local_settings
    store = {}
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS,
                                                         "betfair": dict(local_settings.DEFAULTS["betfair"])})
    monkeypatch.setattr(local_settings, "save", lambda s: store.update(json.loads(json.dumps(s))))
    monkeypatch.setattr(certificato, "create", lambda overwrite=False: {"created": True, "crt": str(tmp_path / "c.crt"),
                                                                         "key": str(tmp_path / "c.key")})
    monkeypatch.setattr(config, "LOCAL_OVERRIDE", tmp_path / "impostazioni.yaml")
    state = {"funds_calls": 0, "logins": 0}

    class Client(BetfairClient):
        def login(self):
            state["logins"] += 1
            self.token, self.token_ts = "SESSIONE", time.time()
            return self.token

    def responses(method):
        if method == "getDeveloperAppKeys":
            return {"result": []}
        if method == "createDeveloperAppKeys":
            return {"result": {"appVersions": [{"applicationKey": "NUOVA", "delayData": True, "active": True}]}}
        if method == "getAccountFunds":
            state["funds_calls"] += 1
            if state["funds_calls"] <= fails:
                return {"error": {"message": "AANGX-0004", "data": {"AccountAPINGException": {"errorCode": "INVALID_APP_KEY"}}}}
            return {"result": {"availableToBetBalance": 30.0}}
        return {"result": []}

    orig_init = Client.__init__

    def init(self, creds):
        orig_init(self, creds)
        self.http.post = lambda url, data=None, headers=None, timeout=None: FakeResp(
            responses(json.loads(data)["method"].split("/")[-1]))
    monkeypatch.setattr(Client, "__init__", init)
    monkeypatch.setattr(betfair, "BetfairClient", Client)
    slept, out = [], []
    answers = iter(["", "antony"])
    ok = collega_betfair.run(ask=lambda _: next(answers), ask_secret=lambda _: "segreta", open_url=lambda u: None,
                             out=out.append, sleep=slept.append)
    return ok, store, state, slept, "\n".join(out)


def test_new_key_waits_for_activation_then_verifies(tmp_path, monkeypatch):
    import yaml
    ok, store, state, slept, text = _run(tmp_path, monkeypatch, fails=2)
    assert ok and store["betfair"]["app_key"] == "NUOVA" and store["betfair"]["verified"] is True
    assert state["funds_calls"] == 3 and slept == [20, 20] and state["logins"] == 1   # nessun nuovo login
    assert "sta attivando la chiave" in text and "NUOVA" not in text and "segreta" not in text
    imp = yaml.safe_load((tmp_path / "impostazioni.yaml").read_text(encoding="utf-8"))
    assert imp["mode"] == "paper" and imp["feed"]["provider"] == "betfair"


def test_key_saved_even_if_never_activated(tmp_path, monkeypatch):
    ok, store, state, slept, text = _run(tmp_path, monkeypatch, fails=999)
    assert store["betfair"]["app_key"] == "NUOVA" and store["betfair"]["verified"] is False
    assert sum(slept) <= 180 and state["funds_calls"] == 10
    assert "Verifica il conto" in text

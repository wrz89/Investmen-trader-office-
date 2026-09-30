"""Collegamento guidato a Betfair: certificato, login, app key delayed creata o ritrovata, tutto salvato."""
from betbot import collega_betfair


def test_pick_delayed_key():
    apps = [{"appVersions": [{"applicationKey": "LIVEKEY", "delayData": False, "active": False},
                             {"applicationKey": "DELAYKEY", "delayData": True, "active": True}]}]
    assert collega_betfair._pick_delayed(apps) == "DELAYKEY"
    assert collega_betfair._pick_delayed([]) is None


def test_guided_connection_creates_key_and_saves(tmp_path, monkeypatch):
    from betbot import certificato, local_settings
    from betbot.feeds import betfair
    store = {}
    monkeypatch.setattr(local_settings, "load", lambda: {**local_settings.DEFAULTS,
                                                         "betfair": dict(local_settings.DEFAULTS["betfair"])})
    monkeypatch.setattr(local_settings, "save", lambda s: store.update(s))
    monkeypatch.setattr(certificato, "create", lambda overwrite=False: {"created": True, "crt": str(tmp_path / "c.crt"),
                                                                         "key": str(tmp_path / "c.key")})
    calls = []

    class FakeClient:
        def __init__(self, creds):
            self.creds, self.app_key, self.token, self.last_login_try = creds, creds["app_key"], None, 0

        def login(self):
            calls.append(("login", self.app_key, bool(self.creds.get("cert_file"))))
            self.token = "T"

        def rpc(self, method, params, url, service):
            calls.append((method, params))
            if method == "getDeveloperAppKeys":
                return []
            return {"appVersions": [{"applicationKey": "NEWDELAYED", "delayData": True, "active": True}]}

        def developer_app_keys(self):
            return self.rpc("getDeveloperAppKeys", {}, None, None)

        def create_developer_app_keys(self, name):
            return self.rpc("createDeveloperAppKeys", {"appName": name}, None, None)

        def account_funds(self):
            return {"availableToBetBalance": 30.0}

    monkeypatch.setattr(betfair, "BetfairClient", FakeClient)
    from betbot import config
    monkeypatch.setattr(config, "LOCAL_OVERRIDE", tmp_path / "impostazioni.yaml")
    answers = iter(["", "antony"])
    out = []
    ok = collega_betfair.run(ask=lambda _: next(answers), ask_secret=lambda _: "segreta", open_url=lambda u: None,
                             out=out.append)
    assert ok
    assert calls[0] == ("login", "BetBot", True)                            # login con certificato, chiave provvisoria
    assert any(c[0] == "createDeveloperAppKeys" for c in calls)
    bf = store["betfair"]
    assert bf["app_key"] == "NEWDELAYED" and bf["username"] == "antony" and bf["verified"] is True
    assert "segreta" not in "\n".join(out)                                   # la password non viene mai stampata
    import yaml
    imp = yaml.safe_load((tmp_path / "impostazioni.yaml").read_text(encoding="utf-8"))
    assert imp["mode"] == "paper" and imp["feed"]["provider"] == "betfair"

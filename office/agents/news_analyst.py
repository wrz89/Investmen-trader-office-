"""AGENTE 8 — NORA · NEWS ANALYST (sentinella delle notizie).

Legge fonti pubbliche (feed RSS, indice Fear & Greed) e classifica ogni titolo
con regole trasparenti. Se una notizia ad alto rischio riguarda un asset trattato,
chiede a Franco di bloccare i NUOVI ingressi su quell'asset per alcune ore.
Non apre, non chiude e non ingrandisce mai posizioni: può solo rendere l'ufficio
più prudente. Ogni notizia viene registrata con i prezzi del momento, per poter
misurare in futuro se le segnalazioni avevano valore.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from ..config import load_yaml
from ..store import now_iso
from .base import Agent

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, ts TEXT, published TEXT, source TEXT,
    title TEXT, link TEXT, assets TEXT, severity TEXT, words TEXT, prices TEXT
);
"""
UA = {"User-Agent": "Mozilla/5.0 (CryptoTradingOffice news sentinel)"}


def _rx(words: list[str]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(re.escape(w.strip()) for w in words) + r")\b", re.IGNORECASE)


def classify(title: str, cfg: dict) -> dict:
    """Asset citati, gravità (high / medium / positive / neutral) e parole che l'hanno decisa."""
    assets = [a for a, words in cfg["aliases"].items() if _rx(words).search(title)]
    for level in ("high_risk", "medium_risk", "positive"):
        hits = sorted({m.lower() for m in _rx(cfg[level]).findall(title)})
        if hits:
            sev = {"high_risk": "high", "medium_risk": "medium", "positive": "positive"}[level]
            return {"assets": assets, "severity": sev, "words": hits}
    return {"assets": assets, "severity": "neutral", "words": []}


def parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = {c.tag.split("}")[-1]: c for c in el}
        title = (get.get("title").text or "").strip() if get.get("title") is not None else ""
        link_el = get.get("link")
        link = ""
        if link_el is not None:
            link = (link_el.text or link_el.get("href") or "").strip()
        # attenzione: un elemento XML senza figli vale False, quindi niente "or"
        date_el = next((get[k] for k in ("pubDate", "published", "updated") if get.get(k) is not None), None)
        published = None
        if date_el is not None and date_el.text:
            try:
                published = parsedate_to_datetime(date_el.text.strip())
            except (TypeError, ValueError):
                try:
                    published = datetime.fromisoformat(date_el.text.strip().replace("Z", "+00:00"))
                except ValueError:
                    published = None
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if title:
            out.append({"title": title, "link": link, "published": published})
    return out


class NewsAnalyst(Agent):
    key = "news_analyst"
    name = "News Analyst"
    role = "Legge le notizie pubbliche e segnala i rischi"

    def __init__(self, office):
        super().__init__(office)
        self.cfg = load_yaml("news.yaml")
        with self.store._lock:
            self.store.conn.executescript(SCHEMA)
            self.store.conn.commit()

    # ── blocchi attivi (letti dal Risk Manager) ─────────────
    def active_blocks(self) -> dict:
        now = time.time()
        blocks = {k: v for k, v in (self.store.get("news_blocks") or {}).items() if v.get("until", 0) > now}
        self.store.set("news_blocks", blocks)
        return blocks

    def run(self, snapshot: dict | None = None) -> None:
        cfg = self.cfg
        last = self.store.get("news_last_fetch") or 0
        if time.time() - last < cfg["refresh_minutes"] * 60:
            self._status()
            return
        self.store.set("news_last_fetch", time.time())
        self.status("working", "Leggo le notizie delle ultime ore…")
        items, failed = [], []
        for feed in cfg["feeds"]:
            try:
                r = requests.get(feed["url"], headers=UA, timeout=12)
                r.raise_for_status()
                for it in parse_feed(r.content):
                    items.append({**it, "source": feed["name"]})
            except Exception:
                failed.append(feed["name"])
        self._fear_greed()

        universe = {s.split("/")[0] for s in self.settings["universe"]}
        prices = {s.split("/")[0]: v.get("mid") for s, v in ((snapshot or {}).get("symbols") or {}).items()}
        max_age = cfg["max_age_hours"] * 3600
        new_high = 0
        for it in items:
            uid = hashlib.sha1((it["link"] or it["title"]).encode()).hexdigest()
            pub = it["published"]
            if pub is not None and (datetime.now(timezone.utc) - pub).total_seconds() > max_age:
                continue
            c = classify(it["title"], cfg)
            cur = self.store.execute(
                "INSERT OR IGNORE INTO news(uid, ts, published, source, title, link, assets, severity, words, prices) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (uid, now_iso(), pub.isoformat() if pub else None, it["source"], it["title"], it["link"],
                 json.dumps(c["assets"]), c["severity"], json.dumps(c["words"]), json.dumps(prices)))
            if cur.rowcount == 0:
                continue                                  # già vista
            relevant = [a for a in c["assets"] if a in universe or a in cfg["block_all_for"]]
            if c["severity"] == "high" and relevant:
                new_high += 1
                self._block(relevant, it, c)
            elif c["severity"] in ("medium", "high") and relevant:
                self.log(f"Da tenere d'occhio ({', '.join(relevant)}): «{it['title']}» — {it['source']}",
                         "INFO", "news", payload={"link": it["link"], "words": c["words"]})
        if failed:
            self.log(f"Fonti non raggiungibili: {', '.join(failed)}. Nessun effetto sul trading.", "WARN", "news_source")
        self._status(len(items), failed)

    def _block(self, assets: list[str], it: dict, c: dict) -> None:
        cfg = self.cfg
        until = time.time() + cfg["block_hours"] * 3600
        blocks = self.active_blocks()
        keys = ["ALL"] if any(a in cfg["block_all_for"] for a in assets) else assets
        for k in keys:
            blocks[k] = {"until": until, "reason": it["title"], "source": it["source"], "words": c["words"]}
        self.store.set("news_blocks", blocks)
        target = "TUTTI gli asset" if keys == ["ALL"] else ", ".join(keys)
        self.say(f"ALLARME NOTIZIE su {target}: «{it['title']}» ({it['source']}). Chiedo a Franco di bloccare "
                 f"i nuovi ingressi per {cfg['block_hours']} ore.", "alert", "news_alert", level="WARN",
                 payload={"assets": keys, "link": it["link"], "words": c["words"], "until": until})

    def _fear_greed(self) -> None:
        try:
            r = requests.get(self.cfg["fear_greed_url"], headers=UA, timeout=10)
            data = r.json()["data"]
            hist = [{"value": int(d["value"]), "label": d["value_classification"], "ts": int(d["timestamp"])} for d in data]
            self.store.set("fear_greed", {"now": hist[0], "history": hist[::-1]})
        except Exception:
            pass

    def _status(self, read: int | None = None, failed: list | None = None) -> None:
        blocks = self.active_blocks()
        fg = (self.store.get("fear_greed") or {}).get("now")
        fg_txt = f"Fear & Greed {fg['value']} ({fg['label']})" if fg else "Fear & Greed n/d"
        day = self.store.query("SELECT COUNT(*) AS n FROM news WHERE ts >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-1 day')")[0]["n"]
        stats = {"fear_greed": fg, "blocks": blocks, "news_24h": day}
        if blocks:
            lst = ", ".join(f"{k} fino alle {datetime.fromtimestamp(v['until']).strftime('%H:%M')}" for k, v in blocks.items())
            self.status("alert", f"Blocco prudenziale attivo: {lst}. {fg_txt}.", stats=stats)
        else:
            extra = f" Fonti non raggiungibili: {', '.join(failed)}." if failed else ""
            self.status("ok", f"Nessun allarme. {fg_txt}.{extra}", stats=stats)

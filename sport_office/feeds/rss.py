"""Lettura di feed RSS/Atom (titolo, link, data), senza dipendenze esterne."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        if el.tag.split("}")[-1] not in ("item", "entry"):
            continue
        get = {c.tag.split("}")[-1]: c for c in el}
        title = (get["title"].text or "").strip() if get.get("title") is not None else ""
        link_el = get.get("link")
        link = (link_el.text or link_el.get("href") or "").strip() if link_el is not None else ""
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

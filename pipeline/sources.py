"""Fetch candidate stories from free feeds. No API keys, stdlib XML parsing."""
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from . import config

FEEDS = [
    ("Onlinekhabar", "https://www.onlinekhabar.com/feed", "news"),
    ("Setopati", "https://www.setopati.com/feed", "politics"),
    ("Ratopati", "https://www.ratopati.com/feed", "breaking"),
    ("Onlinekhabar English", "https://english.onlinekhabar.com/feed", "news"),
    ("Google News Nepal", "https://news.google.com/rss?hl=ne&gl=NP&ceid=NP:ne", "news"),
    ("Google Trends Nepal", "https://trends.google.com/trending/rss?geo=NP", "trending"),
]
UA = {"User-Agent": "Mozilla/5.0 (compatible; NepalCanvasBot/1.0)"}
TAG = re.compile(r"<[^>]+>")


def _text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return html.unescape(TAG.sub(" ", found.text)).strip()
    return ""


def _when(el):
    raw = _text(el, "pubDate", "{http://purl.org/dc/elements/1.1/}date", "updated", "published")
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)


def _story_id(title: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", "", title.lower()).encode()).hexdigest()[:12]


def fetch_feed(name, url, category):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"[sources] {name}: {e}")
        return []
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    out = []
    for it in items[:40]:
        title = _text(it, "title", "{http://www.w3.org/2005/Atom}title")
        if not title:
            continue
        link = _text(it, "link") or (it.find("{http://www.w3.org/2005/Atom}link") or {}).get("href", "")
        body = _text(it, "description", "{http://purl.org/rss/1.0/modules/content/}encoded", "summary",
                     "{http://www.w3.org/2005/Atom}summary")
        traffic = _text(it, "{https://trends.google.com/trending/rss}approx_traffic")
        out.append({
            "id": _story_id(title),
            "source": name,
            "category": category,
            "title": title,
            "link": link,
            "text": body[:1500],
            "traffic": traffic,
            "published": _when(it).isoformat(),
        })
    return out


def fetch_dhm():
    """Department of Hydrology and Meteorology – special bulletins. Page scrape, best effort."""
    try:
        r = requests.get("https://www.mausam.gov.np/", headers=UA, timeout=20)
        text = TAG.sub(" ", r.text)
        m = re.search(r"(विशेष मौसम बुलेटिन|Special Weather Bulletin)(.{0,600})", text)
        if not m:
            return []
        body = re.sub(r"\s+", " ", m.group(0))
        return [{
            "id": _story_id(body[:80]),
            "source": "DHM mausam.gov.np",
            "category": "weather",
            "title": body[:120],
            "link": "https://www.mausam.gov.np/",
            "text": body,
            "traffic": "",
            "published": datetime.now(timezone.utc).isoformat(),
        }]
    except Exception as e:
        print(f"[sources] DHM: {e}")
        return []


def load_posted():
    try:
        return json.loads(config.POSTED.read_text())
    except Exception:
        return []


def candidates():
    posted = load_posted()
    recent_ids = {p["id"] for p in posted}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.MAX_AGE_HOURS)
    seen, out = set(), []
    for name, url, cat in FEEDS:
        for s in fetch_feed(name, url, cat):
            if s["id"] in recent_ids or s["id"] in seen:
                continue
            if datetime.fromisoformat(s["published"]) < cutoff:
                continue
            seen.add(s["id"])
            out.append(s)
    out.extend(s for s in fetch_dhm() if s["id"] not in recent_ids)
    # duplicate-across-outlets count is an importance signal
    titles = [re.sub(r"\W+", " ", s["title"].lower()) for s in out]
    for s, t in zip(out, titles):
        words = set(t.split())
        s["coverage"] = sum(1 for u in titles if len(words & set(u.split())) >= 4)
    print(f"[sources] {len(out)} candidates")
    return out

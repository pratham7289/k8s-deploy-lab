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

# (name, homepage, category, candidate feed URLs). Feeds that fail are auto-discovered from the
# homepage's <link rel="alternate" type="application/rss+xml">. Run `python -m pipeline.sources` to check.
SITES = [
    ("Onlinekhabar", "https://www.onlinekhabar.com", "news", ["https://www.onlinekhabar.com/feed"]),
    ("Onlinekhabar English", "https://english.onlinekhabar.com", "news", ["https://english.onlinekhabar.com/feed"]),
    ("Kantipur", "https://ekantipur.com", "news", ["https://ekantipur.com/rss", "https://ekantipur.com/feed"]),
    ("Setopati", "https://www.setopati.com", "politics", ["https://www.setopati.com/feed"]),
    ("Ratopati", "https://www.ratopati.com", "breaking", ["https://www.ratopati.com/feed"]),
    ("BBC Nepali", "https://www.bbc.com/nepali", "news", ["https://feeds.bbci.co.uk/nepali/rss.xml"]),
    ("The Kathmandu Post", "https://kathmandupost.com", "news", ["https://kathmandupost.com/rss"]),
    ("Ujyaalo Online", "https://ujyaaloonline.com", "news", ["https://ujyaaloonline.com/feed", "https://ujyaaloonline.com/rss"]),
    ("Gorkhapatra", "https://gorkhapatraonline.com", "notice", ["https://gorkhapatraonline.com/rss", "https://gorkhapatraonline.com/feed"]),
    ("Annapurna Post", "https://annapurnapost.com", "news", ["https://annapurnapost.com/rss", "https://annapurnapost.com/feed"]),
    ("Nagarik", "https://nagariknews.nagariknetwork.com", "news", ["https://nagariknews.nagariknetwork.com/feed", "https://nagariknews.nagariknetwork.com/rss"]),
    ("Himal Khabar", "https://www.himalkhabar.com", "news", ["https://www.himalkhabar.com/feed", "https://www.himalkhabar.com/rss"]),
    ("Khabarhub", "https://www.khabarhub.com", "breaking", ["https://www.khabarhub.com/feed/"]),
    ("Baahrakhari", "https://baahrakhari.com", "news", ["https://baahrakhari.com/feed", "https://baahrakhari.com/rss"]),
    ("Nepal Press", "https://www.nepalpress.com", "politics", ["https://www.nepalpress.com/feed/"]),
    ("Lokaantar", "https://www.lokaantar.com", "politics", ["https://www.lokaantar.com/feed", "https://www.lokaantar.com/rss"]),
    ("Nepal News", "https://www.nepalnews.com", "news", ["https://www.nepalnews.com/feed", "https://www.nepalnews.com/rss"]),
    ("Image Khabar", "https://www.imagekhabar.com", "breaking", ["https://www.imagekhabar.com/feed/", "https://www.imagekhabar.com/rss"]),
    ("Google News Nepal", "https://news.google.com", "news", ["https://news.google.com/rss?hl=ne&gl=NP&ceid=NP:ne"]),
    ("Google Trends Nepal", "https://trends.google.com", "trending", ["https://trends.google.com/trending/rss?geo=NP"]),
    # India: used only when the story matters to Nepali viewers (see prompts/score.txt)
    ("ABP News", "https://www.abplive.com", "india", ["https://www.abplive.com/home/feed"]),
    ("NDTV", "https://www.ndtv.com", "india", ["https://feeds.feedburner.com/ndtvnews-top-stories"]),
    ("Aaj Tak", "https://www.aajtak.in", "india", ["https://www.aajtak.in/rssfeeds/?id=home"]),
    ("News18", "https://www.news18.com", "india", ["https://www.news18.com/rss/india.xml"]),
    ("India Today", "https://www.indiatoday.in", "india", ["https://www.indiatoday.in/rss/home"]),
    ("Zee News", "https://zeenews.india.com", "india", ["https://zeenews.india.com/rss/india-national-news.xml"]),
    ("The Hindu", "https://www.thehindu.com", "india", ["https://www.thehindu.com/news/national/feeder/default.rss"]),
    ("The Indian Express", "https://indianexpress.com", "india", ["https://indianexpress.com/feed/"]),
]
FEEDS = [(n, urls[0], c) for n, _, c, urls in SITES]  # kept for older callers
UA = {"User-Agent": "Mozilla/5.0 (compatible; NepalCanvasBot/1.0)"}
TAG = re.compile(r"<[^>]+>")
RAW_DATES = []   # unparsed date strings, shown by the health check
ERRORS = {}      # site -> last error, shown by the health check


def _text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return html.unescape(TAG.sub(" ", found.text)).strip()
    return ""


def _when(el):
    raw = _text(el, "pubDate", "{http://purl.org/dc/elements/1.1/}date", "updated", "published",
                "{http://www.w3.org/2005/Atom}updated", "{http://www.w3.org/2005/Atom}published")
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=config.NPT)).astimezone(timezone.utc)
    except Exception:
        pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})", raw)
    if m:  # bare local timestamp, Nepal sites publish in NPT
        return datetime(*map(int, m.groups()), tzinfo=config.NPT).astimezone(timezone.utc)
    RAW_DATES.append(raw[:60])
    return None  # undated: treated as too old


def _story_id(title: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", "", title.lower()).encode()).hexdigest()[:12]


FEED_LINK = re.compile(r"""<link[^>]+type=["']application/(?:rss|atom)\+xml["'][^>]*>""", re.I)


def _parse(url):
    r = requests.get(url, headers=UA, timeout=12)
    r.raise_for_status()
    root = ET.fromstring(r.content.strip())
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if not items:
        raise ValueError("no items")
    return items


def _discover(home):
    try:
        page = requests.get(home, headers=UA, timeout=12).text
    except Exception:
        return []
    out = []
    for tag in FEED_LINK.findall(page):
        m = re.search(r"""href=["']([^"']+)["']""", tag)
        if m:
            href = html.unescape(m.group(1))
            out.append(href if href.startswith("http") else home.rstrip("/") + "/" + href.lstrip("/"))
    return out


def fetch_site(name, home, category, urls):
    tried = []
    for url in list(urls) + ["__discover__"]:
        if url == "__discover__":
            more = [u for u in _discover(home) if u not in urls]
            if not more:
                break
            for u in more[:3]:
                tried.append(u)
                try:
                    return _items(name, category, _parse(u), u)
                except Exception:
                    continue
            break
        tried.append(url)
        try:
            return _items(name, category, _parse(url), url)
        except Exception as e:
            ERRORS[name] = f"{url[:60]} -> {str(e)[:80]}"
    print(f"[sources] {name}: no working feed ({len(tried)} tried)")
    return []


def fetch_feed(name, url, category):
    return fetch_site(name, url, category, [url])


def _items(name, category, items, url):
    out = []
    for it in items[:40]:
        title = _text(it, "title", "{http://www.w3.org/2005/Atom}title")
        if not title:
            continue
        alink = it.find("{http://www.w3.org/2005/Atom}link")
        link = _text(it, "link") or (alink.get("href", "") if alink is not None else "")
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
            "feed": url,
            "published": (_when(it) or datetime(2000, 1, 1, tzinfo=timezone.utc)).isoformat(),
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


def candidates(max_age_hours=None):
    """Fresh, not-yet-posted stories from all sites (fetched in parallel), newest first."""
    from concurrent.futures import ThreadPoolExecutor
    posted = load_posted()
    recent_ids = {p["id"] for p in posted}
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=10) as ex:
        batches = list(ex.map(lambda site: fetch_site(*site), SITES))
    allitems = [s for b in batches for s in b]
    out = []
    for hours in ([max_age_hours] if max_age_hours else [config.MAX_AGE_HOURS, 24, 36]):
        cutoff = now - timedelta(hours=hours)
        seen, out = set(), []
        for s in allitems:
            if s["id"] in recent_ids or s["id"] in seen or datetime.fromisoformat(s["published"]) < cutoff:
                continue
            seen.add(s["id"]); out.append(s)
        if len([s for s in out if s["category"] != "india"]) >= 15:
            break
    out.extend(s for s in fetch_dhm() if s["id"] not in recent_ids)
    for s in out:
        s["age_h"] = round((now - datetime.fromisoformat(s["published"])).total_seconds() / 3600, 1)
    # duplicate-across-outlets count is an importance signal
    titles = [re.sub(r"\W+", " ", s["title"].lower()) for s in out]
    for s, t in zip(out, titles):
        words = set(t.split())
        s["coverage"] = sum(1 for u in titles if len(words & set(u.split())) >= 4)
    out.sort(key=lambda s: s["published"], reverse=True)
    print(f"[sources] {len(out)} candidates within {hours}h from {sum(1 for b in batches if b)}/{len(SITES)} sites")
    return out


if __name__ == "__main__":  # feed health check
    now = datetime.now(timezone.utc)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=10) as ex:
        res = list(ex.map(lambda site: (site[0], fetch_site(*site)), SITES))
    for name, items in res:
        dated = [datetime.fromisoformat(i["published"]) for i in items]
        newest = min(((now - d).total_seconds() / 3600 for d in dated), default=None)
        print(f"FEED {name:22s} items={len(items):3d} newest={'-' if newest is None else f'{newest:.1f}h'}"
              f" url={items[0]['feed'] if items else '-'}")
    for name, err in ERRORS.items():
        print(f"ERR  {name}: {err}")
    for raw in dict.fromkeys(RAW_DATES):
        print(f"DATE unparsed: {raw!r}")

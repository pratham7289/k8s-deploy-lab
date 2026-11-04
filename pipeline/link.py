"""Turn one news link the owner pastes into a story the normal writer can use.

Works for Nepali, Indian, English or any outlet whose article page can be read without a bot wall.
Sites behind Cloudflare's "Just a moment..." check (Kantipur, Ujyaalo) cannot be read: we fail loudly
instead of guessing, so the owner can send a link from another outlet for the same story.
"""
import html
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from . import sources

NAMES = {  # domain -> name used in the "स्रोत:" credit
    "onlinekhabar.com": "Onlinekhabar", "english.onlinekhabar.com": "Onlinekhabar English",
    "ekantipur.com": "Kantipur", "kathmandupost.com": "The Kathmandu Post", "setopati.com": "Setopati",
    "ratopati.com": "Ratopati", "bbc.com": "BBC", "ujyaaloonline.com": "Ujyaalo Online",
    "gorkhapatraonline.com": "Gorkhapatra", "annapurnapost.com": "Annapurna Post", "nagariknews.nagariknetwork.com": "Nagarik",
    "khabarhub.com": "Khabarhub", "baahrakhari.com": "Baahrakhari", "nepalpress.com": "Nepal Press",
    "lokaantar.com": "Lokaantar", "nepalnews.com": "Nepal News", "imagekhabar.com": "Image Khabar",
    "himalkhabar.com": "Himal Khabar", "ndtv.com": "NDTV", "aajtak.in": "Aaj Tak", "news18.com": "News18",
    "indiatoday.in": "India Today", "zeenews.india.com": "Zee News", "thehindu.com": "The Hindu",
    "indianexpress.com": "The Indian Express", "abplive.com": "ABP", "reuters.com": "Reuters",
    "apnews.com": "AP", "aljazeera.com": "Al Jazeera", "theguardian.com": "The Guardian",
}
META = r'<meta[^>]+(?:property|name)=["\']{}["\'][^>]+content=["\']([^"\']+)'


def _meta(page, key):
    m = re.search(META.format(re.escape(key)), page, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']%s' % re.escape(key), page, re.I)
    return html.unescape(m.group(1)).strip() if m else ""


def _source_name(url):
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    if host in NAMES:
        return NAMES[host]
    for dom, name in NAMES.items():
        if host.endswith("." + dom) or host == dom:
            return name
    return host


BROWSER = {"User-Agent": sources.UA["User-Agent"],
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "ne-NP,ne;q=0.9,en;q=0.8"}


def _paras(page):
    paras = [html.unescape(sources.TAG.sub(" ", p)) for p in re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", page)]
    return [" ".join(p.split()) for p in paras if len(p.split()) >= 6]


def _from_feed(url, title):
    """The outlet's own RSS item for this article (many feeds carry the full text)."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    key = urlparse(url).path.rstrip("/").split("/")[-1]
    for name, home, cat, feeds in sources.SITES:
        if host not in home:
            continue
        for f in feeds:
            try:
                for it in sources._items(name, cat, sources._parse(f), f):
                    if (key and key in it["link"]) or (title and it["title"].strip() == title):
                        return " ".join(sources.TAG.sub(" ", html.unescape(it["text"])).split())
            except Exception as e:
                print(f"[link] feed {f}: {e}")
    return ""


def story_from_link(url: str) -> dict:
    r = requests.get(url, headers=BROWSER, timeout=30)
    if "charset" not in r.headers.get("content-type", "").lower():
        r.encoding = "utf-8"  # most news sites are UTF-8 but omit the charset; requests would guess Latin-1
    page = r.text
    if r.status_code >= 400 or "Just a moment..." in page[:3000]:
        raise RuntimeError(f"{_source_name(url)} blocks automated readers (HTTP {r.status_code}). "
                           "Send a link to the same story from another site.")
    # read title/description/date from the full page first (cleaning below can remove <head>)
    tm = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    title = _meta(page, "og:title") or _meta(page, "twitter:title") or (html.unescape(tm.group(1)).strip() if tm else "")
    title = re.sub(r"\s*(::|[|–])\s*.*$", "", title).strip() if (" :: " in title or " | " in title) else title
    title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip()  # drop " - Outlet" tails
    desc = _meta(page, "og:description") or _meta(page, "description")
    pub = _meta(page, "article:published_time") or _meta(page, "datePublished")
    if not title:
        raise RuntimeError(f"could not find the headline at {url}.")
    raw = page
    page = re.sub(r"(?s)<!--.*?-->", " ", page)  # e.g. Setopati starts with <!--<header>-->
    page = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav|aside)\b.*?</\1>", " ", page)
    body = " ".join(_paras(page))[:6000]
    if len(body) < 300:   # layout tags wrapped the article: take every paragraph instead
        body = " ".join(_paras(re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)))[:6000]
    if len(body) < 300:
        print(f"[link] page {len(raw)} chars, HTTP {r.status_code}, {raw.count('<p')} <p> tags; trying the outlet's feed")
        feed = _from_feed(url, title)
        if len(feed) > len(body):
            body = feed[:6000]
    if len(body) < 300 and len(desc) >= 120:
        body = desc
    if len(body) < 120:
        raise RuntimeError(f"could not read the article text at {url} (only {len(body)} characters).")
    try:
        published = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        published = datetime.now(timezone.utc)
    host = urlparse(url).netloc.lower()
    india = any(d in host for d in ("ndtv", "aajtak", "news18", "indiatoday", "zeenews", "thehindu",
                                     "indianexpress", "abplive", "hindustantimes", "timesofindia"))
    story = {"id": sources._story_id(title), "source": _source_name(url), "title": title, "link": url,
             "category": "india" if india else "news", "text": (desc + " " + body[:1200]).strip(),
             "article": body, "published": published.isoformat()}
    print(f"[link] {story['source']}: {title[:80]} ({len(body)} chars)")
    return story

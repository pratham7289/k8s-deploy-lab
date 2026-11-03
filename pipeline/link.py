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


def story_from_link(url: str) -> dict:
    r = requests.get(url, headers=sources.UA, timeout=30)
    page = r.text
    if r.status_code >= 400 or "Just a moment..." in page[:3000]:
        raise RuntimeError(f"{_source_name(url)} blocks automated readers (HTTP {r.status_code}). "
                           "Send a link to the same story from another site.")
    page = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav|aside)\b.*?</\1>", " ", page)
    title = _meta(page, "og:title") or html.unescape(re.search(r"(?is)<title>(.*?)</title>", page).group(1)).strip()
    title = re.sub(r"\s*[|\-–]\s*[^|\-–]{2,40}$", "", title).strip()  # drop " | Outlet" tails
    desc = _meta(page, "og:description") or _meta(page, "description")
    paras = [html.unescape(sources.TAG.sub(" ", p)) for p in re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", page)]
    paras = [" ".join(p.split()) for p in paras if len(p.split()) >= 6]
    body = " ".join(paras)[:6000]
    if len(body) < 300:
        raise RuntimeError(f"could not read the article text at {url} (only {len(body)} characters).")
    pub = _meta(page, "article:published_time") or _meta(page, "datePublished")
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

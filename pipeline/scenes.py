"""Scene planning: split the script into scenes, each with a visual subject and a preferred source.
Then fetch one image/clip per scene so what is on screen matches what is being said.

Visual sources, most relevant first:
  1. photos used in the English Wikipedia article for the named subject (wiki_title)   -> highly specific
  2. Wikimedia Commons full-text search for the subject                                -> specific
  3. Pixabay photo / video for the generic English query                               -> generic b-roll
All free-licence (PD / CC0 / CC BY / CC BY-SA), credited in the caption.
"""
import json
import random
import re
from pathlib import Path

import requests

from . import config, llm, visuals

COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKI = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "NepalCanvasBot/1.0 (https://facebook.com/nepalcanvasofficial; nepalcanvas22@gmail.com)"}
BAD = re.compile(r"\.(svg|gif|tif|tiff|pdf|djvu|ogv|webm|png)$|map|flag|logo|coat of arms|seal|emblem|diagram|chart|icon|"
                 r"signature|stamp|banner|collage|locator|wordmark|screenshot", re.I)
LICENSE_OK = re.compile(r"public domain|cc0|cc[ -]by(?!-nc)(?!-nd)", re.I)
MIN_W = 1000


def plan(script: str, headline: str, category: str) -> list[dict]:
    res = llm.ask_json(config.prompt("scenes"),
                       json.dumps({"headline": headline, "category": category, "script": script}, ensure_ascii=False),
                       temperature=0.3)
    out = []
    for s in res.get("scenes", [])[:7]:
        if not s.get("text"):
            continue
        out.append({"text": s["text"], "subject": s.get("subject", ""), "wiki_title": s.get("wiki_title", ""),
                    "query_en": s.get("query_en", ""), "source": s.get("source", "stock"), "card": s.get("card", "")})
    return out


def _imageinfo(titles: list[str], n: int):
    """Commons imageinfo for File: titles -> [(image, url, 5, credit)] filtered by size + licence."""
    if not titles:
        return []
    try:
        r = requests.get(COMMONS, headers=UA, timeout=20, params={
            "action": "query", "format": "json", "titles": "|".join(titles[:20]), "prop": "imageinfo",
            "iiprop": "url|extmetadata|size", "iiurlwidth": 1600, "iiextmetadatafilter": "LicenseShortName|Artist"})
        pages = list(r.json().get("query", {}).get("pages", {}).values())
    except Exception as e:
        print(f"[commons] {e}"); return []
    out = []
    for p in pages:
        title = p.get("title", "")
        if BAD.search(title):
            continue
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("width", 0) < MIN_W or ii.get("height", 0) < 600:
            continue
        lic = (ii.get("extmetadata", {}).get("LicenseShortName", {}).get("value") or "")
        if not LICENSE_OK.search(lic):
            continue
        artist = re.sub(r"<[^>]+>", "", ii.get("extmetadata", {}).get("Artist", {}).get("value") or "").strip()[:60]
        name = artist if artist and not re.fullmatch(r"(?i)(myself|own work|self|unknown.*)", artist) \
            else re.sub(r"\.\w+$", "", title[5:])
        out.append(("image", ii.get("thumburl") or ii.get("url"), 5, f"{name} ({lic}, Wikimedia Commons)"))
        if len(out) >= n:
            break
    return out


def wikipedia(title: str, n: int = 3):
    """Photos actually used in the English Wikipedia article about the subject (lead image first)."""
    if not title:
        return []
    try:
        r = requests.get(WIKI, headers=UA, timeout=20, params={
            "action": "query", "format": "json", "redirects": 1, "titles": title,
            "prop": "pageimages|images", "piprop": "name", "imlimit": 40})
        pages = list(r.json().get("query", {}).get("pages", {}).values())
    except Exception as e:
        print(f"[wiki] {e}"); return []
    if not pages or "missing" in pages[0]:
        return []
    p = pages[0]
    titles = []
    lead = p.get("pageimage")
    if lead:
        titles.append("File:" + lead)
    for im in p.get("images", []):
        t = im.get("title", "")
        if t not in titles and re.search(r"\.jpe?g$", t, re.I) and not BAD.search(t):
            titles.append(t)
    res = _imageinfo(titles, n)
    if res:
        print(f"[wiki] {title}: {len(res)} photos")
    return res


def commons(query: str, n: int = 3):
    try:
        r = requests.get(COMMONS, headers=UA, timeout=20, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 20,
            "gsrsearch": f"filetype:bitmap filemime:image/jpeg {query}"})
        pages = list(r.json().get("query", {}).get("pages", {}).values())
    except Exception as e:
        print(f"[commons] {e}"); return []
    titles = [p["title"] for p in sorted(pages, key=lambda p: p.get("index", 0)) if not BAD.search(p.get("title", ""))]
    res = _imageinfo(titles, n)
    if res:
        print(f"[commons] {query}: {len(res)} photos")
    return res


def _stock(q_en: str, kinds=("photos", "videos")):
    cands = []
    for kind in kinds:
        for t, url, dur in visuals.search(q_en, 3, kind):
            cands.append((t, url, dur, "Pixabay"))
        if cands:
            break
    return cands


def fetch_for_scene(scene: dict, workdir: Path, idx: int, want: int = 2, used: set | None = None):
    """Return up to `want` visuals [(kind, path, dur, credit)] for one scene, most relevant first.
    Several shots per scene keep cuts every ~3-4 s instead of one photo held for 15 s."""
    used = used if used is not None else set()
    named = scene.get("source") == "commons" or bool(scene.get("wiki_title"))
    subject, q_en = scene.get("subject") or "", scene.get("query_en") or scene.get("subject") or ""
    if named:
        order = [lambda: wikipedia(scene.get("wiki_title") or subject, 4),
                 lambda: commons(subject, 4),
                 lambda: _stock(q_en, ("videos", "photos"))]
    else:
        order = [lambda: _stock(q_en, ("videos", "photos")),
                 lambda: commons(subject, 3)]
    got = []
    for si, get in enumerate(order):
        if got and named and si == len(order) - 1:
            break  # do not pad a named subject with generic stock
        cands = get()
        if cands and cands[0][3] == "Pixabay":
            random.shuffle(cands)
        for kind, url, dur, credit in cands:
            if url in used:
                continue
            ext = ".mp4" if kind == "video" else ".jpg"
            p = workdir / f"s{idx}_{len(got)}{ext}"
            try:
                visuals.download(url, p)
                if p.stat().st_size > 20_000:
                    used.add(url)
                    got.append((kind, p, dur, credit))
            except Exception as e:
                print(f"[scene {idx}] download failed: {e}")
            if len(got) >= want:
                return got
    return got

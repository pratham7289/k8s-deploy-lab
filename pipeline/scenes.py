"""Scene planning: split the script into scenes, each with a visual subject and a preferred source.
Then fetch one image/clip per scene so what is on screen matches what is being said."""
import json
import random
import re
from pathlib import Path

import requests

from . import config, llm, visuals

COMMONS = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "NepalCanvasBot/1.0 (nepalcanvas22@gmail.com)"}
BAD = re.compile(r"\.(svg|gif|tif|tiff|pdf|djvu|ogv|webm)$|map|flag|logo|coat of arms|seal|emblem|diagram|chart|icon", re.I)


def plan(script: str, headline: str, category: str) -> list[dict]:
    res = llm.ask_json(config.prompt("scenes"),
                       json.dumps({"headline": headline, "category": category, "script": script}, ensure_ascii=False),
                       temperature=0.3)
    scenes = res.get("scenes", [])
    out = []
    for s in scenes[:7]:
        if not s.get("text"):
            continue
        out.append({"text": s["text"], "subject": s.get("subject", ""), "query_en": s.get("query_en", ""),
                    "source": s.get("source", "stock"), "card": s.get("card", "")})
    return out


def commons(query: str, n: int = 3):
    try:
        r = requests.get(COMMONS, headers=UA, timeout=20, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 12,
            "gsrsearch": f"filetype:bitmap {query}", "prop": "imageinfo", "iiprop": "url|extmetadata|size",
            "iiurlwidth": 1280, "iiextmetadatafilter": "LicenseShortName|Artist"})
        pages = list(r.json().get("query", {}).get("pages", {}).values())
    except Exception as e:
        print(f"[commons] {e}"); return []
    out = []
    for p in pages:
        title = p.get("title", "")
        if BAD.search(title):
            continue
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("width", 0) < 800:
            continue
        lic = (ii.get("extmetadata", {}).get("LicenseShortName", {}).get("value") or "")
        if not re.search(r"public domain|cc0|cc by", lic, re.I):
            continue
        artist = re.sub(r"<[^>]+>", "", ii.get("extmetadata", {}).get("Artist", {}).get("value") or "")[:60]
        out.append(("image", ii.get("thumburl") or ii.get("url"), 5, f"{title[5:]} · {artist} · {lic}"))
        if len(out) >= n:
            break
    return out


def fetch_for_scene(scene: dict, workdir: Path, idx: int):
    """Return (kind, path, dur, credit) or None."""
    cands = []
    q_en = scene.get("query_en") or scene.get("subject") or ""
    if scene.get("source") == "commons" and scene.get("subject"):
        cands = commons(scene["subject"])
    if not cands and q_en:
        for kind in ("videos", "photos"):
            for t, url, dur in visuals.search(q_en, 3, kind):
                cands.append((t, url, dur, "Pixabay"))
            if cands:
                break
    if not cands and scene.get("source") != "commons" and scene.get("subject"):
        cands = commons(scene["subject"])
    random.shuffle(cands)
    for kind, url, dur, credit in cands:
        ext = ".mp4" if kind == "video" else ".jpg"
        p = workdir / f"s{idx}{ext}"
        try:
            visuals.download(url, p)
            if p.stat().st_size > 20_000:
                return (kind, p, dur, credit)
        except Exception as e:
            print(f"[scene {idx}] download failed: {e}")
    return None

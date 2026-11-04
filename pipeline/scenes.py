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
    if isinstance(res, list):
        res = {"scenes": res}
    out = []
    for s in res.get("scenes", [])[:7]:
        if not s.get("text"):
            continue
        out.append({"text": s["text"], "subject": s.get("subject", ""), "wiki_title": s.get("wiki_title", ""),
                    "query_en": s.get("query_en", ""), "source": s.get("source", "stock"), "card": s.get("card", ""),
                    "map": s.get("map", ""), "map_ne": s.get("map_ne", ""), "video_query": s.get("video_query", ""),
                    "number": s.get("number") if isinstance(s.get("number"), dict) else None})
    return out


PERSON = re.compile(r"(?i)\b(people|portraits?|politicians?|births|deaths|men|women|persons?|speeches|speakers|"
                    r"ministers?|members of|leaders?|actors?|actresses|singers?|players?|cricketers?|judges?|"
                    r"officials?|activists?|journalists?|presidents?|chairm[ae]n|crowds?|faces?|selfies?|families)\b")


def _imageinfo(titles: list[str], n: int):
    """Commons imageinfo for File: titles -> [(image, url, 5, credit)] filtered by size + licence."""
    if not titles:
        return []
    try:
        r = requests.get(COMMONS, headers=UA, timeout=20, params={
            "action": "query", "format": "json", "titles": "|".join(titles[:20]), "prop": "imageinfo|categories",
            "cllimit": "max", "iiprop": "url|extmetadata|size", "iiurlwidth": 1600,
            "iiextmetadatafilter": "LicenseShortName|Artist|ImageDescription"})
        pages = list(r.json().get("query", {}).get("pages", {}).values())
    except Exception as e:
        print(f"[commons] {e}"); return []
    out = []
    for p in pages:
        title = p.get("title", "")
        if BAD.search(title):
            continue
        ii = (p.get("imageinfo") or [{}])[0]
        cats = " ".join(c.get("title", "") for c in p.get("categories", []))
        desc = re.sub(r"<[^>]+>", " ", ii.get("extmetadata", {}).get("ImageDescription", {}).get("value") or "")
        if PERSON.search(cats) or PERSON.search(desc[:300]):
            continue  # never show a real person: a wrong face beside a crime/court story is defamation
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


def _videos(q: str, n: int):
    """Real moving footage (vertical stock video) for a query."""
    if not q:
        return []
    out = [(t, url, dur, "Pixabay") for t, url, dur in visuals.search(q, n + 4, "videos") if t == "video"]
    random.shuffle(out)
    return out


CHECK = """You are the picture editor of a Nepali TV news Reel. This picture would be shown while the presenter says:
"{text}"
(story headline: "{headline}"; picture subject wanted: "{subject}")
Reject the picture (ok=false) if ANY of these is true:
- a real person's face is clearly recognisable (close-up or medium shot of a specific person). Distant crowds, hands, backs are fine.
- it shows fire, riots, burning buildings, blood, weapons or violence, unless the sentence is about exactly that
- a foreign country's flag, landmark or city when the story is about Nepal
- big readable text, words, dates or numbers in the image (e.g. a calendar, a sign) that are not about the story
- a concert, party, nightclub, sports or entertainment scene when the story is not about that
- religious paintings, cartoons, illustrations or memes
- it is plainly unrelated to the sentence, or would mislead a viewer about what happened
Otherwise ok=true. Return only JSON: {{"ok": true|false, "why": "<5 words>"}}"""


def _frame(path: Path) -> bytes | None:
    import subprocess
    if path.suffix == ".jpg":
        src = ["-i", str(path)]
    else:
        src = ["-ss", "1.5", "-i", str(path)]
    try:
        return subprocess.run(["ffmpeg", "-v", "error", *src, "-frames:v", "1", "-vf", "scale=512:-2",
                               "-f", "image2", "-c:v", "mjpeg", "-"], capture_output=True, timeout=30).stdout or None
    except Exception:
        return None


_CHECK = {"start": None, "calls": 0, "fails": 0}
CHECK_BUDGET_S = 300   # at most 5 min of picture checks per Reel, so the job never runs out of time
CHECK_MAX_CALLS = 45
CHECK_MAX_FAILS = 4    # Gemini busy/quota: stop checking for this Reel, keep the pictures


def suitable(path: Path, scene: dict, headline: str) -> bool:
    """Gemini looks at the picture before it is used. Fails open (keeps it) if the check errors or is over budget."""
    import time
    if not config.GEMINI_API_KEY:
        return True
    st = _CHECK
    st["start"] = st["start"] or time.time()
    if (time.time() - st["start"] > CHECK_BUDGET_S or st["calls"] >= CHECK_MAX_CALLS
            or st["fails"] >= CHECK_MAX_FAILS):
        return True
    img = _frame(path)
    if not img:
        return True
    st["calls"] += 1
    try:
        res = llm.ask_image(CHECK.format(text=scene.get("text", "")[:400], headline=headline[:150],
                                         subject=scene.get("subject", "")), img)
    except Exception as e:
        st["fails"] += 1
        print(f"[check] skipped: {e}")
        if st["fails"] >= CHECK_MAX_FAILS:
            print("[check] too many errors, picture check off for this Reel")
        return True
    ok = bool(res.get("ok", True))
    print(f"[check] {'ok' if ok else 'rejected'} {path.name}: {res.get('why', '')}")
    return ok


def fetch_for_scene(scene: dict, workdir: Path, idx: int, want: int = 2, used: set | None = None, headline: str = ""):
    """Return up to `want` visuals [(kind, path, dur, credit)] for one scene.
    Mix: about half real moving footage (stock video for `video_query`), the rest the most relevant
    photos of the named subject. Scene 0 opens on motion so the first second is not a still."""
    used = used if used is not None else set()
    named = scene.get("source") == "commons" or bool(scene.get("wiki_title"))
    subject, q_en = scene.get("subject") or "", scene.get("query_en") or scene.get("subject") or ""
    vq = scene.get("video_query") or q_en
    n_vid = max(1, want // 2) if (named and want > 1) or idx == 0 else want
    if not named:
        n_vid = want

    def take(cands, limit, got):
        for kind, url, dur, credit in cands:
            if len(got) >= limit:
                break
            if url in used:
                continue
            ext = ".mp4" if kind == "video" else ".jpg"
            p = workdir / f"s{idx}_{len(got)}{ext}"
            try:
                visuals.download(url, p)
                used.add(url)
                if p.stat().st_size > 20_000 and suitable(p, scene, headline):
                    got.append((kind, p, dur, credit))
            except Exception as e:
                print(f"[scene {idx}] download failed: {e}")
        return got

    vids = take(_videos(vq, n_vid), n_vid, [])
    if len(vids) < n_vid and q_en != vq:
        vids = take(_videos(q_en, n_vid), n_vid, vids)
    photos = []
    need = want - len(vids)
    if need > 0:
        sources_ = ([lambda: wikipedia(scene.get("wiki_title") or subject, 6), lambda: commons(subject, 6)]
                    if named else [lambda: _stock(q_en, ("photos",)), lambda: commons(subject, 5)])
        for get in sources_:
            photos = take(get(), need, photos)
            if len(photos) >= need:
                break
    if not vids and not photos:  # everything rejected: neutral Nepal footage beats a wrong picture
        vids = take(_videos("Kathmandu city aerial", 1), 1, [])
    # named subject: its photo first (relevance), then footage; scene 0 / generic: footage first (motion)
    got = (photos + vids) if (named and idx > 0) else (vids + photos)
    print(f"[scene {idx}] {sum(1 for g in got if g[0] == 'video')} video + {sum(1 for g in got if g[0] != 'video')} photo")
    return got[:want]

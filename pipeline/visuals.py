"""Pexels b-roll + Devanagari text cards rendered with Pillow."""
import random
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from . import config

W, H = 1080, 1920
NAVY, NAVY2, CREAM, RED, GREY = (31, 42, 68), (59, 74, 107), (246, 241, 231), (220, 20, 60), (201, 208, 224)
TEMPLATES = ["navy", "cream", "split"]


def _font(size, latin=False):
    p = config.FONT_LATIN if latin else config.FONT
    try:
        f = ImageFont.truetype(str(p), size, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        f = ImageFont.truetype(str(p), size)
    try:
        f.set_variation_by_name("Bold")  # variable font axis
    except Exception:
        pass
    return f


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur); cur = word
    if cur:
        lines.append(cur)
    return lines


def card(text: str, path: Path, template: str, kicker: str = "", small: str = "", logo: Path | None = None):
    bg, fg, acc = {"navy": (NAVY, CREAM, RED), "cream": (CREAM, NAVY, RED), "split": (NAVY2, CREAM, RED)}[template]
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 28], fill=acc)
    if kicker:
        d.text((80, 140), kicker, font=_font(44), fill=acc)
    f = _font(84)
    lines = _wrap(d, text, f, W - 160)[:6]
    y = H // 2 - len(lines) * 55
    for ln in lines:
        d.text((80, y), ln, font=f, fill=fg)
        y += 110
    if small:
        d.text((80, H - 260), small, font=_font(38), fill=GREY if template != "cream" else NAVY2)
    d.text((80, H - 160), "NEPAL CANVAS", font=_font(40, latin=True), fill=acc)
    im.save(path)
    return path


def pexels(keyword: str, n: int = 2, kind: str = "videos"):
    if not config.PEXELS_API_KEY:
        return []
    url = f"https://api.pexels.com/v1/{kind}/search" if kind == "videos" else "https://api.pexels.com/v1/search"
    r = requests.get(url, headers={"Authorization": config.PEXELS_API_KEY},
                     params={"query": keyword, "orientation": "portrait", "per_page": 8, "size": "medium"}, timeout=20)
    if r.status_code != 200:
        print(f"[pexels] {r.status_code} for {keyword}")
        return []
    items = r.json().get("videos" if kind == "videos" else "photos", [])
    random.shuffle(items)
    out = []
    for it in items[:n]:
        if kind == "videos":
            files = sorted((f for f in it["video_files"] if f.get("height", 0) >= f.get("width", 0)),
                           key=lambda f: abs(f.get("height", 0) - 1920))
            if files:
                out.append(("video", files[0]["link"], it.get("duration", 10)))
        else:
            out.append(("image", it["src"]["portrait"], 5))
    return out


def download(url: str, path: Path):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return path


def pixabay(keyword: str, n: int = 2, kind: str = "videos"):
    key = config.PIXABAY_API_KEY
    if not key:
        return []
    url = "https://pixabay.com/api/videos/" if kind == "videos" else "https://pixabay.com/api/"
    params = {"key": key, "q": keyword, "per_page": 10, "safesearch": "true", "order": "popular"}
    params["orientation"] = "vertical"
    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        print(f"[pixabay] {r.status_code} for {keyword}")
        return []
    hits = r.json().get("hits", [])
    random.shuffle(hits)
    out = []
    for h in hits[:n]:
        if kind == "videos":
            v = h["videos"].get("medium") or h["videos"].get("small") or h["videos"].get("large")
            if v and v.get("url"):
                out.append(("video", v["url"], h.get("duration", 10)))
        else:
            out.append(("image", h.get("largeImageURL") or h.get("webformatURL"), 5))
    return out


def search(keyword: str, n: int, kind: str):
    """Pixabay first (new Pexels keys are paused as of Oct 2026); Pexels if a key exists."""
    res = pixabay(keyword, n, kind)
    return res or pexels(keyword, n, kind)


def broll(keywords: list[str], workdir: Path, want: int = 4):
    clips = []
    for kw in keywords:
        for kind in ("videos", "photos"):
            for t, url, dur in search(kw, 2, kind):
                ext = ".mp4" if t == "video" else ".jpg"
                p = workdir / f"b{len(clips)}{ext}"
                try:
                    download(url, p); clips.append((t, p, dur))
                except Exception as e:
                    print(f"[broll] download failed: {e}")
                if len(clips) >= want:
                    return clips
    return clips

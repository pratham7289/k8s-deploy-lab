"""Branded info cards: a big-number card and a district locator map.

Map data: geoBoundaries (CC BY 4.0) Nepal ADM2 districts, simplified, fetched once at run time into
assets/maps/ (the workflow commits it back, so later runs work offline).
The map is a REGIONAL locator: the target district and its neighbours, not the national outline,
and it is skipped near Darchula so we never draw a disputed national border.
"""
import difflib
import json
import math
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from . import config, visuals

W, H = 1080, 1920
MAPS = config.ASSETS / "maps"
ADM2 = MAPS / "npl_adm2.geojson"
API = "https://www.geoboundaries.org/api/current/gbOpen/NPL/ADM2/"
BG, LAND, EDGE, HI, TXT, SUB = (16, 22, 38), (52, 64, 92), (96, 110, 140), (220, 20, 60), (255, 255, 255), (190, 198, 214)
SKIP = {"darchula"}  # never draw near the disputed north-west border
ALIASES = {"kavre": "kavrepalanchok", "kavrepalanchowk": "kavrepalanchok", "sindhupalchowk": "sindhupalchok",
           "nawalparasi": "nawalparasi east", "rukum": "rukum east", "dhanusha": "dhanusa",
           "chitwan": "chitawan", "kapilbastu": "kapilvastu", "terhathum": "tehrathum", "makwanpur": "makawanpur"}


def _key(s: str) -> str:
    return re.sub(r"[^a-z ]", "", (s or "").lower().replace("district", "")).strip()


def _load():
    if not ADM2.exists():
        MAPS.mkdir(parents=True, exist_ok=True)
        meta = requests.get(API, timeout=20).json()
        url = meta.get("simplifiedGeometryGeoJSON") or meta["gjDownloadURL"]
        ADM2.write_bytes(requests.get(url, timeout=60).content)
        print(f"[maps] downloaded {ADM2.stat().st_size // 1024} KB from geoBoundaries")
    gj = json.loads(ADM2.read_text())
    out = {}
    for f in gj["features"]:
        name = f["properties"].get("shapeName", "")
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out[_key(name)] = [p[0] for p in polys]  # outer rings only
    return out


def _centroid(rings):
    pts = [pt for r in rings for pt in r]
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def find(name: str, districts: dict):
    k = ALIASES.get(_key(name), _key(name))
    if k in districts:
        return k
    m = difflib.get_close_matches(k, list(districts), n=1, cutoff=0.8)
    if m:
        return m[0]
    for d in districts:  # "nawalparasi east" vs "nawalparasi"
        if k and (d.startswith(k) or k.startswith(d)):
            return d
    return None


def map_card(district_en: str, label_ne: str, sub_ne: str, path: Path):
    """Locator card or None if the district is unknown / near the disputed border / data unavailable."""
    try:
        ds = _load()
    except Exception as e:
        print(f"[maps] no map data: {e}")
        return None
    tk = find(district_en, ds)
    if not tk:
        print(f"[maps] district not found: {district_en}")
        return None
    cx, cy = _centroid(ds[tk])
    near = [k for k, r in ds.items() if math.hypot(_centroid(r)[0] - cx, _centroid(r)[1] - cy) < 1.05]
    if SKIP & set(near):
        print(f"[maps] skipped {tk}: near disputed border")
        return None
    pts = [pt for k in near for r in ds[k] for pt in r]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    kx = math.cos(math.radians(cy))  # degrees of longitude are shorter
    box_w, box_h, top = W - 120, 520, 540
    s = min(box_w / ((x1 - x0) * kx), box_h / (y1 - y0))
    ox = 60 + (box_w - (x1 - x0) * kx * s) / 2
    oy = top + (box_h - (y1 - y0) * s) / 2

    def xy(p):
        return ox + (p[0] - x0) * kx * s, oy + (y1 - p[1]) * s

    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 28], fill=HI)
    for k in near:
        for r in ds[k]:
            d.polygon([xy(p) for p in r], fill=HI if k == tk else LAND, outline=EDGE if k != tk else (255, 255, 255))
    tx, ty = xy((cx, cy))
    d.ellipse([tx - 16, ty - 16, tx + 16, ty + 16], fill=(255, 255, 255), outline=HI, width=6)
    d.text((80, 175), "कहाँ?", font=visuals._font(46), fill=HI)
    d.text((80, 240), label_ne or district_en, font=visuals._font(104), fill=TXT)
    if sub_ne:
        d.text((80, 395), sub_ne, font=visuals._font(46), fill=SUB)
    d.text((80, H - 120), "नक्सा: geoBoundaries (CC BY 4.0)", font=visuals._font(28), fill=(120, 130, 150))
    im.save(path)
    return path


def number_card(value: str, label: str, path: Path):
    """One big number with what it means."""
    if not value:
        return None
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 28], fill=HI)
    size = 300 if len(value) <= 4 else 230 if len(value) <= 7 else 160
    f = visuals._font(size)
    while d.textlength(value, font=f) > W - 140 and size > 90:
        size -= 20; f = visuals._font(size)
    vw = d.textlength(value, font=f)
    y = 330
    d.text(((W - vw) / 2, y), value, font=f, fill=TXT)
    d.rectangle([(W - 160) / 2, y + size + 80, (W + 160) / 2, y + size + 92], fill=HI)
    fl = visuals._font(64)
    yy = y + size + 150
    for ln in visuals._wrap(d, label or "", fl, W - 180)[:3]:
        lw = d.textlength(ln, font=fl)
        d.text(((W - lw) / 2, yy), ln, font=fl, fill=SUB); yy += 90
    im.save(path)
    return path

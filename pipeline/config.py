"""Central settings. Everything tunable lives here or in prompts/."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
ASSETS = ROOT / "assets"
PROMPTS = ROOT / "prompts"
EVERGREEN = ROOT / "evergreen"
POSTED = ROOT / "posted.json"

NPT = timezone(timedelta(hours=5, minutes=45))

MODE = os.getenv("MODE", "publish")  # publish | draft | dry
MIN_SCORE = int(os.getenv("MIN_SCORE", "60"))
MAX_POSTS_24H = int(os.getenv("MAX_POSTS_24H", "25"))  # Meta cap is 30
MAX_AGE_HOURS = 36
RECENT_DAYS = 7

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]  # fallback order
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "")
GRAPH = "https://graph.facebook.com/v23.0"
RUPLOAD = "https://rupload.facebook.com/video-upload/v23.0"

VOICES = ["ne-NP-HemkalaNeural", "ne-NP-SagarNeural"]
FONT = ASSETS / "fonts" / "NotoSansDevanagari.ttf"
FONT_LATIN = ASSETS / "fonts" / "NotoSans.ttf"

BASE_HASHTAGS = ["#Nepal", "#NepalNews", "#NepalCanvas"]

# slot -> categories the scorer prefers, and the fallback kind
SLOTS = {
    "morning":    {"prefer": ["news", "weather"],        "fallback": "evergreen"},
    "politics":   {"prefer": ["politics"],               "fallback": "any"},
    "breaking":   {"prefer": ["breaking", "accident", "disaster"], "fallback": "trending"},
    "publicinfo": {"prefer": ["publicinfo", "notice"],   "fallback": "evergreen"},
    "evening":    {"prefer": ["news", "politics"],       "fallback": "evergreen"},
}


def slot_for_now() -> str:
    forced = os.getenv("SLOT", "").strip()
    if forced:
        return forced
    h = datetime.now(NPT).hour
    if h < 9:
        return "morning"
    if h < 12:
        return "politics"
    if h < 15:
        return "breaking"
    if h < 17:
        return "publicinfo"
    return "evening"


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")

"""What is Nepal talking about right now?

Two free signals:
  1. Newsroom heat: the same names/words appearing in headlines of many different outlets in the
     last few hours (a story every newsroom is covering is a story people are following).
  2. Social heat: X (Twitter) trends for Nepal (trends24.in), Reddit r/Nepal hot posts, and the
     Google Trends Nepal feed (already a source; its titles are passed in).

annotate() gives every candidate a numeric `trend` score and returns a short context
(hot topics + social terms) that the scoring model uses to match Nepali and English spellings
(e.g. "बालेन" and "#BalenShah").
"""
import html
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
WORD = re.compile(r"[ऀ-ॿ]+|[A-Za-z]{3,}")
SUFFIXES = sorted(["हरूलाई", "हरूको", "हरूले", "हरूमा", "हरू", "हरु", "लाई", "बाट", "सँग", "द्वारा", "प्रति", "भित्र",
                   "माथि", "तिर", "विरुद्ध", "को", "का", "की", "ले", "मा"], key=len, reverse=True)
STOP = set("""र पनि छ छन् हो थियो थिए गर्न गरेको गरेका भएको भएका गर्ने गर्दै गरे गरी हुने हुन भने भन्दै भन्छन् यो त्यो
यस उक्त एक दुई तीन नयाँ आज हिजो भोलि अब अझै सबै धेरै केही कुनै लागि अनुसार बीच बारे पछि अघि पहिलो वर्ष दिन
समाचार नेपाल नेपाली सरकार मन्त्री प्रदेश जिल्ला काठमाडौं विरुद्ध सम्बन्धी गरियो दिए दिने लिए माग निर्णय कार्यक्रम
the and for with from that this have has are was were will after over into amid says said news nepal nepali""".split())
NEPAL_CATS = None  # every category except "india" counts toward newsroom heat


def stem(w: str) -> str:
    w = w.lower()
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            return w[: -len(suf)]
    return w


def tokens(text: str) -> set:
    return {t for t in (stem(w) for w in WORD.findall(text or "")) if t not in STOP and len(t) >= 2}


def _x_trends():
    try:
        page = requests.get("https://trends24.in/nepal/", headers=UA, timeout=12).text
        terms = re.findall(r'href="https://(?:twitter|x)\.com/search\?q=[^"]*"[^>]*>([^<]{2,60})</a>', page)
        return list(dict.fromkeys(html.unescape(t).strip() for t in terms))[:25]
    except Exception as e:
        print(f"[trending] X trends: {e}")
        return []


def _reddit():
    try:
        r = requests.get("https://www.reddit.com/r/Nepal/hot/.rss?limit=25",
                         headers={"User-Agent": "NepalCanvas/1.0 (news explainer page)"}, timeout=12)
        titles = re.findall(r"<title>([^<]{8,200})</title>", r.text)[1:]
        return [html.unescape(t) for t in titles][:20]
    except Exception as e:
        print(f"[trending] Reddit: {e}")
        return []


def annotate(cands: list, posted: list, window_h: float = 8.0):
    """Add s['trend'] (0-100) to each candidate; drop repeats of an event already posted in 12 h.
    Returns (kept_candidates, context_dict)."""
    now = datetime.now(timezone.utc)
    # 1) newsroom heat: stemmed word -> set of outlets using it in recent headlines
    heat = defaultdict(set)
    for s in cands:
        if s.get("category") == "india" or s.get("age_h", 99) > window_h:
            continue
        for t in tokens(s["title"]):
            heat[t].add(s["source"])
    hot = {t: len(srcs) for t, srcs in heat.items() if len(srcs) >= 3}
    top = sorted(hot.items(), key=lambda kv: -kv[1])[:20]

    # 2) social heat
    x_terms = _x_trends()
    reddit = _reddit()
    google = [s["title"] for s in cands if s.get("source") == "Google Trends Nepal"][:15]
    social_tokens = set()
    for t in x_terms + reddit + google:
        social_tokens |= tokens(t.replace("#", " ").replace("_", " "))

    # 3) do not repeat an event posted in the last 12 h (same main words)
    recent = []
    for p in posted:
        try:
            if now - datetime.fromisoformat(p["published_at"]) < timedelta(hours=12):
                recent.append(tokens(p.get("title", "")))
        except Exception:
            pass
    kept = []
    for s in cands:
        tk = tokens(s["title"])
        if any(tk and len(tk & r) >= max(2, 0.5 * len(tk)) for r in recent):
            continue
        score = sum(min(hot.get(t, 0), 10) for t in tk)
        score += 8 * len(tk & social_tokens)
        s["trend"] = min(100, score * 3)
        kept.append(s)
    ctx = {"hot_topics": [f"{t} ({n} outlets)" for t, n in top],
           "x_trends_nepal": x_terms[:20], "reddit_r_nepal": reddit[:12], "google_trends": google[:12]}
    print(f"[trending] hot={len(hot)} x={len(x_terms)} reddit={len(reddit)} google={len(google)} "
          f"dropped_repeats={len(cands) - len(kept)} top={[t for t, _ in top[:8]]}")
    return kept, ctx

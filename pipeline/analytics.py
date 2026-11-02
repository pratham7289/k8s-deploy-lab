"""Per-Reel numbers so we can see which topics, hooks and lengths work.

stats.json: {video_id: {title, slot, published_at, updated, plays, reach, avg_watch_s, likes, comments, shares}}
Best effort: any field the API refuses is simply left out. Run after each post (last ~10 days).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import config

STATS = Path(__file__).resolve().parent.parent / "stats.json"
METRICS = {"fb_reels_total_plays": "plays", "blue_reels_play_count": "plays", "post_impressions_unique": "reach",
           "post_video_avg_time_watched": "avg_watch_ms", "post_video_views": "views_3s"}
_ERR = []


def _get(path, tok, **params):
    r = requests.get(f"{config.GRAPH}/{path}", params={**params, "access_token": tok}, timeout=30)
    return r.json() if r.ok else {"error": r.text[:200]}


def video_stats(vid: str, tok: str) -> dict:
    out = {}
    for name, key in METRICS.items():  # one call per metric, so one refused metric does not kill the rest
        if key in out:
            continue
        d = _get(f"{vid}/video_insights", tok, metric=name)
        try:
            out[key] = d["data"][0]["values"][0]["value"]
        except Exception:
            if len(_ERR) < 3:
                _ERR.append(f"{name}: {str(d)[:220]}")
                print(f"[analytics] {_ERR[-1]}")
    if "avg_watch_ms" in out:
        out["avg_watch_s"] = round(out.pop("avg_watch_ms") / 1000, 1)
    d = _get(vid, tok, fields="likes.summary(true).limit(0),comments.summary(true).limit(0),length")
    try:
        out["likes"] = d["likes"]["summary"]["total_count"]
    except Exception:
        pass
    try:
        out["comments"] = d["comments"]["summary"]["total_count"]
    except Exception:
        pass
    if d.get("length"):
        out["length_s"] = round(float(d["length"]), 1)
    return out


def update(posted: list, tok: str, days: int = 10) -> dict:
    try:
        stats = json.loads(STATS.read_text())
    except Exception:
        stats = {}
    now = datetime.now(timezone.utc)
    n = 0
    for p in posted:
        vid = p.get("fb_video_id")
        if not vid or p.get("state") == "DRAFT":
            continue
        try:
            at = datetime.fromisoformat(p["published_at"])
        except Exception:
            continue
        if now - at > timedelta(days=days) or at > now:
            continue
        s = video_stats(vid, tok)
        stats[vid] = {**stats.get(vid, {}), "title": p.get("title", ""), "slot": p.get("slot_key") or p.get("slot"),
                      "published_at": p["published_at"], "updated": now.isoformat(timespec="minutes"), **s}
        n += 1
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[analytics] updated {n} reels")
    for vid, s in sorted(stats.items(), key=lambda kv: kv[1].get("published_at", ""))[-12:]:
        print(f"[analytics] {s.get('published_at', '')[:16]} plays={s.get('plays', '-')} reach={s.get('reach', '-')} "
              f"watch={s.get('avg_watch_s', '-')}s likes={s.get('likes', '-')} | {s.get('title', '')[:50]}")
    return stats

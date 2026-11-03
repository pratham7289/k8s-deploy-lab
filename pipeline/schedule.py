"""Fixed daily posting slots, independent of when GitHub actually starts the job.

GitHub's cron can start late, so the job runs before each slot (attempts at about 100, 60 and 25 minutes
before), renders the Reel and hands it to Facebook with scheduled_publish_time = the exact slot time.
Facebook then publishes on the minute. A slot that is already done (posted.json has its key) is skipped,
so the extra attempts cost almost nothing. A slot missed completely is still posted up to LATE_MAX
minutes late rather than dropped.

Stdlib only: `python -m pipeline.schedule` runs before any dependencies are installed and prints
key/time/kind to $GITHUB_OUTPUT (or 'none').
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

NPT = timezone(timedelta(hours=5, minutes=45))
# 10 a day at the hours Nepali Facebook is busiest: morning, lunch, evening
KINDS = {7: "morning", 8: "politics", 10: "breaking", 12: "publicinfo", 13: "politics",
         15: "breaking", 17: "publicinfo", 19: "evening", 20: "politics", 21: "evening"}
SLOTS = [(h, 0, k) for h, k in KINDS.items()]
BREAK_HOURS = range(6, 22)   # extra breaking Reels only while people are awake
BREAK_GAP = 180              # minutes between two breaking Reels
BREAK_PER_DAY = 3            # at most 3 genuinely breaking Reels a day
BREAK_MAX_24H = 25           # total posts in 24 h before breaking checks stop (Meta cap is 30)
AHEAD_MAX = 58    # prepare a slot at most ~1 h before it (cron-job.org runs at :05 and :35)
LATE_MAX = 40     # still post a missed slot up to 40 min late (next slot is 1 h later)
MIN_LEAD = 15     # Facebook needs the scheduled time >= 10 min ahead; below this publish immediately
POSTED = Path(__file__).resolve().parent.parent / "posted.json"


def load_posted():
    try:
        return json.loads(POSTED.read_text())
    except Exception:
        return []


def due_slot(posted, now=None):
    """(key, slot_time, kind) of the earliest slot that is not done and is in the work window, else None."""
    now = now or datetime.now(NPT)
    done = {p.get("slot_key") for p in posted if p.get("slot_key")}
    today = now.astimezone(NPT).date()
    for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
        for h, m, kind in SLOTS:
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=NPT)
            key = t.strftime("%Y-%m-%d-%H%M")
            delta = (t - now).total_seconds() / 60
            if key not in done and -LATE_MAX <= delta <= AHEAD_MAX:
                return key, t, kind
    return None


def breaking_due(posted, now=None):
    """A breaking check is allowed when no slot is due, during waking hours, not too soon after the last one."""
    now = now or datetime.now(NPT)
    if now.astimezone(NPT).hour not in BREAK_HOURS:
        return None
    recent = []
    for p in posted:
        try:
            recent.append((datetime.fromisoformat(p["published_at"]), p.get("slot_key") or ""))
        except Exception:
            pass
    if sum(1 for t, _ in recent if now - t < timedelta(hours=24)) >= BREAK_MAX_24H:
        return None
    if any(k.startswith("brk-") and now - t < timedelta(minutes=BREAK_GAP) for t, k in recent):
        return None
    if sum(1 for t, k in recent if k.startswith("brk-") and now - t < timedelta(hours=24)) >= BREAK_PER_DAY:
        return None
    return "brk-" + now.astimezone(NPT).strftime("%Y-%m-%d-%H%M")


def main():
    posted = load_posted()
    slot = due_slot(posted)
    brk = None if slot else breaking_due(posted)
    out = os.getenv("GITHUB_OUTPUT")
    if slot:
        lines = ["due=yes", f"key={slot[0]}", f"time={slot[1].isoformat()}", f"kind={slot[2]}", "breaking=0"]
    elif brk:
        lines = ["due=yes", f"key={brk}", "time=", "kind=breaking", "breaking=1"]
    else:
        lines = ["due=none"]
    print("[schedule] " + " ".join(lines))
    if out:
        with open(out, "a") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()

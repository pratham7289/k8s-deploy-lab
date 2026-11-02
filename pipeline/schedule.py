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
SLOTS = [(7, 0, "morning"), (9, 0, "politics"), (11, 0, "breaking"), (13, 0, "breaking"),
         (15, 0, "publicinfo"), (17, 0, "politics"), (20, 0, "evening")]  # 7 a day
AHEAD_MAX = 110   # prepare a slot at most ~1 h 50 m before it, so the news is fresh
LATE_MAX = 60     # still post a missed slot up to 1 h late (next slot is 2 h later)
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


def main():
    slot = due_slot(load_posted())
    out = os.getenv("GITHUB_OUTPUT")
    lines = ["due=none"] if not slot else ["due=yes", f"key={slot[0]}", f"time={slot[1].isoformat()}", f"kind={slot[2]}"]
    print("[schedule] " + " ".join(lines))
    if out:
        with open(out, "a") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()

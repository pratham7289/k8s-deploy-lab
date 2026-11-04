"""Claim a slot in git BEFORE any work, so a retry can never post the same slot twice.

Facebook is the point of no return: once a Reel is uploaded it exists even if the job is killed
before it records it. So the slot is marked CLAIMED and pushed first. A run that ends without
uploading marks it RELEASED (a later attempt may try again); a run killed mid-way leaves it CLAIMED,
and the slot is skipped (a missed slot is better than a duplicate).

  python scripts/claim.py <slot_key>   -> prints claimed=yes|no to $GITHUB_OUTPUT
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTED = ROOT / "posted.json"


def main(key):
    posted = json.loads(POSTED.read_text()) if POSTED.exists() else []
    taken = [p for p in posted if p.get("slot_key") == key and p.get("state") != "RELEASED"]
    ok = not taken
    if ok:
        posted = [p for p in posted if not (p.get("slot_key") == key and p.get("state") == "RELEASED")]
        posted.append({"id": f"claim-{key}", "title": "", "slot_key": key, "state": "CLAIMED",
                       "claimed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "run": os.getenv("GITHUB_RUN_ID", "")})
        POSTED.write_text(json.dumps(posted, ensure_ascii=False, indent=1), encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "save_state.py"), f"claim {key}"])
        ok = r.returncode == 0   # could not push the claim -> do not risk a duplicate
    print(f"[claim] {key}: {'claimed' if ok else 'already taken, skipping'}")
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"claimed={'yes' if ok else 'no'}\n")


if __name__ == "__main__":
    main(sys.argv[1])

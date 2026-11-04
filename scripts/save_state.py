"""Commit posted.json / stats.json / token.enc / maps safely when several runs finish at once.

Our copy is merged into the newest origin/main (posted: union by video id, ours wins; stats: dict update),
then pushed; on a race we fetch again and retry. Plain `git pull --rebase` conflicts on JSON appends.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["posted.json", "stats.json", "token.enc"]


def git(*a, check=True):
    return subprocess.run(["git", *a], cwd=ROOT, check=check, capture_output=True, text=True)


def load(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def main(msg):
    keep = Path(tempfile.mkdtemp())
    for f in FILES:
        if (ROOT / f).exists():
            shutil.copy(ROOT / f, keep / f)
    maps = ROOT / "assets" / "maps"
    if maps.exists():
        shutil.copytree(maps, keep / "maps")
    git("config", "user.name", "nepal-canvas-bot")
    git("config", "user.email", "bot@users.noreply.github.com")
    for attempt in range(6):
        git("fetch", "-q", "origin", "main")
        git("reset", "-q", "--hard", "origin/main")
        theirs, ours = load(ROOT / "posted.json") or [], load(keep / "posted.json") or []
        merged, seen = [], {}
        for p in theirs + ours:  # ours later in the list -> wins
            k = p.get("fb_video_id") or (("claim-" + p["slot_key"]) if p.get("state") in ("CLAIMED", "RELEASED")
                                         else p.get("slot_key")) or p.get("id")
            if k in seen:
                merged[seen[k]] = p
            else:
                seen[k] = len(merged); merged.append(p)
        # a claim and a release for the same slot share the key "claim-<slot>" -> ours wins above;
        # a CLAIMED marker is kept (it blocks retries) but never duplicated
        merged.sort(key=lambda p: p.get("published_at") or p.get("claimed_at") or "")
        (ROOT / "posted.json").write_text(json.dumps(merged[-500:], ensure_ascii=False, indent=1), encoding="utf-8")
        st = load(ROOT / "stats.json") or {}
        st.update(load(keep / "stats.json") or {})
        if st:
            (ROOT / "stats.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        if (keep / "token.enc").exists():
            shutil.copy(keep / "token.enc", ROOT / "token.enc")
        if (keep / "maps").exists():
            shutil.copytree(keep / "maps", maps, dirs_exist_ok=True)
        for f in ["posted.json", "stats.json", "token.enc", "assets/maps"]:
            if (ROOT / f).exists():
                git("add", "-A", f)
        git("rm", "-q", "--cached", "--ignore-unmatch", "token.json", check=False)
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            print("[state] nothing to save"); return
        git("commit", "-q", "-m", msg)
        if git("push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
            print(f"[state] saved (attempt {attempt + 1})"); return
        time.sleep(5 + 5 * attempt)
    sys.exit("[state] could not push state")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "state")

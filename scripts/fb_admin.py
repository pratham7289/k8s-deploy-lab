"""Small Page maintenance jobs, run from the fb-admin workflow.

  python scripts/fb_admin.py delete <video_id> [<video_id> ...]
  python scripts/fb_admin.py stats
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import requests  # noqa: E402

from pipeline import analytics, config, token  # noqa: E402


def main():
    config.FB_PAGE_TOKEN = token.refresh_if_needed(os.getenv("FB_APP_ID", ""), os.getenv("FB_APP_SECRET", ""))
    tok = config.FB_PAGE_TOKEN
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "delete":
        posted = json.loads(config.POSTED.read_text())
        for vid in args:
            info = requests.get(f"{config.GRAPH}/{vid}", params={"fields": "id,post_id,permalink_url,status",
                                                                   "access_token": tok}, timeout=30)
            print(f"[admin] info {vid}: {info.status_code} {info.text[:300]}")
            targets = [vid]
            try:
                pid = info.json().get("post_id")
                if pid:
                    targets.append(pid if "_" in pid else f"{config.FB_PAGE_ID}_{pid}")
            except Exception:
                pass
            for t in targets:
                r = requests.delete(f"{config.GRAPH}/{t}", params={"access_token": tok}, timeout=30)
                print(f"[admin] delete {t}: {r.status_code} {r.text[:160]}")
                if r.ok:
                    break
            if r.ok:
                for p in posted:
                    if p.get("fb_video_id") == vid:
                        p["state"] = "DELETED"
        config.POSTED.write_text(json.dumps(posted, ensure_ascii=False, indent=1), encoding="utf-8")
    elif cmd == "stats":
        analytics.update(json.loads(config.POSTED.read_text()), tok, days=30)


if __name__ == "__main__":
    main()

"""Small Page maintenance jobs, run from the fb-admin workflow.

  python scripts/fb_admin.py delete <video_id> [<video_id> ...]
  python scripts/fb_admin.py stats
  python scripts/fb_admin.py list      (every Reel on the Page, scheduled ones too, duplicates marked)
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
    elif cmd == "list":
        seen, rows = {}, []
        for edge, extra in (("video_reels", {}), ("videos", {"type": "uploaded"}), ("scheduled_posts", {})):
            url = f"{config.GRAPH}/{config.FB_PAGE_ID}/{edge}"
            params = {"fields": "id,description,created_time,published,scheduled_publish_time,status",
                      "limit": 100, "access_token": tok, **extra}
            if edge == "scheduled_posts":
                params["fields"] = "id,message,created_time,scheduled_publish_time"
            for _ in range(5):
                r = requests.get(url, params=params, timeout=30)
                if not r.ok:
                    print(f"[admin] {edge}: {r.status_code} {r.text[:200]}"); break
                d = r.json()
                for v in d.get("data", []):
                    if v["id"] in seen:
                        continue
                    seen[v["id"]] = 1
                    first = ((v.get("description") or v.get("message") or "").strip().split("\n") or [""])[0][:90]
                    st = v.get("status") or {}
                    rows.append((v.get("created_time", ""), v["id"], edge, v.get("published"),
                                 v.get("scheduled_publish_time"), (st.get("video_status") if isinstance(st, dict) else st), first))
                nxt = d.get("paging", {}).get("next")
                if not nxt:
                    break
                url, params = nxt, {}
        rows.sort()
        firsts = {}
        for row in rows:
            key = row[6]
            dup = f"DUPLICATE of {firsts[key]}" if key and key in firsts else ""
            firsts.setdefault(key, row[1])
            print(f"[list] {row[0]} id={row[1]} via={row[2]} published={row[3]} at={row[4]} status={row[5]} {dup} | {row[6]}")
        print(f"[list] {len(rows)} items")
    elif cmd == "stats":
        analytics.update(json.loads(config.POSTED.read_text()), tok, days=30)


if __name__ == "__main__":
    main()

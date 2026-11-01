"""Publish a Reel to the Page: start -> upload bytes -> finish. Reels Publishing API."""
import time
from pathlib import Path

import requests

from . import config


def publish_reel(video: Path, description: str, state: str = "PUBLISHED", publish_at: int | None = None) -> str:
    """state: PUBLISHED | DRAFT | SCHEDULED (with publish_at = unix time, >= 10 min ahead)."""
    base = f"{config.GRAPH}/{config.FB_PAGE_ID}/video_reels"
    tok = config.FB_PAGE_TOKEN
    r = requests.post(base, data={"upload_phase": "start", "access_token": tok}, timeout=30)
    r.raise_for_status()
    video_id = r.json()["video_id"]
    size = video.stat().st_size
    with open(video, "rb") as f:
        up = requests.post(f"{config.RUPLOAD}/{video_id}",
                           headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)},
                           data=f, timeout=600)
    up.raise_for_status()
    fin = {"upload_phase": "finish", "video_id": video_id, "video_state": state,
           "description": description, "access_token": tok}
    if state == "SCHEDULED":
        fin["scheduled_publish_time"] = str(int(publish_at))
    r = requests.post(base, data=fin, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"finish failed {r.status_code}: {r.text[:300]}")
    for _ in range(30):
        st = requests.get(f"{config.GRAPH}/{video_id}", params={"fields": "status", "access_token": tok},
                          timeout=30).json().get("status", {})
        phase = st.get("video_status")
        if phase in ("ready", "published") or st.get("publishing_phase", {}).get("status") == "complete":
            break
        if st.get("processing_phase", {}).get("status") == "error":
            raise RuntimeError(f"processing error: {st}")
        time.sleep(10)
    return video_id

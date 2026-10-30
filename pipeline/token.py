"""Keep the Page token alive without human help.

A long-lived Page token (60 days) can be exchanged for a fresh one with the app id/secret.
The workflow stores the newest token in token.json (committed back) so the GitHub secret
only needs to be set once.
"""
import json
import time
from pathlib import Path

import requests

from . import config

STORE = config.ROOT / "token.json"


def current() -> str:
    try:
        d = json.loads(STORE.read_text())
        if d.get("token"):
            return d["token"]
    except Exception:
        pass
    return config.FB_PAGE_TOKEN


def refresh_if_needed(app_id: str, app_secret: str) -> str:
    tok = current()
    if not (app_id and app_secret and tok):
        return tok
    dbg = requests.get(f"{config.GRAPH}/debug_token",
                       params={"input_token": tok, "access_token": f"{app_id}|{app_secret}"},
                       timeout=30).json().get("data", {})
    exp = dbg.get("expires_at", 0)
    if exp and exp - time.time() > 20 * 86400:
        return tok  # more than 20 days left
    r = requests.get(f"{config.GRAPH}/oauth/access_token",
                     params={"grant_type": "fb_exchange_token", "client_id": app_id,
                             "client_secret": app_secret, "fb_exchange_token": tok}, timeout=30).json()
    new = r.get("access_token")
    if new:
        STORE.write_text(json.dumps({"token": new, "refreshed": int(time.time())}))
        print("[token] refreshed")
        return new
    print(f"[token] refresh failed: {r}")
    return tok

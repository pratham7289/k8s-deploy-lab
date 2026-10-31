"""Keep the Page token alive without human help.

A long-lived Page token (60 days) can be exchanged for a fresh one with the app id/secret.
The newest token is kept ENCRYPTED in token.enc (AES-256, key = the app secret, which only
lives in GitHub Secrets) and committed back, so the GitHub secret only needs to be set once
and the repo never holds a readable token.
"""
import json
import os
import subprocess
import time
from pathlib import Path

import requests

from . import config

STORE = config.ROOT / "token.enc"
LEGACY = config.ROOT / "token.json"


def _ssl(args: list[str], data: bytes) -> bytes:
    env = dict(os.environ, TOKEN_KEY=os.getenv("FB_APP_SECRET", ""))
    return subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-a", "-A",
                           "-pass", "env:TOKEN_KEY"] + args, input=data, capture_output=True, check=True, env=env).stdout


def save(tok: str):
    if not os.getenv("FB_APP_SECRET"):
        return
    STORE.write_bytes(_ssl([], json.dumps({"token": tok, "refreshed": int(time.time())}).encode()))
    LEGACY.unlink(missing_ok=True)


def current() -> str:
    try:
        d = json.loads(_ssl(["-d"], STORE.read_bytes()))
        if d.get("token"):
            return d["token"]
    except Exception:
        pass
    try:  # one-time migration from the old plain-text file
        tok = json.loads(LEGACY.read_text()).get("token")
        if tok:
            save(tok)
            return tok
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
        save(new)
        print("[token] refreshed")
        return new
    print(f"[token] refresh failed: {r}")
    return tok

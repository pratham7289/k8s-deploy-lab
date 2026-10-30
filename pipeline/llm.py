"""Gemini via REST – no SDK. Returns parsed JSON; falls back to Flash-Lite on 429."""
import json
import re
import time

import requests

from . import config

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def ask_json(system: str, user: str, temperature=0.4) -> dict:
    last = None
    for model in config.GEMINI_MODELS:
        for attempt in range(2):
            try:
                r = requests.post(
                    URL.format(model=model, key=config.GEMINI_API_KEY),
                    json={
                        "system_instruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": [{"text": user}]}],
                        "generationConfig": {"temperature": temperature,
                                             "responseMimeType": "application/json"},
                    },
                    timeout=90,
                )
                if r.status_code == 429:
                    last = "429"; time.sleep(60); continue
                if r.status_code in (400, 403, 404):
                    last = f"{r.status_code} {r.text[:120]}"; print(f"[llm] {model}: {last}"); break  # next model
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
                return json.loads(text)
            except Exception as e:
                last = str(e)
                time.sleep(5)
    raise RuntimeError(f"Gemini failed: {last}")

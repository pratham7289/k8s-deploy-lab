"""Nepali voice-over.
Primary: Gemini TTS (free tier, natural news-anchor delivery, Nepali supported).
Fallback: edge-tts Microsoft neural voices (no key).
"""
import asyncio
import base64
import subprocess
import time
from datetime import date
from pathlib import Path

import requests

from . import config

GEMINI_TTS_MODELS = ["gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts"]
# Two prebuilt voices that read Devanagari well; alternate daily like a two-anchor desk.
GEMINI_VOICES = ["Aoede", "Charon"]
STYLE = ("Read this as a calm, confident Nepali television news anchor. Natural Nepali pronunciation, "
         "clear diction, measured pace, short pauses at sentence ends, slight emphasis on key facts, "
         "no dramatic or robotic tone.")


def voice_for_today() -> str:
    return GEMINI_VOICES[date.today().toordinal() % len(GEMINI_VOICES)]


def _gemini(text: str, path: Path) -> bool:
    voice = voice_for_today()
    body = {
        "contents": [{"parts": [{"text": f"{STYLE}\n\n{text}"}]}],
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
    }
    for model in GEMINI_TTS_MODELS:
        for attempt in range(2):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}",
                    json=body, timeout=180)
                if r.status_code == 429:
                    time.sleep(45); continue
                if r.status_code >= 400:
                    print(f"[tts] {model}: {r.status_code} {r.text[:100]}"); break
                part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                raw = base64.b64decode(part["data"])
                mime = part.get("mimeType", "audio/wav")
                wav = path.with_suffix(".wav")
                if "wav" in mime:
                    wav.write_bytes(raw)
                else:  # raw L16 pcm, 24 kHz mono
                    rate = 24000
                    for kv in mime.split(";"):
                        if kv.strip().startswith("rate="):
                            rate = int(kv.split("=")[1])
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1",
                                    "-i", "pipe:0", str(wav)], input=raw, check=True)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-af", "loudnorm=I=-16:TP=-1.5",
                                "-ar", "48000", "-b:a", "160k", str(path)], check=True)
                print(f"[tts] gemini {model} voice={voice}")
                return True
            except Exception as e:
                print(f"[tts] gemini error: {e}"); time.sleep(5)
    return False


async def _edge(text, voice, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate="-6%", pitch="-2Hz").save(str(path))


def synth(text: str, path: Path) -> float:
    if config.GEMINI_API_KEY and _gemini(text, path):
        return duration(path)
    voice = config.VOICES[date.today().toordinal() % len(config.VOICES)]
    asyncio.run(_edge(text, voice, path))
    print(f"[tts] edge-tts fallback voice={voice}")
    return duration(path)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)

"""Nepali voice-over.
Primary: Gemini TTS (free tier, natural news-anchor delivery, Nepali supported).
The script is read in short chunks with the same voice and style; each chunk's speaking rate is
measured and levelled to the median so the delivery stays even from first sentence to last.
Fallback: edge-tts Microsoft neural voices (no key).
"""
import asyncio
import base64
import re
import statistics
import subprocess
import time
from datetime import date
from pathlib import Path

import requests

from . import config

GEMINI_TTS_MODELS = ["gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts"]
# Two prebuilt voices that read Devanagari well; alternate daily like a two-anchor desk.
GEMINI_VOICES = ["Aoede", "Charon"]
STYLE = ("Read this as a calm, confident Nepali television news anchor speaking to viewers. "
         "Natural Nepali pronunciation, clear diction, a steady medium pace that stays exactly the same "
         "from the first word to the last, short pauses at sentence ends, slight emphasis on key facts, "
         "no dramatic or robotic tone, do not slow down or trail off at the end.")
CHUNK_CHARS = 260          # ~2-3 Nepali sentences per TTS call
RATE_TOLERANCE = 0.10      # chunks faster/slower than median by more than this get time-stretched
GAP = 0.32                 # seconds of silence between chunks


def voice_for_today() -> str:
    return GEMINI_VOICES[date.today().toordinal() % len(GEMINI_VOICES)]


def chunks(text: str) -> list[str]:
    sents = [s.strip() for s in re.split(r"(?<=[।!?])\s+", text.strip()) if s.strip()]
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) > CHUNK_CHARS:
            out.append(cur); cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return out


def _gemini_call(text: str, voice: str, wav: Path) -> str | None:
    """One TTS request -> wav file. Returns the model used, or None."""
    body = {
        "contents": [{"parts": [{"text": f"{STYLE}\n\n{text}"}]}],
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
    }
    for model in GEMINI_TTS_MODELS:
        for attempt in range(3):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}",
                    json=body, timeout=180)
                if r.status_code == 429:
                    time.sleep(40 * (attempt + 1)); continue
                if r.status_code >= 400:
                    print(f"[tts] {model}: {r.status_code} {r.text[:100]}"); break
                part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                raw = base64.b64decode(part["data"])
                mime = part.get("mimeType", "audio/wav")
                if "wav" in mime:
                    wav.write_bytes(raw)
                else:  # raw L16 pcm
                    rate = 24000
                    for kv in mime.split(";"):
                        if kv.strip().startswith("rate="):
                            rate = int(kv.split("=")[1])
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1",
                                    "-i", "pipe:0", str(wav)], input=raw, check=True)
                return model
            except Exception as e:
                print(f"[tts] gemini error: {e}"); time.sleep(5)
    return None


def _trim(wav: Path) -> Path:
    """Strip leading/trailing silence so gaps are controlled by us, not the model."""
    out = wav.with_name(wav.stem + "_t.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-af",
                    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,"
                    "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.25,areverse",
                    str(out)], check=True)
    return out


def _gemini(text: str, path: Path) -> bool:
    voice = voice_for_today()
    parts = chunks(text)
    wavs, model = [], None
    for i, ch in enumerate(parts):
        wav = path.with_name(f"tts{i}.wav")
        model = _gemini_call(ch, voice, wav)
        if not model:
            return False
        wavs.append((ch, _trim(wav)))
    # level the speaking rate: chars per second per chunk vs median
    rates = []
    for ch, wav in wavs:
        d = duration(wav)
        rates.append(len(ch) / max(d, 0.1))
    med = statistics.median(rates)
    levelled = []
    for (ch, wav), rate in zip(wavs, rates):
        factor = med / rate  # >1 means this chunk is slow -> speed up
        if abs(factor - 1) > RATE_TOLERANCE:
            factor = max(0.85, min(1.2, factor))
            out = wav.with_name(wav.stem + "_l.wav")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-af", f"atempo={factor:.3f}",
                            str(out)], check=True)
            print(f"[tts] levelled chunk {len(levelled)} x{factor:.2f}")
            wav = out
        levelled.append(wav)
    # concat with fixed gaps, then loudness-normalise
    lst = path.with_name("tts_list.txt")
    gap = path.with_name("gap.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", f"{GAP}", str(gap)], check=True)
    lines = []
    for w in levelled:
        lines.append(f"file '{w.resolve()}'\n")
        lines.append(f"file '{gap.resolve()}'\n")
    lst.write_text("".join(lines[:-1]))
    joined = path.with_name("tts_all.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-ar", "24000", "-ac", "1", str(joined)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-af", "loudnorm=I=-16:TP=-1.5:LRA=9",
                    "-ar", "48000", "-b:a", "160k", str(path)], check=True)
    print(f"[tts] gemini {model} voice={voice} chunks={len(parts)} rate_spread={min(rates)/med:.2f}-{max(rates)/med:.2f}")
    return True


async def _edge(text, voice, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate="-6%", pitch="-2Hz").save(str(path))


def synth(text: str, path: Path) -> float:
    if config.GEMINI_API_KEY:
        try:
            if _gemini(text, path):
                return duration(path)
        except Exception as e:
            print(f"[tts] gemini pipeline failed: {e}")
    voice = config.VOICES[date.today().toordinal() % len(config.VOICES)]
    asyncio.run(_edge(text, voice, path))
    print(f"[tts] edge-tts fallback voice={voice}")
    return duration(path)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)

"""Nepali voice-over.
Primary: Gemini TTS, one locked voice (Aoede), the whole script in ONE request so tone and pace
stay the same throughout (and it costs one TTS call per Reel).
If the single take drifts in pace (first half vs second half differ by more than 15%), it is redone
in short chunks whose speaking rate is levelled to the median.
Fallback: edge-tts Microsoft neural voice (no key).
sentence_times() finds when each sentence is spoken (pauses in the audio), for subtitles and cuts.
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
VOICE = "Aoede"  # locked: one anchor voice for the Page
STYLE = ("Read this as a calm, confident Nepali television news anchor speaking to viewers. "
         "Natural Nepali pronunciation, clear diction, a steady medium pace that stays exactly the same "
         "from the first word to the last, short pauses at sentence ends, slight emphasis on key facts, "
         "no dramatic or robotic tone, do not slow down or trail off at the end.")
CHUNK_CHARS = 260          # ~2-3 Nepali sentences per TTS call
RATE_TOLERANCE = 0.10      # chunks faster/slower than median by more than this get time-stretched
GAP = 0.32                 # seconds of silence between chunks


DRIFT_LIMIT = 0.35  # sentence-level estimate is noisy; only catch a clear slow-down
RATE_OK = (6.5, 11.0)  # weighted chars per second; outside this a take is rushed, dragged or cut short
TARGET_CPS = 8.2       # every Reel is levelled to this pace so all videos sound the same


def _weight(s: str) -> int:
    """Spoken length estimate: a digit is read as a whole word, so count it ~4 chars."""
    return len(s) + 3 * len(re.findall(r"[0-9०-९]", s))


def voice_for_today() -> str:
    return VOICE


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
        "contents": [{"parts": [{"text": text}]}],  # text only: the model read any style instruction aloud
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


def _gemini_chunked(text: str, path: Path) -> bool:
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
        rates.append(_weight(ch) / max(d, 0.1))
    med = TARGET_CPS
    levelled = []
    for (ch, wav), rate in zip(wavs, rates):
        factor = med / rate  # >1 means this chunk is slow -> speed up
        if abs(factor - 1) > RATE_TOLERANCE:
            factor = max(0.8, min(1.25, factor))
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
    _finish(joined, path)
    print(f"[tts] gemini {model} voice={voice} chunks={len(parts)} rate_spread={min(rates)/med:.2f}-{max(rates)/med:.2f}")
    return True


def _finish(wav: Path, path: Path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-af", "loudnorm=I=-16:TP=-1.5:LRA=9",
                    "-ar", "48000", "-b:a", "160k", str(path)], check=True)


def _gemini_single(text: str, path: Path, sentences: list[str]) -> bool:
    wav = path.with_name("tts_one.wav")
    model = _gemini_call(text, VOICE, wav)
    if not model:
        return False
    wav = _trim(wav)
    rate = sum(_weight(s) for s in sentences) / max(duration(wav), 0.1)
    print(f"[tts] single take rate={rate:.1f} chars/s")
    if not RATE_OK[0] <= rate <= RATE_OK[1]:
        return False
    times = sentence_times(wav, sentences)
    if len(times) >= 4:
        half = len(times) // 2
        def cps(ts, ss):
            return sum(_weight(x) for x in ss) / max(sum(b - a for a, b in ts), 0.1)
        r1, r2 = cps(times[:half], sentences[:half]), cps(times[half:], sentences[half:])
        drift = abs(r2 / r1 - 1)
        print(f"[tts] single take {model} voice={VOICE} drift={drift:.2f}")
        if drift > DRIFT_LIMIT:
            return False
    _finish(wav, path)
    return True


def _gemini(text: str, path: Path, sentences: list[str]) -> bool:
    for _ in range(2):
        if _gemini_single(text, path, sentences):
            return True
    print("[tts] single take rejected -> chunked + levelled")
    return _gemini_chunked(text, path)


async def _edge(text, voice, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate="-6%", pitch="-2Hz").save(str(path))


def synth(text: str, path: Path, sentences: list[str] | None = None) -> float:
    sentences = sentences or [x for x in re.split(r"(?<=[।!?])\s+", text.strip()) if x.strip()]
    if config.GEMINI_API_KEY:
        try:
            if _gemini(text, path, sentences):
                return duration(path)
        except Exception as e:
            print(f"[tts] gemini pipeline failed: {e}")
    voice = "ne-NP-HemkalaNeural"  # female, to match Aoede
    asyncio.run(_edge(text, voice, path))
    print(f"[tts] edge-tts fallback voice={voice}")
    return duration(path)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def _silences(path: Path) -> list[tuple[float, float]]:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "silencedetect=noise=-35dB:d=0.15",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends))


def sentence_times(path: Path, sentences: list[str]) -> list[tuple[float, float]]:
    """(start, end) of each sentence in the audio file: expected boundaries from character share,
    snapped to the nearest real pause, re-estimating the rest after each snap."""
    total = duration(path)
    n = len(sentences)
    if n <= 1:
        return [(0.0, total)]
    mids = [(a + b) / 2 for a, b in _silences(path) if 0.2 < (a + b) / 2 < total - 0.2]
    lens = [max(_weight(s), 1) for s in sentences]
    bounds, t_prev = [], 0.0
    for k in range(n - 1):
        rest = sum(lens[k:])
        expect = t_prev + (total - t_prev) * lens[k] / rest
        cands = [m for m in mids if m > t_prev + 0.4 and abs(m - expect) < max(1.2, 0.25 * (expect - t_prev))]
        b = min(cands, key=lambda m: abs(m - expect)) if cands else expect
        bounds.append(b); t_prev = b
    edges = [0.0] + bounds + [total]
    return [(edges[i], edges[i + 1]) for i in range(n)]

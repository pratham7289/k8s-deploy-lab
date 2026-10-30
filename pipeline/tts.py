"""Nepali voice-over with edge-tts (no key). Alternates voices by day."""
import asyncio
import subprocess
from datetime import date
from pathlib import Path

from . import config


def voice_for_today() -> str:
    return config.VOICES[date.today().toordinal() % len(config.VOICES)]


async def _synth(text, voice, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate="-5%").save(str(path))


def synth(text: str, path: Path, voice: str | None = None) -> float:
    voice = voice or voice_for_today()
    asyncio.run(_synth(text, voice, path))
    return duration(path)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)

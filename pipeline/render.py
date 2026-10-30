"""Assemble the Reel with ffmpeg: intro -> headline card -> b-roll under voice (cards cut in) -> credit."""
import random
import subprocess
from pathlib import Path

from . import config

W, H, FPS = 1080, 1920, 30


def _seg_still(img: Path, dur: float, out: Path, zoom=True):
    # Cheap Ken Burns: a slow crop that drifts over a slightly oversized frame (no zoompan — it is 50x slower).
    vf = (f"scale={int(W*1.12)}:{int(H*1.12)},crop={W}:{H}:x='(in_w-{W})*t/{dur:.2f}':y='(in_h-{H})/2',fps={FPS}"
          if zoom else f"scale={W}:{H},fps={FPS}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.2f}",
                    "-i", str(img), "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                    "-pix_fmt", "yuv420p", "-an", str(out)], check=True)
    return out


def _seg_video(src: Path, dur: float, out: Path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-t", f"{dur:.2f}",
                    "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p", "-an",
                    str(out)], check=True)
    return out


def build(voice: Path, voice_len: float, cards: list[Path], broll: list, workdir: Path, out: Path) -> Path:
    """cards[0] = headline, cards[1:-1] = inserts, cards[-1] = credit."""
    segs = []
    intro = config.ASSETS / "intro" / "intro.mp4"
    plan = []  # (kind, path, dur)
    plan.append(("still", cards[0], 3.0))
    inserts = cards[1:-1]
    body = max(voice_len - 3.0, 8.0)
    # alternate b-roll and insert cards across the voice
    slots = len(broll) + len(inserts)
    per = body / max(slots, 1)
    b_i = c_i = 0
    for k in range(slots):
        if k % 2 == 0 and b_i < len(broll) or c_i >= len(inserts):
            t, p, d = broll[b_i % max(len(broll), 1)] if broll else ("image", cards[0], per)
            plan.append(("video" if t == "video" else "still", p, min(per, max(d, 2))))
            b_i += 1
        else:
            plan.append(("still", inserts[c_i], per)); c_i += 1
    plan.append(("still", cards[-1], 3.0))
    for i, (kind, p, d) in enumerate(plan):
        seg = workdir / f"seg{i:02d}.mp4"
        (_seg_video if kind == "video" else _seg_still)(p, d, seg)
        segs.append(seg)
    lst = workdir / "list.txt"
    lst.write_text("".join(f"file '{s.resolve()}'\n" for s in ([intro] if intro.exists() else []) + segs))
    silent = workdir / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(silent)], check=True)
    music = sorted((config.ASSETS / "music").glob("*.mp3"))
    offset = 2.0 if intro.exists() else 0.0
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-itsoffset", f"{offset}", "-i", str(voice)]
    if music:
        m = random.choice(music)
        cmd += ["-stream_loop", "-1", "-i", str(m), "-filter_complex",
                "[2:a]volume=0.18,afade=t=out:st=9999:d=1[m];[1:a][m]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-b:a", "128k", "-shortest",
            "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    return out

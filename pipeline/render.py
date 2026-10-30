"""Assemble the Reel: headline card -> one visual per scene (timed to its sentences) -> credit card.
Each scene gets its caption burned in; a scene with no fitting visual becomes a text card."""
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, visuals

W, H, FPS = 1080, 1920, 30


def _caption_overlay(text: str, path: Path):
    """Transparent PNG with a dark band + Nepali caption, for overlaying on footage."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = visuals._font(56)
    lines = visuals._wrap(d, text, f, W - 160)[:3]
    band_h = 60 + 78 * len(lines)
    y0 = H - 420 - band_h
    d.rounded_rectangle([60, y0, W - 60, y0 + band_h], radius=24, fill=(31, 42, 68, 215))
    y = y0 + 30
    for ln in lines:
        d.text((100, y), ln, font=f, fill=(246, 241, 231, 255)); y += 78
    d.text((100, H - 160), "NEPAL CANVAS", font=visuals._font(36, latin=True), fill=(220, 20, 60, 255))
    im.save(path)
    return path


def _seg_still(img: Path, dur: float, out: Path, overlay: Path | None):
    vf = f"scale={int(W*1.12)}:{int(H*1.12)}:force_original_aspect_ratio=increase,crop={int(W*1.12)}:{int(H*1.12)}," \
         f"crop={W}:{H}:x='(in_w-{W})*t/{max(dur,0.1):.2f}':y='(in_h-{H})/2',fps={FPS}"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.2f}", "-i", str(img)]
    if overlay:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", str(overlay), "-filter_complex",
                f"[0:v]{vf}[b];[b][1:v]overlay=0:0:format=yuv420"]
    else:
        cmd += ["-vf", vf]
    cmd += ["-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p", "-an", str(out)]
    subprocess.run(cmd, check=True)
    return out


def _seg_video(src: Path, dur: float, out: Path, overlay: Path | None):
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-threads", "2", "-stream_loop", "-1", "-i", str(src), "-t", f"{dur:.2f}"]
    if overlay:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", str(overlay), "-filter_complex",
                f"[0:v]{vf}[b];[b][1:v]overlay=0:0:format=yuv420"]
    else:
        cmd += ["-vf", vf]
    cmd += ["-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p", "-an", str(out)]
    subprocess.run(cmd, check=True)
    return out


def build(voice: Path, voice_len: float, headline_card: Path, credit_card: Path,
          scenes: list[dict], workdir: Path, out: Path, template: str) -> Path:
    """scenes: [{text, card, visual: (kind, path, dur, credit) | None}] in order."""
    INTRO, OUTRO = 2.6, 3.0
    body = max(voice_len - 0.3, 5.0)
    words = [max(len(s["text"].split()), 1) for s in scenes] or [1]
    total_w = sum(words)
    plan = [("still", headline_card, INTRO, None)]
    credits = []
    for i, s in enumerate(scenes):
        dur = max(body * words[i] / total_w, 2.5)
        ov = _caption_overlay(s.get("card") or "", workdir / f"ov{i}.png") if s.get("card") else None
        v = s.get("visual")
        if v:
            kind, p, _, credit = v
            plan.append(("video" if kind == "video" else "still", p, dur, ov))
            if credit and credit != "Pixabay":
                credits.append(credit)
        else:
            plan.append(("still", visuals.card(s.get("card") or s["text"][:70], workdir / f"fc{i}.png", template), dur, None))
    plan.append(("still", credit_card, OUTRO, None))
    segs = []
    for i, (kind, p, d, ov) in enumerate(plan):
        seg = workdir / f"seg{i:02d}.mp4"
        (_seg_video if kind == "video" else _seg_still)(p, d, seg, ov)
        segs.append(seg)
    lst = workdir / "list.txt"
    lst.write_text("".join(f"file '{s.resolve()}'\n" for s in segs))
    silent = workdir / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(silent)], check=True)
    music = sorted((config.ASSETS / "music").glob("*.mp3"))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-itsoffset", f"{INTRO - 0.4:.2f}", "-i", str(voice)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(random.choice(music)), "-filter_complex",
                "[2:a]volume=0.14[m];[1:a][m]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-b:a", "128k", "-shortest",
            "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    (workdir / "credits.txt").write_text("\n".join(dict.fromkeys(credits)), encoding="utf-8")
    return out

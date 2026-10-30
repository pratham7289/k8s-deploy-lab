"""Assemble the Reel: headline card -> one visual per scene (timed to its sentences) -> credit card.
Each scene gets its caption burned in; a scene with no fitting visual becomes a text card.
Scenes are joined with short crossfades; a soft news bed sits under the voice."""
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, visuals

W, H, FPS = 1080, 1920, 30
XF = 0.45            # crossfade length (s)
CRF, PRESET = "19", "medium"
MUSIC_DB = -24       # bed level under the voice


def _caption_overlay(text: str, path: Path):
    """Transparent PNG with a dark band + Nepali caption, for overlaying on footage."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = visuals._font(58)
    lines = visuals._wrap(d, text, f, W - 180)[:3]
    band_h = 56 + 82 * len(lines)
    y0 = H - 470 - band_h
    d.rounded_rectangle([56, y0, W - 56, y0 + band_h], radius=26, fill=(14, 20, 36, 205))
    d.rectangle([56, y0 + 18, 66, y0 + band_h - 18], fill=(220, 20, 60, 255))
    y = y0 + 26
    for ln in lines:
        d.text((96, y), ln, font=f, fill=(246, 241, 231, 255)); y += 82
    im.save(path)
    return path


def _enc():
    return ["-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p", "-an"]


def _seg_still(img: Path, dur: float, out: Path, overlay: Path | None):
    # slow Ken Burns: 1.0 -> 1.07 scale with a gentle drift, on a 2x oversampled frame for smoothness
    ov = f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2}"
    zoom = (f"zoompan=z='min(1+0.07*on/({dur:.2f}*{FPS}),1.07)':d=1:"
            f"x='iw/2-(iw/zoom/2)+({dur:.2f}*{FPS}-on)*0.3':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}")
    vf = f"{ov},{zoom},format=yuv420p"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.2f}", "-i", str(img)]
    if overlay:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", str(overlay), "-filter_complex",
                f"[0:v]{vf}[b];[b][1:v]overlay=0:0:format=yuv420"]
    else:
        cmd += ["-vf", vf]
    cmd += ["-t", f"{dur:.2f}"] + _enc() + [str(out)]
    subprocess.run(cmd, check=True)
    return out


def _seg_video(src: Path, dur: float, out: Path, overlay: Path | None):
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=yuv420p"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-threads", "2", "-stream_loop", "-1", "-i", str(src), "-t", f"{dur:.2f}"]
    if overlay:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", str(overlay), "-filter_complex",
                f"[0:v]{vf}[b];[b][1:v]overlay=0:0:format=yuv420"]
    else:
        cmd += ["-vf", vf]
    cmd += ["-t", f"{dur:.2f}"] + _enc() + [str(out)]
    subprocess.run(cmd, check=True)
    return out


def _xfade_concat(segs: list[Path], durs: list[float], out: Path):
    """Join segments with crossfades (each fade eats XF seconds, so segment durations are padded by the caller)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for s in segs:
        cmd += ["-i", str(s)]
    if len(segs) == 1:
        subprocess.run(cmd + ["-c", "copy", str(out)], check=True); return out
    fc, prev, offset = [], "[0:v]", 0.0
    for i in range(1, len(segs)):
        offset += durs[i - 1] - XF
        lbl = f"[v{i}]" if i < len(segs) - 1 else "[v]"
        fc.append(f"{prev}[{i}:v]xfade=transition=fade:duration={XF}:offset={offset:.3f}{lbl}")
        prev = lbl
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]"] + _enc() + [str(out)]
    subprocess.run(cmd, check=True)
    return out


def _bed(workdir: Path, length: float) -> Path | None:
    """Background bed: a real track from assets/music if present, else a soft synthesised newsroom pad."""
    music = sorted((config.ASSETS / "music").glob("*.mp3"))
    if music:
        return random.choice(music)
    out = workdir / "bed.wav"
    # two detuned low pads + a very soft pulse, low-passed: unobtrusive, 'bulletin' feel
    src = ("sine=f=110:r=48000[a];sine=f=164.8:r=48000[b];sine=f=220.5:r=48000[c];"
           "[a][b][c]amix=inputs=3:normalize=0,tremolo=f=1.9:d=0.25,lowpass=f=600,volume=0.25[m]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src.replace("[m]", ""),
                    "-t", f"{length + 2:.1f}", str(out)], check=True)
    return out


def build(voice: Path, voice_len: float, headline_card: Path, credit_card: Path,
          scenes: list[dict], workdir: Path, out: Path, template: str) -> Path:
    """scenes: [{text, card, visual: (kind, path, dur, credit) | None}] in order."""
    INTRO, OUTRO = 2.6, 3.2
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
    # pad every segment except the last by XF so crossfades do not shorten the timeline
    segs, durs = [], []
    for i, (kind, p, d, ov) in enumerate(plan):
        d_pad = d + (XF if i < len(plan) - 1 else 0)
        seg = workdir / f"seg{i:02d}.mp4"
        (_seg_video if kind == "video" else _seg_still)(p, d_pad, seg, ov)
        segs.append(seg); durs.append(d_pad)
    silent = workdir / "silent.mp4"
    _xfade_concat(segs, durs, silent)
    total = sum(d for _, _, d, _ in plan)
    bed = _bed(workdir, total)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-itsoffset", f"{INTRO - 0.4:.2f}", "-i", str(voice)]
    if bed:
        cmd += ["-stream_loop", "-1", "-i", str(bed), "-filter_complex",
                f"[2:a]volume={MUSIC_DB}dB,afade=t=in:d=1.5,afade=t=out:st={total-2.5:.2f}:d=2.5[m];"
                f"[1:a]apad[v];[v][m]amix=inputs=2:duration=first:normalize=0:dropout_transition=2[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-b:a", "160k", "-shortest",
            "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    (workdir / "credits.txt").write_text("\n".join(dict.fromkeys(credits)), encoding="utf-8")
    return out

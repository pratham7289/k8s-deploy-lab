"""Assemble the Reel.

Timeline: the voice starts at 0.25 s on the first scene (no title card), the headline sits over the
first shot for ~3 s, every scene is cut into shots of <= ~4.5 s that follow the sentence timing,
subtitles show each sentence (phrase by phrase) in sync with the voice, and a short takeaway card
closes. Stills get a slow pan made with crop on a pre-scaled frame (much faster than zoompan).
"""
import math
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, visuals

W, H, FPS = 1080, 1920, 30
CRF, PRESET = "21", "veryfast"
MUSIC_DB = -25        # bed level under the voice
LEAD = 0.25           # voice starts this long after frame 0
TAIL = 0.35           # breath after the last word before the end card
OUTRO = 2.6           # takeaway / source card
HOOK_SECS = 3.2       # headline over the first shot
MAX_SHOT = 4.5


def _run(cmd):
    subprocess.run(cmd, check=True)


def _enc():
    return ["-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-g", str(FPS * 2), "-an"]


# ---------- overlays (Pillow + raqm, so Devanagari shapes correctly) ----------

def _sub_png(text: str, path: Path):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text:
        d = ImageDraw.Draw(im)
        f = visuals._font(62)
        lines = visuals._wrap(d, text, f, W - 300)[:2]
        widths = [d.textlength(ln, font=f) for ln in lines]
        bw, bh = int(max(widths)) + 84, 40 + 84 * len(lines)
        cx = W // 2 - 20                      # keep clear of the Reels buttons on the right
        x0, y0 = cx - bw // 2, H - 640 - bh   # above the caption/username area
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=24, fill=(10, 14, 26, 200))
        d.rectangle([x0, y0 + 16, x0 + 8, y0 + bh - 16], fill=(220, 20, 60, 255))
        y = y0 + 18
        for ln, lw in zip(lines, widths):
            d.text((cx - lw / 2, y), ln, font=f, fill=(255, 255, 255, 255)); y += 84
    im.save(path)
    return path


def _hook_png(headline: str, kicker: str, path: Path):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grad = Image.new("L", (1, 900))
    for y in range(900):
        grad.putpixel((0, y), int(215 * (1 - y / 900) ** 1.4))
    shade = Image.new("RGBA", (W, 900), (8, 12, 24, 255))
    shade.putalpha(grad.resize((W, 900)))
    im.alpha_composite(shade)
    d = ImageDraw.Draw(im)
    y = 170
    if kicker:
        fk = visuals._font(44)
        kw = d.textlength(kicker, font=fk)
        d.rounded_rectangle([72, y, 72 + kw + 40, y + 70], radius=12, fill=(220, 20, 60, 255))
        d.text((92, y + 6), kicker, font=fk, fill=(255, 255, 255, 255))
        y += 100
    f = visuals._font(80)
    for ln in visuals._wrap(d, headline, f, W - 150)[:3]:
        d.text((76, y + 3), ln, font=f, fill=(0, 0, 0, 160))
        d.text((72, y), ln, font=f, fill=(255, 255, 255, 255)); y += 104
    im.save(path)
    return path


def _phrases(text: str, max_lines: int = 2) -> list[str]:
    """Split one on-screen sentence into chunks that fit in two subtitle lines."""
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    f = visuals._font(62)
    out, cur = [], []
    for w in text.split():
        trial = " ".join(cur + [w])
        if cur and len(visuals._wrap(probe, trial, f, W - 300)) > max_lines:
            out.append(" ".join(cur)); cur = [w]
        else:
            cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return out


def _subtitle_track(cues: list[tuple[float, float, str]], total: float, workdir: Path) -> Path:
    """Concat-demuxer list of full-frame transparent PNGs, one per phrase, gaps blank."""
    blank = _sub_png("", workdir / "sub_blank.png")
    items, t, n = [], 0.0, 0
    for start, end, text in cues:
        phs = _phrases(text)
        chars = sum(len(p) for p in phs) or 1
        a = start
        for ph in phs:
            b = a + (end - start) * len(ph) / chars
            if a > t + 0.02:
                items.append((blank, a - t))
            png = _sub_png(ph, workdir / f"sub{n:03d}.png"); n += 1
            items.append((png, b - max(a, t)))
            t = b; a = b
    if total > t:
        items.append((blank, total - t))
    lst = workdir / "subs.txt"
    lines = []
    for p, d in items:
        lines += [f"file '{p.resolve()}'", f"duration {max(d, 0.04):.3f}"]
    lines.append(f"file '{blank.resolve()}'")
    lst.write_text("\n".join(lines) + "\n")
    return lst


# ---------- shots ----------

MOVES = ["up", "down", "left", "right"]


def _prep_still(img: Path, out: Path, scale: float) -> Path:
    """Cover-fit the image once at (scale x frame) size so ffmpeg only has to crop per frame."""
    im = Image.open(img).convert("RGB")
    tw, th = int(W * scale) // 2 * 2, int(H * scale) // 2 * 2
    r = max(tw / im.width, th / im.height)
    im = im.resize((max(tw, int(im.width * r + 0.5)), max(th, int(im.height * r + 0.5))), Image.LANCZOS)
    left, top = (im.width - tw) // 2, (im.height - th) // 2
    im.crop((left, top, left + tw, top + th)).save(out, quality=92)
    return out


def _shot_still(img: Path, dur: float, out: Path, move: str, scale: float = 1.14):
    pre = _prep_still(img, out.with_suffix(".jpg"), scale)
    cx, cy = f"(in_w-{W})/2", f"(in_h-{H})/2"
    p = f"(t/{dur:.3f})"
    x, y = {"up": (cx, f"(in_h-{H})*(1-{p})"), "down": (cx, f"(in_h-{H})*{p}"),
            "left": (f"(in_w-{W})*(1-{p})", cy), "right": (f"(in_w-{W})*{p}", cy),
            "none": (cx, cy)}[move]
    _run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}",
          "-i", str(pre), "-vf", f"crop={W}:{H}:x='{x}':y='{y}',format=yuv420p", "-t", f"{dur:.3f}"]
         + _enc() + [str(out)])
    return out


def _shot_video(src: Path, dur: float, out: Path):
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=yuv420p"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src), "-t", f"{dur:.3f}",
          "-vf", vf] + _enc() + [str(out)])
    return out


def _bed(workdir: Path, length: float) -> Path:
    """Background bed: a real track from assets/music if present, else a soft synthesised pad."""
    music = sorted((config.ASSETS / "music").glob("*.mp3"))
    if music:
        return random.choice(music)
    out = workdir / "bed.wav"
    src = ("sine=f=110:r=48000[a];sine=f=164.8:r=48000[b];sine=f=220.5:r=48000[c];"
           "[a][b][c]amix=inputs=3:normalize=0,tremolo=f=1.9:d=0.25,lowpass=f=600,volume=0.25")
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, "-t", f"{length + 2:.1f}", str(out)])
    return out


def _scene_spans(scenes, sent_times, sentences, body_end):
    """Map each scene (consecutive sentences, verbatim) to a time span via character position."""
    total_chars = sum(len(s) for s in sentences) or 1
    scene_chars = [max(len(sc.get("text", "")), 1) for sc in scenes]
    k = total_chars / sum(scene_chars)
    # char offset -> time, piecewise linear inside each sentence
    edges, c = [], 0
    for s, (a, b) in zip(sentences, sent_times):
        edges.append((c, c + len(s), a, b)); c += len(s)

    def t_at(ch):
        for c0, c1, a, b in edges:
            if ch <= c1:
                return a + (b - a) * (ch - c0) / max(c1 - c0, 1)
        return edges[-1][3]
    spans, cum = [], 0.0
    for i, n in enumerate(scene_chars):
        a = 0.0 if i == 0 else spans[-1][1]
        cum += n * k
        b = body_end if i == len(scene_chars) - 1 else LEAD + t_at(cum)
        spans.append((a, max(b, a + 1.0)))
    return spans


def build(voice: Path, voice_len: float, sentences: list[str], shows: list[str],
          sent_times: list[tuple[float, float]], headline: str, kicker: str, end_card: Path,
          scenes: list[dict], workdir: Path, out: Path, template: str) -> Path:
    """scenes: [{text, card, visuals: [(kind, path, dur, credit)]}] in order."""
    body_end = LEAD + voice_len + TAIL
    spans = _scene_spans(scenes, sent_times, sentences, body_end)
    shots, credits, mi = [], [], random.randrange(4)
    for i, (sc, (a, b)) in enumerate(zip(scenes, spans)):
        dur = b - a
        vis = sc.get("visuals") or []
        n = max(len(vis), math.ceil(dur / MAX_SHOT), 1)
        if not vis:
            card = visuals.card(sc.get("card") or sc["text"][:70], workdir / f"fc{i}.png", template)
            vis = [("card", card, 0, None)]
        for c in vis:
            if c[3] and c[3] != "Pixabay":
                credits.append(c[3])
        for j in range(n):
            kind, p, _, _ = vis[j % len(vis)]
            sd = dur / n
            seg = workdir / f"shot{i:02d}_{j}.mp4"
            if kind == "video":
                _shot_video(p, sd, seg)
            elif kind == "card":
                _shot_still(p, sd, seg, "down" if j % 2 else "up", scale=1.04)
            else:  # alternate framing on reuse so a repeated photo reads as a new shot
                _shot_still(p, sd, seg, MOVES[mi % 4], scale=1.14 if j < len(vis) else 1.38)
                mi += 1
            shots.append(seg)
    end = _shot_still(end_card, OUTRO, workdir / "shot_end.mp4", "none", scale=1.0)
    shots.append(end)
    lst = workdir / "shots.txt"
    lst.write_text("".join(f"file '{s.resolve()}'\n" for s in shots))
    silent = workdir / "silent.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)])

    total = body_end + OUTRO
    cues = [(LEAD + a, LEAD + b, txt) for (a, b), txt in zip(sent_times, shows)]
    subs = _subtitle_track(cues, total, workdir)
    hook = _hook_png(headline, kicker, workdir / "hook.png")
    bed = _bed(workdir, total)
    fc = (f"[1:v]format=rgba[s];[0:v][s]overlay=0:0:eof_action=pass[v1];"
          f"[2:v]format=rgba,fade=t=out:st={HOOK_SECS - 0.4:.2f}:d=0.4:alpha=1[h];"
          f"[v1][h]overlay=0:0:eof_action=pass,format=yuv420p[v];"
          f"[3:a]adelay={int(LEAD * 1000)}|{int(LEAD * 1000)},apad[vo];"
          f"[4:a]volume={MUSIC_DB}dB,afade=t=in:d=1.0,afade=t=out:st={total - 2.0:.2f}:d=2.0[m];"
          f"[vo][m]amix=inputs=2:duration=first:normalize=0[a]")
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent),
          "-f", "concat", "-safe", "0", "-i", str(subs),
          "-loop", "1", "-framerate", str(FPS), "-t", f"{HOOK_SECS:.2f}", "-i", str(hook),
          "-i", str(voice), "-stream_loop", "-1", "-i", str(bed),
          "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
          "-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p", "-r", str(FPS),
          "-g", str(FPS * 2), "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k",
          "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])
    (workdir / "credits.txt").write_text("\n".join(dict.fromkeys(credits)), encoding="utf-8")
    return out

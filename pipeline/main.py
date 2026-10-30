"""One run = one Reel. Exit 0 on publish errors (no spammy retries); state in posted.json."""
import json
import random
import shutil
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import os
from . import config, facebook, render, score, sources, token, tts, visuals, write


def posts_last_24h(posted):
    cut = datetime.now(timezone.utc) - timedelta(hours=24)
    return sum(1 for p in posted if p.get("published_at") and datetime.fromisoformat(p["published_at"]) > cut)


def evergreen_item(posted):
    used = {p["id"] for p in posted}
    items = sorted(config.EVERGREEN.glob("*.json"))
    fresh = [p for p in items if f"ever-{p.stem}" not in used] or items
    p = random.choice(fresh)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["id"] = f"ever-{p.stem}"
    data["source"] = data.get("source", "Nepal Canvas")
    return data


def alert(subject, body):
    to, pw = os.getenv("ALERT_EMAIL"), os.getenv("GMAIL_APP_PASSWORD")
    if not (to and pw):
        return
    m = EmailMessage(); m["Subject"] = subject; m["From"] = to; m["To"] = to; m.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(to, pw); s.send_message(m)
    except Exception as e:
        print(f"[alert] {e}")


def main():
    config.FB_PAGE_TOKEN = token.refresh_if_needed(os.getenv('FB_APP_ID', ''), os.getenv('FB_APP_SECRET', ''))
    slot = config.slot_for_now()
    posted = sources.load_posted()
    print(f"[main] slot={slot} mode={config.MODE} posted24h={posts_last_24h(posted)}")
    if config.MODE != "dry" and posts_last_24h(posted) >= config.MAX_POSTS_24H:
        print("[main] 24h cap reached, skipping"); return

    work = config.OUT / datetime.now(config.NPT).strftime("%Y%m%d-%H%M")
    work.mkdir(parents=True, exist_ok=True)

    story = score.pick(sources.candidates(), slot) if config.GEMINI_API_KEY else None
    if story:
        pack = write.write(story)
        pack["id"] = story["id"]; pack["title"] = story["title"]; pack["link"] = story.get("link", "")
    else:
        pack = evergreen_item(posted)
        print(f"[main] evergreen: {pack['id']}")

    # voice
    voice = work / "voice.mp3"
    if config.MODE == "dry" and not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg/ffprobe required")
    try:
        vlen = tts.synth(pack["script"], voice)
    except Exception as e:
        print(f"[main] tts failed: {e}")
        alert("Nepal Canvas: TTS failed", str(e)); return

    # cards
    tpl = random.choice(visuals.TEMPLATES)
    cards = [visuals.card(pack["headline"], work / "c0.png", tpl, kicker=pack.get("kicker", "आजको खबर"))]
    for i, c in enumerate(pack.get("cards", []), 1):
        cards.append(visuals.card(c, work / f"c{i}.png", tpl))
    cards.append(visuals.card(pack.get("takeaway", ""), work / "c_end.png", tpl, kicker="तपाईंका लागि",
                              small=f"स्रोत: {pack['source']}"))
    # b-roll
    broll = visuals.broll(pack.get("keywords", ["Nepal", "Kathmandu"]), work)

    out = work / "reel.mp4"
    render.build(voice, vlen, cards, broll, work, out)
    caption = f"{pack.get('caption_ne', pack['headline'])}\n{pack.get('caption_en', '')}\n" \
              f"स्रोत: {pack['source']}\n{' '.join(pack['hashtags'])}"
    (work / "caption.txt").write_text(caption, encoding="utf-8")
    print(f"[main] rendered {out} ({vlen:.0f}s voice)")

    if config.MODE == "dry":
        return
    try:
        vid = facebook.publish_reel(out, caption, "DRAFT" if config.MODE == "draft" else "PUBLISHED")
    except Exception as e:
        print(f"[main] publish failed: {e}")
        alert("Nepal Canvas: publish failed", f"{pack['headline']}\n{e}")
        return  # exit 0 on purpose
    posted.append({"id": pack["id"], "title": pack.get("title", pack["headline"]), "fb_video_id": vid,
                   "slot": slot, "published_at": datetime.now(timezone.utc).isoformat()})
    config.POSTED.write_text(json.dumps(posted[-500:], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[main] published video {vid}")


if __name__ == "__main__":
    main()

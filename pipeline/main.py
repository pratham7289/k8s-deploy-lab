"""One run = one Reel. Exit 0 on publish errors (no spammy retries); state in posted.json."""
import json
import random
import re
import shutil
import subprocess
import time
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import os
from . import config, facebook, infocards, link, render, scenes, schedule, score, sources, token, tts, visuals, write


MAX_VOICE = 83.0  # LEAD + voice + TAIL + OUTRO must stay under 90 s


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
    slot = os.getenv("SLOT_KIND") or config.slot_for_now()
    slot_key, slot_time = os.getenv("SLOT_KEY", ""), os.getenv("SLOT_TIME", "")
    posted = sources.load_posted()
    link_url = os.getenv("LINK", "").strip()
    link_story = None
    if link_url:  # the owner pasted a link: build and publish it now (or at AT=HH:MM Nepal time)
        try:
            link_story = link.story_from_link(link_url)
        except Exception as e:
            print(f"[main] link failed: {e}")
            alert("Nepal Canvas: link could not be read", f"{link_url}\n{e}")
            raise SystemExit(1)
        slot, slot_key = "breaking", f"link-{link_story['id']}"
        at_txt = os.getenv("AT", "").strip()
        if at_txt:
            hh, mm = (int(x) for x in at_txt.split(":"))
            t = datetime.now(config.NPT).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if t < datetime.now(config.NPT):
                t += timedelta(days=1)
            slot_time = t.isoformat()
    print(f"[main] slot={slot} key={slot_key or 'manual'} for={slot_time or 'now'} mode={config.MODE} "
          f"posted24h={posts_last_24h(posted)}")
    if slot_key and any(p.get("slot_key") == slot_key for p in posted):
        print("[main] slot already done"); return
    cap = 29 if link_url else config.MAX_POSTS_24H  # owner links may use the last slots under Meta's 30
    if config.MODE != "dry" and posts_last_24h(posted) >= cap:
        print("[main] 24h cap reached, skipping"); return

    work = config.OUT / datetime.now(config.NPT).strftime("%Y%m%d-%H%M")
    work.mkdir(parents=True, exist_ok=True)

    breaking = os.getenv("BREAKING") == "1"
    if link_story:
        story = link_story
    elif breaking:
        # extra Reel only for a story many outlets are carrying right now; otherwise post nothing
        cands = sources.candidates()
        hot = [c for c in cands if c.get("category") != "india" and c.get("age_h", 99) <= 3
               and (c.get("coverage", 0) >= 4 or c.get("trend", 0) >= 70)]
        print(f"[main] breaking check: {len(hot)} hot stories")
        if not hot:
            return
        config.MIN_SCORE = max(config.MIN_SCORE, 80)
        story = score.pick(hot, "breaking")
        if not story:
            print("[main] no story strong enough for a breaking Reel"); return
    else:
        story = score.pick(sources.candidates(), slot) if config.GEMINI_API_KEY else None
    if story:
        pack = write.write(story)
        pack["id"] = story["id"]; pack["title"] = story["title"]; pack["link"] = story.get("link", "")
    else:
        pack = write.finish(evergreen_item(posted))
        print(f"[main] evergreen: {pack['id']}")

    # voice: one take, hard length budget (Reels API max is 90 s)
    voice = work / "voice.mp3"
    t0 = time.time()
    for attempt in range(2):
        says = [s["say"] for s in pack["sentences"]]
        try:
            vlen = tts.synth(pack["script"], voice, says)
        except Exception as e:
            print(f"[main] tts failed: {e}")
            alert("Nepal Canvas: TTS failed", str(e)); return
        if vlen > MAX_VOICE and vlen <= MAX_VOICE * 1.10:
            fast = work / "voice_fast.mp3"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice), "-af",
                            f"atempo={vlen / MAX_VOICE:.3f}", "-b:a", "160k", str(fast)], check=True)
            fast.replace(voice); vlen = tts.duration(voice)
            print(f"[main] voice sped up to {vlen:.1f}s")
        if vlen <= MAX_VOICE:
            break
        if attempt == 0 and story:
            print(f"[main] voice {vlen:.0f}s too long -> shorter rewrite")
            keep = {k: pack[k] for k in ("id", "title", "link")}
            pack = write.write(story, shorter=True); pack.update(keep)
            continue
        print(f"[main] voice {vlen:.0f}s over budget, not publishing")
        alert("Nepal Canvas: script too long", f"{pack['headline']} {vlen:.0f}s"); return
    says = [s["say"] for s in pack["sentences"]]
    shows = [s["show"] for s in pack["sentences"]]
    sent_times = tts.sentence_times(voice, says)

    # scene plan: what is on screen while each sentence is spoken
    tpl = random.choice(visuals.TEMPLATES)
    end_card = visuals.card(pack.get("takeaway", ""), work / "c_end.png", tpl, kicker="तपाईंका लागि",
                            small=f"स्रोत: {pack['source']}")
    try:
        plan = scenes.plan(pack["script"], pack["headline"], pack.get("category", "news")) if config.GEMINI_API_KEY else []
    except Exception as e:
        print(f"[main] scene plan failed: {e}"); plan = []
    if not plan:  # fallback: split sentences evenly over the cards
        cards = pack.get("cards") or [pack["headline"]]
        k = max(len(says) // max(len(cards), 1), 1)
        plan = [{"text": " ".join(says[i*k:(i+1)*k]) or c, "card": c, "subject": "", "query_en": kw, "source": "stock"}
                for i, (c, kw) in enumerate(zip(cards, (pack.get("keywords") or ["Nepal"]) * 4))]
    used, shots = set(), 0
    for i, sc in enumerate(plan):
        est = len(sc.get("text", "")) / 14  # ~14 Devanagari chars per second of speech
        info = []
        try:
            if i > 0 and sc.get("map"):
                m = infocards.map_card(sc["map"], sc.get("map_ne", ""), pack.get("kicker", ""), work / f"map{i}.png")
                if m:
                    info.append(("card", m, 0, "geoBoundaries (CC BY 4.0)"))
            num = sc.get("number") or {}
            if i > 0 and num.get("value") and any(ch.isdigit() for ch in num["value"]):
                n = infocards.number_card(num["value"], num.get("label", ""), work / f"num{i}.png")
                if n:
                    info.append(("card", n, 0, None))
        except Exception as e:
            print(f"[main] info card failed: {e}")
        want = max(1, min(3, round(est / 4)) - len(info))
        sc["visuals"] = info + scenes.fetch_for_scene(sc, work, i, want=want, used=used)
        shots += len(sc["visuals"])
    print(f"[main] scenes={len(plan)} visuals={shots} ({time.time() - t0:.0f}s so far)")

    out = work / "reel.mp4"
    t1 = time.time()
    render.build(voice, vlen, says, shows, sent_times, pack["headline"], pack.get("kicker", ""), end_card,
                 plan, work, out, tpl, punch=pack.get("punch", ""))
    total = tts.duration(out)
    print(f"[main] rendered {out} total={total:.1f}s voice={vlen:.1f}s render={time.time() - t1:.0f}s")
    credits = [c for c in (work / "credits.txt").read_text(encoding="utf-8").splitlines() if c.strip()]
    photo_line = ("\nतस्बिर: " + "; ".join(credits[:5])) if credits else ""
    caption = f"{pack.get('caption_ne', pack['headline']).strip()}\n\n{pack.get('caption_en', '').strip()}\n\n" \
              f"स्रोत: {pack['source']}{photo_line}\n\n{' '.join(pack['hashtags'])}"
    (work / "caption.txt").write_text(caption, encoding="utf-8")
    print("[main] caption:\n" + caption)
    if total > 89.5:
        print("[main] video over 90 s, not publishing"); alert("Nepal Canvas: video too long", str(total)); return

    if config.MODE == "dry":
        return
    try:
        state, at = ("DRAFT" if config.MODE == "draft" else "PUBLISHED"), None
        if state == "PUBLISHED" and slot_time:
            at = datetime.fromisoformat(slot_time)
            if (at - datetime.now(timezone.utc)).total_seconds() >= schedule.MIN_LEAD * 60:
                state = "SCHEDULED"
            else:
                at = None
        vid = facebook.publish_reel(out, caption, state, int(at.timestamp()) if at else None)
        print(f"[main] {state.lower()} video {vid}" + (f" for {at.isoformat()}" if at else ""))
    except Exception as e:
        print(f"[main] publish failed: {e}")
        alert("Nepal Canvas: publish failed", f"{pack['headline']}\n{e}")
        return  # exit 0 on purpose
    posted.append({"id": pack["id"], "title": pack.get("title", pack["headline"]), "fb_video_id": vid,
                   "source": pack.get("source", ""),
                   "slot": slot, "slot_key": (slot_key or None) if state != "DRAFT" else None, "state": state,
                   "scheduled_for": at.isoformat() if at else None,
                   "published_at": (at or datetime.now(timezone.utc)).isoformat()})
    config.POSTED.write_text(json.dumps(posted[-500:], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[main] published video {vid}")
    try:
        from . import analytics
        analytics.update(posted, config.FB_PAGE_TOKEN)
    except Exception as e:
        print(f"[main] stats skipped: {e}")


if __name__ == "__main__":
    main()

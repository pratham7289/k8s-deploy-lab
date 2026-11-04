"""Score candidates 0-100 and pick one for the slot."""
import json

from . import config, llm, sources, trending

# Big outlets whose article pages we can actually read from GitHub's servers, best first. When the chosen
# story is also carried by one of these, the Reel is made from (and credited to) that outlet instead.
# Kantipur and Ujyaalo block automated readers (Cloudflare 403), so they only appear via Google News.
PREFERRED = ["Onlinekhabar", "BBC Nepali", "Setopati", "Ratopati", "Gorkhapatra", "Khabarhub",
             "Baahrakhari", "Image Khabar", "Onlinekhabar English"]
PER_OUTLET = 4      # max stories per outlet in the shortlist the editor model sees
REPEAT_PENALTY = 8  # score points off per recent post from the same outlet


def _recent_titles(hours=72):
    """Titles posted (or scheduled) in the last `hours`, so the editor can reject the same event in new words."""
    from datetime import datetime, timedelta, timezone
    now, out = datetime.now(timezone.utc), []
    for p in sources.load_posted():
        try:
            if now - datetime.fromisoformat(p["published_at"]) < timedelta(hours=hours) and p.get("state") != "DELETED":
                out.append(p.get("title", ""))
        except Exception:
            pass
    return out[-60:]


def _recent_outlets(n=3):
    return [p.get("source", "") for p in sources.load_posted()[-n:]]


def _same_story(a, b):
    ta, tb = trending.tokens(a["title"]), trending.tokens(b["title"])
    return bool(ta and tb) and len(ta & tb) >= max(2, 0.4 * min(len(ta), len(tb)))


def _is_repeat(story, recent):
    """Second, strict check by the model: same main person/institution + same case/issue = same story."""
    if not recent:
        return False
    try:
        res = llm.ask_json(
            "You check a Nepali news page for repeats. Answer ONLY JSON {\"repeat\": true|false, \"which\": \"...\"}. "
            "repeat=true if the candidate is about the same main person, institution or incident AND the same case, "
            "issue, event or decision as ANY recent title, even if it is a later stage (charge, hearing, verdict, "
            "update, reaction), uses different words or spelling, or comes from another outlet. "
            "Different people or different issues are not repeats.",
            json.dumps({"candidate": story["title"] + " — " + story.get("text", "")[:300], "recent": recent},
                       ensure_ascii=False), temperature=0)
        if res.get("repeat"):
            print(f"[score] repeat of: {str(res.get('which', ''))[:70]}")
            return True
    except Exception as e:
        print(f"[score] repeat check failed: {e}")
    return False


def _best_outlet(story, cands):
    """Only when the chosen item has no readable article (Google News link): use the same story from a readable outlet."""
    if "news.google.com" not in story.get("link", ""):
        return story
    twins = [c for c in cands if c is not story and c.get("source") in PREFERRED
             and "news.google.com" not in c.get("link", "") and _same_story(story, c)]
    rank = lambda c: PREFERRED.index(c["source"])
    cur = PREFERRED.index(story["source"]) if story.get("source") in PREFERRED else len(PREFERRED)
    better = [c for c in twins if rank(c) < cur]
    if "news.google.com" in story.get("link", "") and twins:
        better = better or twins          # Google News link: no article text, use a readable twin
    if better:
        b = min(better, key=rank)
        print(f"[score] same story from {b['source']} instead of {story['source']}")
        return b
    return story


def pick(cands, slot: str):
    if not cands:
        return None
    prefer = config.SLOTS[slot]["prefer"]
    # keep the prompt small: the 35 newest, most-covered stories (Indian outlets capped at 8)
    nepal = [s for s in cands if s["category"] != "india"]
    india = [s for s in cands if s["category"] == "india"][:8]
    allc = cands
    ordered = sorted(nepal[:120] + india, key=lambda s: (s.get("age_h", 99) > 12, -s.get("trend", 0), -s.get("coverage", 0), s.get("age_h", 99)))
    per, cands = {}, []
    for s in ordered:  # no single outlet may fill the shortlist
        if per.get(s["source"], 0) < PER_OUTLET:
            per[s["source"]] = per.get(s["source"], 0) + 1
            cands.append(s)
        if len(cands) >= 40:
            break
    recent = _recent_outlets()
    rows = [{"i": i, "source": s["source"], "category": s["category"], "title": s["title"],
             "snippet": s["text"][:300], "coverage": s.get("coverage", 0), "age_h": s.get("age_h"), "trend": s.get("trend", 0),
             "traffic": s.get("traffic", "")} for i, s in enumerate(cands)]
    res = llm.ask_json(
        config.prompt("score"),
        json.dumps({"slot": slot, "preferred_categories": prefer, "trending_now": sources.TREND_CONTEXT,
                    "already_posted_last_3_days": _recent_titles(),
                    "candidates": rows}, ensure_ascii=False),
        temperature=0.2,
    )
    scored = res.get("scored", [])
    best = None
    for row in scored:
        try:
            i = int(row["i"]); sc = int(row["score"])
        except Exception:
            continue
        if not row.get("verified", False) or row.get("reject") or row.get("same_as_posted"):
            continue
        if sc < config.MIN_SCORE or i >= len(cands):
            continue
        adj = sc - REPEAT_PENALTY * recent.count(cands[i]["source"])  # rotate outlets
        if best is None or adj > best[3]:
            best = (sc, i, row, adj)
    if best is None:
        print("[score] nothing >= MIN_SCORE")
        return None
    sc, i, row, _ = best
    recent_titles = _recent_titles()
    if _is_repeat(cands[i], recent_titles):  # try the next best ones
        ranked = sorted((r for r in scored if r is not row), key=lambda r: -int(r.get("score", 0) or 0))
        best = None
        for r in ranked[:4]:
            try:
                j = int(r["i"]); scj = int(r["score"])
            except Exception:
                continue
            if (scj < config.MIN_SCORE or j >= len(cands) or not r.get("verified") or r.get("reject")
                    or r.get("same_as_posted") or _is_repeat(cands[j], recent_titles)):
                continue
            best = (scj, j, r, scj); break
        if best is None:
            print("[score] only repeats available")
            return None
        sc, i, row, _ = best
    s = dict(_best_outlet(cands[i], allc)); s["score"] = sc; s["category"] = row.get("category", s["category"])
    s["reason"] = row.get("reason", "")
    print(f"[score] picked {sc}: {s['title'][:70]}")
    return s

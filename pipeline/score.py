"""Score candidates 0-100 and pick one for the slot."""
import json

from . import config, llm, sources


def pick(cands, slot: str):
    if not cands:
        return None
    prefer = config.SLOTS[slot]["prefer"]
    # keep the prompt small: the 35 newest, most-covered stories (Indian outlets capped at 8)
    nepal = [s for s in cands if s["category"] != "india"]
    india = [s for s in cands if s["category"] == "india"][:8]
    cands = sorted(nepal[:80] + india, key=lambda s: (s.get("age_h", 99) > 12, -s.get("trend", 0), -s.get("coverage", 0), s.get("age_h", 99)))[:40]
    rows = [{"i": i, "source": s["source"], "category": s["category"], "title": s["title"],
             "snippet": s["text"][:300], "coverage": s.get("coverage", 0), "age_h": s.get("age_h"), "trend": s.get("trend", 0),
             "traffic": s.get("traffic", "")} for i, s in enumerate(cands)]
    res = llm.ask_json(
        config.prompt("score"),
        json.dumps({"slot": slot, "preferred_categories": prefer, "trending_now": sources.TREND_CONTEXT,
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
        if not row.get("verified", False) or row.get("reject"):
            continue
        if sc >= config.MIN_SCORE and (best is None or sc > best[0]):
            best = (sc, i, row)
    if best is None:
        print("[score] nothing >= MIN_SCORE")
        return None
    sc, i, row = best
    s = dict(cands[i]); s["score"] = sc; s["category"] = row.get("category", s["category"])
    s["reason"] = row.get("reason", "")
    print(f"[score] picked {sc}: {s['title'][:70]}")
    return s

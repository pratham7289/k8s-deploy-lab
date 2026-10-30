"""Score candidates 0-100 and pick one for the slot."""
import json

from . import config, llm


def pick(cands, slot: str):
    if not cands:
        return None
    prefer = config.SLOTS[slot]["prefer"]
    # keep the prompt small: top 25 by coverage, then recency
    cands = sorted(cands, key=lambda s: (-s.get("coverage", 0), s["published"]), reverse=False)[:25]
    rows = [{"i": i, "source": s["source"], "category": s["category"], "title": s["title"],
             "snippet": s["text"][:300], "coverage": s.get("coverage", 0),
             "traffic": s.get("traffic", "")} for i, s in enumerate(cands)]
    res = llm.ask_json(
        config.prompt("score"),
        json.dumps({"slot": slot, "preferred_categories": prefer, "candidates": rows}, ensure_ascii=False),
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

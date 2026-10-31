"""Turn one story into a short hook-first script (spoken + on-screen forms), cards and caption."""
import json
import re

import requests

from . import config, llm, sources

MIN_WORDS, MAX_WORDS = 55, 85
DIGIT_OR_LATIN = re.compile(r"[0-9०-९A-Za-z]")


def fetch_article(link: str) -> str:
    """Pull the page text so the script uses the article, not just the RSS snippet."""
    try:
        r = requests.get(link, headers=sources.UA, timeout=20)
        text = sources.TAG.sub(" ", r.text)
        text = " ".join(text.split())
        return text[:6000]
    except Exception:
        return ""


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[।!?])\s+", text.strip()) if s.strip()]


def finish(res: dict) -> dict:
    """Normalise a pack: sentences [{say, show}] + script (spoken) + subtitle lines."""
    sents = [s for s in res.get("sentences") or [] if isinstance(s, dict) and s.get("say", "").strip()]
    if not sents and res.get("script"):
        sents = [{"say": s, "show": s} for s in split_sentences(res["script"])]
    for s in sents:
        s["say"] = s["say"].strip()
        s["show"] = (s.get("show") or s["say"]).strip()
    res["sentences"] = sents
    res["script"] = " ".join(s["say"] for s in sents)
    return res


def _words(res: dict) -> int:
    return len(res.get("script", "").split())


def write(story: dict, shorter: bool = False) -> dict:
    article = fetch_article(story["link"]) if story.get("link") else ""
    payload = {"source": story["source"], "title": story["title"], "category": story["category"],
               "rss_text": story["text"], "article_text": article or story["text"]}
    if shorter:
        payload["instruction"] = "Keep it to 55-65 words in total. Same facts only."
    res = finish(llm.ask_json(config.prompt("write"), json.dumps(payload, ensure_ascii=False), temperature=0.5))
    w = _words(res)
    if not MIN_WORDS <= w <= MAX_WORDS:  # one rewrite pass towards the target length
        payload["previous_sentences"] = res.get("sentences", [])
        payload["instruction"] = (f"The previous version had {w} words. Rewrite it to 65-80 words in total, "
                                  "same facts only, hook first, same JSON format.")
        res2 = finish(llm.ask_json(config.prompt("write"), json.dumps(payload, ensure_ascii=False), temperature=0.5))
        if abs(_words(res2) - 72) < abs(w - 72):
            res = res2
    # hard rules regardless of what the model returned
    for s in res["sentences"]:
        if DIGIT_OR_LATIN.search(s["say"]):
            print(f"[write] digits/latin left in spoken text: {s['say'][:60]}")
    res["hashtags"] = (config.BASE_HASHTAGS + [h for h in res.get("hashtags", []) if h.startswith("#")])[:5]
    res["cards"] = [c[:60] for c in res.get("cards", [])][:4]
    res["headline"] = res.get("headline", story["title"])[:60]
    res["source"] = story["source"]
    print(f"[write] {_words(res)} words, {len(res['sentences'])} sentences")
    return res

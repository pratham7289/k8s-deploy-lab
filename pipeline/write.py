"""Turn one story into script + cards + caption + b-roll keywords."""
import json

import requests

from . import config, llm, sources


def fetch_article(link: str) -> str:
    """Pull the page text so the script uses the article, not just the RSS snippet."""
    try:
        r = requests.get(link, headers=sources.UA, timeout=20)
        text = sources.TAG.sub(" ", r.text)
        text = " ".join(text.split())
        return text[:6000]
    except Exception:
        return ""


def write(story: dict) -> dict:
    article = fetch_article(story["link"]) if story.get("link") else ""
    payload = {"source": story["source"], "title": story["title"], "category": story["category"],
               "rss_text": story["text"], "article_text": article or story["text"]}
    res = llm.ask_json(config.prompt("write"), json.dumps(payload, ensure_ascii=False), temperature=0.5)
    if len(res.get("script", "").split()) < 110:  # too short for a 45-75 s Reel: one rewrite pass
        payload["previous_script"] = res.get("script", "")
        payload["instruction"] = "The previous script was too short. Rewrite it to 130-170 words, same facts only, add context and the 'what it means for you' part."
        res2 = llm.ask_json(config.prompt("write"), json.dumps(payload, ensure_ascii=False), temperature=0.6)
        if len(res2.get("script", "").split()) > len(res.get("script", "").split()):
            res = res2
    # hard rules regardless of what the model returned
    res["hashtags"] = (config.BASE_HASHTAGS + [h for h in res.get("hashtags", []) if h.startswith("#")])[:5]
    res["cards"] = [c[:60] for c in res.get("cards", [])][:4]
    res["headline"] = res.get("headline", story["title"])[:70]
    res["source"] = story["source"]
    words = len(res.get("script", "").split())
    if words < 90 or words > 200:
        print(f"[write] script {words} words – outside 90-200, still using")
    return res

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
    res = llm.ask_json(
        config.prompt("write"),
        json.dumps({
            "source": story["source"], "title": story["title"], "category": story["category"],
            "rss_text": story["text"], "article_text": article or story["text"],
        }, ensure_ascii=False),
        temperature=0.5,
    )
    # hard rules regardless of what the model returned
    res["hashtags"] = (config.BASE_HASHTAGS + [h for h in res.get("hashtags", []) if h.startswith("#")])[:5]
    res["cards"] = [c[:60] for c in res.get("cards", [])][:4]
    res["headline"] = res.get("headline", story["title"])[:70]
    res["source"] = story["source"]
    words = len(res.get("script", "").split())
    if words < 90 or words > 200:
        print(f"[write] script {words} words – outside 90-200, still using")
    return res

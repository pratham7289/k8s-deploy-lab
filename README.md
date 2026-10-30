# Nepal Canvas pipeline

Posts 5 original Nepali news Reels a day to the **Nepal Canvas** Facebook Page. Runs on GitHub Actions. Zero cost, no cloud account, no card.

```
RSS + Trends → Gemini Flash (score + Nepali script) → edge-tts voice → Pexels b-roll + Devanagari cards → ffmpeg → Facebook Reels API
```

## One-time setup (≈ 25 min)

1. **Page** — create the Facebook Page "Nepal Canvas" (News & media website). Note its Page ID (About → Page transparency).
2. **Meta app** — developers.facebook.com → Create app → type *Business* → name "Nepal Canvas Publisher". Leave it in **Development mode** (you are the only user, so no App Review is needed).
3. **User token** — Tools → Graph API Explorer → pick the app → Permissions: `pages_show_list pages_read_engagement pages_manage_posts publish_video` → *Generate Access Token* (one login). Copy it.
4. **Page token** — on your PC:
   ```
   APP_ID=... APP_SECRET=... USER_TOKEN=... python scripts/auth.py
   ```
   It prints `FB_PAGE_ID` and a non-expiring `FB_PAGE_TOKEN`.
5. **Keys** — Google AI Studio → Gemini API key. pixabay.com → log in → pixabay.com/api/docs shows your key (Pexels is optional; new Pexels keys are paused).
6. **GitHub** — push this repo (private). Settings → Secrets and variables → Actions → add:
   `GEMINI_API_KEY`, `PIXABAY_API_KEY` (optionally `PEXELS_API_KEY`), `FB_PAGE_ID`, `FB_PAGE_TOKEN` (the 60-day token from the Access Token Debugger's *Extend* button), `FB_APP_ID`, `FB_APP_SECRET` (App settings → Basic). The job refreshes the token itself when < 20 days remain; optional `ALERT_EMAIL` + `GMAIL_APP_PASSWORD` for failure emails.
7. **Music** — drop 10–20 royalty-free MP3s from pixabay.com/music into `assets/music/`. Optional: a 2 s `assets/intro/intro.mp4` (1080×1920).

## Test before going live

- `make dry-run` — builds `out/<timestamp>/reel.mp4` locally, publishes nothing.
- Actions → *post-reel* → *Run workflow* with mode `draft` — posts one Reel as a **draft** to the Page so you can check it in Meta Business Suite.
- Happy? The cron takes over: 07:00, 10:00, 13:00, 16:00, 18:00 Nepal time.

## How a run decides

- Candidates older than 36 h or already in `posted.json` are dropped.
- Gemini scores freshness / public interest / source reliability / visual potential / uniqueness, marks `verified`, and rejects allegations, rumours, gossip and graphic content.
- Nothing ≥ `MIN_SCORE` (60) → an item from `evergreen/` is posted instead. Add your own JSON files there (same shape as the existing ones).
- Script text may only use the fetched article; the outlet is credited on screen and in the caption.
- Hard caps: 25 API posts per 24 h (Meta allows 30). Any publish error → saved MP4, exit 0, email alert. No retries.

## Files

| Path | What |
| --- | --- |
| `pipeline/sources.py` | RSS / Google News / Trends / DHM fetch, dedupe, coverage count |
| `pipeline/score.py`, `write.py`, `llm.py` | Gemini calls; prompts live in `prompts/*.txt` |
| `pipeline/tts.py` | edge-tts `ne-NP-Hemkala` / `ne-NP-Sagar`, alternating daily |
| `pipeline/visuals.py` | Pexels b-roll, Pillow cards with Noto Sans Devanagari (OFL) |
| `pipeline/render.py` | ffmpeg: intro → headline → b-roll/cards under voice → credit; music ducked |
| `pipeline/facebook.py` | Reels Publishing API: start → upload → finish |
| `pipeline/main.py` | the run; `MODE=publish|draft|dry`, `SLOT=...` |
| `.github/workflows/post.yml` | cron + manual trigger; commits `posted.json` back |

## Free limits vs use (150 videos/month)

Gemini Flash free tier (15 calls/day) · edge-tts (none) · Pexels 20,000 req/month (~900) · GitHub Actions 2,000 min (~750) · Meta Reels API 30 posts/day (5).

## Not done on purpose

No downloading of other creators' TikTok/YouTube videos, no auto-likes or comment bots. Meta cuts monetization and reach for reused content; this Page is built to be monetized.

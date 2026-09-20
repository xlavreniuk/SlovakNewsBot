# 🧠 SlovakNewsBot — Comprehensive Project Context & Architecture

This document preserves the full context, architecture, operational history, and extension guidelines for this project. Any AI assistant or developer can read this file to understand the entire system and modify or extend it safely.

---

## 1. Project Overview

**SlovakNewsBot** is a 100% autonomous, configuration-driven news aggregation and copywriting pipeline running 24/7 on **GitHub Actions ($0/month)**.

* **Target Audience & Channel:** [t.me/bratislavanewss](https://t.me/bratislavanewss) (`@bratislavanewss`, Chat ID: `-1003978794281`)
* **Telegram Bot:** `@xlvrnewsbot`
* **Target Scope:** Strictly **Bratislava** and its districts (Staré Mesto, Petržalka, Ružinov, Nové Mesto, Rača, Dúbravka, Karlova Ves, etc.). Stories from other cities are strictly rejected.
* **Content Style:** Clear, professional, and informative city news tone in **Ukrainian** for the average reader (no tabloid/yellow journalism, no sarcasm, max **1 emoji** per post), 100% strictly factual with zero hallucinations.
* **Format:** Every post is published as a **photo post** (direct multipart binary upload of verified high-definition photos) with formatted HTML caption and a clickable channel footer: `[Братислава](https://t.me/bratislavanewss)`. External sources are omitted from the footer to keep traffic inside the channel.

---

## 2. Core Modules & Codebase Architecture

```text
.
├── config.yaml               # Country, city, feeds, AI thresholds, writing style, schedule settings
├── config.py                 # Dataclasses & YAML/environment loader
├── scraper.py                # Universal RSS scraper + Pillow high-resolution image engine
├── ai_engine.py              # Gemini AI client: batch triage, deduplication, Ukrainian copywriter & emoji enforcer
├── telegram_poster.py        # Telegram Bot API client: binary photo upload, Pillow format optimization, HTML sanitization
├── state_manager.py          # seen_urls.json manager: persistent URL history & auto-pruning
├── main.py                   # Orchestrator with support for live runs and --dry-run
├── seen_urls.json            # State file tracking processed articles & recent post titles
├── requirements.txt          # Minimal, reliable Python dependencies (including Pillow)
├── .env.example              # Template for environment secrets
├── .env                      # Local environment secrets (git-ignored)
└── .github/workflows/
    └── run.yml               # GitHub Actions 24/7 cloud runner with dual day/night cron
```

### Module Responsibilities:

1. **`config.yaml` & `config.py`**:
   - Universal configuration: to adapt the bot to any other country or city, change only this YAML file without altering Python code.
   - Parses `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, and `GEMINI_API_KEY`.
   - Defines image resolution thresholds (`min_image_width: 500`, `min_image_height: 300`).

2. **`scraper.py`**:
   - Fetches RSS feeds with desktop browser `User-Agent`.
   - **High-Resolution Image Extraction Engine**:
     - Scrapes webpage candidates: OpenGraph (`og:image`), Twitter Card, JSON-LD, `srcset` descriptors, lead figures.
     - CMS & CDN upscaling heuristics: strips WordPress resized dimensions (e.g. `-724x1024.png`), upgrades Pravda (`/16x9-big/`), upgrades Topky (`/1280px/`), strips resizing query parameters.
     - Probes dimensions using **Pillow** (`PIL.Image`) and ranks candidate images by pixel resolution, selecting the highest-definition valid image.
     - Enforces minimum dimensions ($500\times 300$) and filters out icons, tracking pixels, or distorted banners.

3. **`ai_engine.py`**:
   - Uses Google AI Studio REST API (`gemini-3.5-flash-lite`, with fallbacks to `gemini-3.1-flash-lite` and `gemini-flash-latest`).
   - **Batch Triage**: Groups 10 articles into a single prompt to stay well below the 15 RPM free tier limit.
   - **Strict Location Filter**: Rejects any story outside Bratislava with score `0`.
   - **Semantic Deduplication**: Checks incoming articles against recently posted headlines to prevent reporting the same incident twice.
   - **Ukrainian Copywriter**: Rewrites the story in clear, accessible, professional Ukrainian with full translation of Slovak terms and institutions.
   - **Emoji Enforcer**: Programmatically ensures at most 1 calm, relevant emoji is present across the entire post.
   - Cleans HTML tags (`<br>` and `<p>` converted to `\n`) for Telegram Bot API compliance.

4. **`telegram_poster.py`**:
   - Inspects and prepares downloaded image bytes with **Pillow**.
   - Converts exotic formats (AVIF, WebP, CMYK) into high-quality RGB JPEG (`quality=95`, `optimize=True`) for guaranteed Telegram retina rendering.
   - Uploads directly to Telegram via `multipart/form-data` to avoid CDN anti-bot blocks.
   - Strictly enforces `require_image=True`.

5. **`state_manager.py`**:
   - Records processed URLs in `seen_urls.json`.
   - Records recent posted story titles and timestamps.
   - Auto-prunes records older than 14 days to prevent git repository bloat.

6. **`.github/workflows/run.yml`**:
   - Runs 100% autonomously in GitHub Actions cloud ($0/month).
   - Dual Schedule (Bratislava local time):
     - **Day (08:00 – 00:00):** Every 30 minutes (`*/30 6-22 * * *` UTC)
     - **Night (00:00 – 08:00):** Every 2 hours (`0 0,2,4 * * *` UTC)
   - Concurrency group prevents overlapping runs.
   - Automatically commits updated `seen_urls.json` back to GitHub if changed.

---

## 3. Active Feeds & Credentials

### Active Feeds in `config.yaml`:
* **Bratislavský Kraj:** `https://bratislavskykraj.sk/feed/`
* **Pravda Regióny:** `https://spravy.pravda.sk/regiony/rss/xml/`
* **Teraz Regióny:** `https://www.teraz.sk/rss/regiony.rss`
* **Topky Domáce:** `https://www.topky.sk/rss/10/Spravy_-_Domace.rss`
* **Aktuality:** `https://www.aktuality.sk/rss/`
* **Pravda Správy:** `https://spravy.pravda.sk/domace/rss/xml/`
* **SME Bratislava:** `https://bratislava.sme.sk/rss-title`

### Required Environment Secrets:
| Key | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot API token from @BotFather (e.g. `123456789:ABC...`) |
| `TELEGRAM_CHANNEL_ID` | Telegram Channel Username (e.g. `@bratislavanewss`) or Chat ID |
| `GEMINI_API_KEY` | Google AI Studio Key (e.g. `AIzaSy...`) |

---

## 4. Quota & Cost Math ($0/Month)

* **GitHub Actions:**
  - 36 runs/day $\times$ 15 seconds $\approx$ 270 minutes/month.
  - Free tier limit: 2,000 minutes/month ($\sim$13% used).
* **Gemini API:**
  - 36 runs/day $\times$ 2 requests = $\sim$72 requests/day.
  - Free tier limit: 1,500 requests/day ($< 5\%$ used).

---

## 5. How to Modify or Extend in the Future

### To Change Country / City:
1. Open `config.yaml`.
2. Update `country`, `target_city`, `source_language`, and `content_theme`.
3. Replace the `sources` list with RSS URLs for the new region.
4. Commit and push — all scrapers and AI prompts will automatically adapt.

### To Change Writing Tone or Language:
1. Open `config.yaml`.
2. Modify `output.target_language` (e.g. English, Ukrainian, Slovak).
3. Modify `output.style_guide` with new formatting rules.

### To Test Locally:
```bash
source .venv/bin/activate
# Test without posting or modifying state
python main.py --dry-run

# Run live once
python main.py
```

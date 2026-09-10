# 🌍 Bratislava News → Telegram AI Bot

An automated, configuration-driven pipeline that monitors news for **Bratislava**, uses **Google Gemini AI** to score and filter for wild, bizarre, or notable local stories, translates and stylizes them into Russian *«Труха»* style, and publishes them with photos to the Telegram channel [t.me/bratislavanewss](https://t.me/bratislavanewss).

Runs 100% autonomously 24/7 on **GitHub Actions ($0/month hosting)**.

> 📖 **For complete architectural documentation, prompt guidelines, and AI memory, see [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).**

---

## ✨ Features

- **Strictly Bratislava-Focused**: Only evaluates news happening in Bratislava and its boroughs. Stories from other cities are rejected.
- **Always With Images**: Multi-source image extraction (OpenGraph, Twitter Cards, JSON-LD) with direct binary photo upload to Telegram.
- **Zero Hallucination / Strictly Factual**: Real, verified news only (temperature 0.2, strict anti-rumor prompt).
- **Smart 2-Stage AI Evaluation**: Batch triage groups articles together to easily stay under Google AI Studio's 15 RPM limit (<5% of free tier used).
- **Cross-Source Deduplication**: Blocks reporting the same incident twice across different newspapers.
- **Viral Channel Branding**: Every post ends with `📢 Bratislava` directly linking back to your channel.
- **Autonomous Day/Night Schedule**: 
  - ☀️ **Day (08:00 – 00:00):** Every 30 minutes
  - 🌙 **Night (00:00 – 08:00):** Every 2 hours

---

## 📁 Project Structure

```text
.
├── PROJECT_CONTEXT.md        # Full architectural memory and future AI reference
├── config.yaml               # Country, city, feeds, AI thresholds, writing style, schedule settings
├── config.py                 # Configuration loader & validator
├── scraper.py                # Multi-layer RSS & webpage image scraper
├── ai_engine.py              # Gemini AI scoring, deduplication & copywriting
├── telegram_poster.py        # Telegram Bot API client with direct binary photo upload
├── state_manager.py          # seen_urls.json deduplication & auto-pruning
├── main.py                   # Main pipeline entrypoint (supports --dry-run)
├── seen_urls.json            # State file tracking processed articles
├── requirements.txt          # Python dependencies
├── .env.example              # Template for environment variables
└── .github/workflows/
    └── run.yml               # GitHub Actions scheduled runner (Day: 30m, Night: 2h)
```

---

## 🚀 Setup & Credentials Checklist

### 1. Telegram Setup
1. Open Telegram and message **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`, choose a name and a username (e.g. `svk_tabloid_bot`).
3. Save the **Bot Token** (`TELEGRAM_BOT_TOKEN`).
4. Create a Telegram Channel (public or private).
5. Add your bot as an **Administrator** in channel settings with permission to **Post Messages**.
6. Get your **Channel ID** (`TELEGRAM_CHANNEL_ID`):
   - For public channels: `@yourchannelname`
   - For private channels: post a message in the channel, visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and look for `"chat":{"id": -100xxxxxxxxxx}`.

### 2. Google Gemini API Key
1. Go to [Google AI Studio](https://aistudio.google.com/).
2. Click **Get API key** $\rightarrow$ **Create API key** (free tier, no credit card required).
3. Save this as `GEMINI_API_KEY`.

---

## 🛠 Local Testing

1. **Clone and install dependencies:**
   ```bash
   git clone <repo-url>
   cd SlovakNewsBot
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   Create a `.env` file from the template:
   ```bash
   cp .env.example .env
   ```
   Fill in your tokens in `.env`:
   ```ini
   TELEGRAM_BOT_TOKEN=123456789:ABC...
   TELEGRAM_CHANNEL_ID=@yourchannel
   GEMINI_API_KEY=AIzaSy...
   ```

3. **Run in Dry-Run Mode (Safe test, no posts sent):**
   ```bash
   python main.py --dry-run
   ```

4. **Run in Live Mode:**
   ```bash
   python main.py
   ```

---

## 🌐 Deploy to GitHub Actions (Free 24/7 Automation)

1. Push this repository to GitHub (public or private).
2. Go to **Settings $\rightarrow$ Secrets and variables $\rightarrow$ Actions $\rightarrow$ New repository secret**.
3. Add the following 3 secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHANNEL_ID`
   - `GEMINI_API_KEY`
4. The workflow in `.github/workflows/run.yml` will automatically run every **45 minutes** and commit state changes back to `seen_urls.json`.
5. You can also trigger a manual run anytime from the **Actions** tab $\rightarrow$ **News Ingestion & Telegram Bot** $\rightarrow$ **Run workflow**.

---

## 🔄 Adapting for Another Country

To adapt the bot for Poland, Czech Republic, Germany, or another region:
1. Open [config.yaml](file:///Users/andrii/Desktop/SlovakNewsBot/config.yaml).
2. Change `country`, `source_language`, and `content_theme`.
3. Update `output.target_language` and `style_guide`.
4. Replace the `sources` list with RSS feed URLs for your chosen country.
5. Commit and push the changes — no Python code changes needed!
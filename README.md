# 🌍 Universal News → Telegram AI Bot

An automated, configuration-driven pipeline that scrapes news feeds from **any country**, uses **Google Gemini AI** to score and filter for wild, bizarre, or tabloid news, translates and stylizes the stories (e.g., *«Труха»* style), and posts them to your Telegram channel.

Runs automatically on a schedule via **GitHub Actions ($0/month hosting)**.

---

## ✨ Features

- **100% Configurable & Universal**: Swap country, topic theme, target language, writing style, and RSS sources in `config.yaml` without changing any code.
- **Smart 2-Stage AI Evaluation**: Evaluates articles in batches to easily stay under Google AI Studio's free-tier rate limits (15 RPM).
- **Cross-Source Deduplication**: Avoids posting the exact same event multiple times if multiple outlets report it.
- **Telegram Formatting Resilience**: Uses clean HTML mode with automatic fallback: `sendPhoto` with caption $\rightarrow$ `sendMessage` fallback $\rightarrow$ plain text recovery.
- **Auto-pruning State**: Prevents repository bloat by pruning `seen_urls.json` entries older than 14 days.

---

## 📁 Project Structure

```text
.
├── config.yaml               # Country, language, vibe, feeds & settings
├── config.py                 # Configuration loader & validator
├── scraper.py                # Universal RSS scraper & image extractor
├── ai_engine.py              # Gemini AI scoring, deduplication & copywriting
├── telegram_poster.py        # Telegram Bot API client with resilience fallbacks
├── state_manager.py          # seen_urls.json deduplication & pruning
├── main.py                   # Main pipeline entrypoint (supports --dry-run)
├── seen_urls.json            # State file tracking processed articles
├── requirements.txt          # Python dependencies
├── .env.example              # Template for environment variables
└── .github/workflows/
    └── run.yml               # GitHub Actions scheduled runner (runs every 45 min)
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
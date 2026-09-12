# 🤖 Universal AI News Bot (Telegram + Google Gemini)

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Google Gemini](https://img.shields.io/badge/AI-Google%20Gemini-4285F4?logo=google&logoColor=white)](https://aistudio.google.com/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![GitHub Actions](https://img.shields.io/badge/Automation-GitHub%20Actions%2024%2F7-2088FF?logo=github-actions&logoColor=white)](https://github.com/features/actions)
[![Cost](https://img.shields.io/badge/Cost-%240%20%2F%20month-success)](#-cost--quota-breakdown-0month)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An **autonomous, configuration-driven news aggregator, AI filter, and copywriter** that continuously monitors RSS feeds, evaluates stories for local relevance and news value using **Google Gemini AI**, generates punchy stylized posts in your target language, and publishes them with verified high-resolution photos to a Telegram channel.

Runs **100% autonomously 24/7 in GitHub Actions cloud** at **$0/month hosting cost**. No VPS, no Docker container, no local machine, and no credit card required.

---

## 🌟 Live Demo

* **Channel:** [t.me/bratislavanewss](https://t.me/bratislavanewss) (`@bratislavanewss`)
* **Topic:** Bratislava, Slovakia local news, city life, and notable incidents
* **Language:** Russian (punchy, engaging, strictly factual)

---

## ⚡️ Key Features

- **🌐 100% Universal & Config-Driven**: Adapts to **any city, country, or language** simply by modifying [`config.yaml`](config.yaml) — zero Python code changes required.
- **🧠 Intelligent Gemini AI Triage**:
  - **Batch Evaluation**: Evaluates up to 10 articles per prompt, staying well under the 15 RPM free tier limit.
  - **Location Enforcement**: Rejects stories that did not occur in your target metropolitan area or boroughs.
  - **Boring Content Filter**: Filters out dry bureaucratic notices, routine statistical tables, and procurement tenders.
  - **Strict Factual Accuracy**: Generates engaging captions (temperature `0.2`) based 100% on source facts — zero hallucinations.
- **📸 Multi-Layer Image Scraping & Direct Upload**:
  - Extracts verified high-resolution lead images via OpenGraph, Twitter Cards, Schema.org JSON-LD, and `<figure>` tags.
  - Downloads images and uploads them as binary multipart form-data to Telegram, bypassing anti-bot CDN blocks.
  - Strict photo policy: refuses to fall back to plain text if an image cannot be verified.
- **🛡 Anti-Duplicate Memory**:
  - Semantic deduplication: prevents reposting the same event covered by different rival media outlets.
  - Persistent state in `seen_urls.json` automatically committed back to your repo.
  - Automatic 14-day history pruning keeps repository size tiny.
- **⏳ Smart Candidate Queue**:
  - Unposted qualified articles remain queued for subsequent runs, ensuring a reliable, continuous flow of news throughout the day even during quiet news cycles.
- **⏰ Autonomous Cloud Schedule**:
  - ☀️ **Day Hours (08:00 – 00:00 local time):** Runs every 30 minutes.
  - 🌙 **Night Hours (00:00 – 08:00 local time):** Runs every 2 hours.

---

## 🏗 System Architecture

```text
 ┌────────────────────────────────────────────────────────┐
 │                    RSS Feed Sources                    │
 │    (Regional News, City Portals, Major Daily Feeds)    │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │                 News Scraper & Filter                  │
 │   • Deduplication check against seen_urls.json         │
 │   • Target city / borough keyword pre-filtering        │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │                   Google Gemini AI                     │
 │   • Batch scoring (0-10) against content theme         │
 │   • Cross-outlet duplicate event detection             │
 │   • Filters out dry bureaucracy and off-target stories │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │              Media Extraction Engine                   │
 │   • Multi-source scrape (OpenGraph, Twitter, JSON-LD)  │
 │   • Pre-flight HTTP verification (valid image > 3KB)   │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │                  AI Copywriter                         │
 │   • Rewrites article in target language and tone       │
 │   • HTML formatting & custom channel branding footer   │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │                 Telegram Bot API                       │
 │   • Multipart binary photo upload (bypasses CDNs)      │
 │   • State recorded & committed back to GitHub          │
 └────────────────────────────────────────────────────────┘
```

---

## 🚀 3-Minute Quick Start Guide

You can launch your own autonomous channel in under 5 minutes without writing any code:

### 1. Fork this Repository
Click the **Fork** button at the top right of this page to create your own copy on GitHub.

### 2. Create a Telegram Bot & Channel
1. Open Telegram and message **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`, choose a display name and username (e.g. `my_city_news_bot`).
3. Save the **HTTP API Token** provided by BotFather (`TELEGRAM_BOT_TOKEN`).
4. Create a Telegram Channel (Public or Private).
5. Open your Channel Settings $\rightarrow$ **Administrators** $\rightarrow$ **Add Administrator** $\rightarrow$ select your bot and grant **Post Messages** permission.
6. Get your **Channel ID** (`TELEGRAM_CHANNEL_ID`):
   - For public channels: `@yourchannelusername`
   - For private channels: forward a channel message to [@userinfobot](https://t.me/userinfobot) or check `https://api.telegram.org/bot<TOKEN>/getUpdates` to get the `-100xxxxxxxxxx` ID.

### 3. Get a Free Google Gemini API Key
1. Go to [Google AI Studio](https://aistudio.google.com/).
2. Sign in with any Google account and click **Get API Key** $\rightarrow$ **Create API Key**.
3. Copy the key (`GEMINI_API_KEY`). It is completely free and requires no credit card.

### 4. Add Secrets to your GitHub Repository
1. In your forked GitHub repository, go to **Settings** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions**.
2. Click **New repository secret** and add the following 3 secrets:

| Secret Name | Example Value | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `123456789:ABCdefGHIjklMNOpqrs` | Bot token from @BotFather |
| `TELEGRAM_CHANNEL_ID` | `@mycitynews` or `-1001234567890` | Username or chat ID of your channel |
| `GEMINI_API_KEY` | `AIzaSy...` | API key from Google AI Studio |

### 5. Enable GitHub Actions
1. Go to the **Actions** tab in your repository.
2. If prompted, click **I understand my workflows, go ahead and enable them**.
3. Select **News Ingestion & Telegram Bot** in the left sidebar and click **Run workflow** to test it immediately!

The bot is now fully operational and will run on its automatic schedule without your laptop or computer needing to be turned on!

---

## ⚙️ Customization: Adapting to Any City or Country

To adapt the bot to a different region or topic, simply edit [`config.yaml`](config.yaml):

```yaml
# 1. Target Region and Topic
country: "Czech Republic"
target_city: "Prague"
source_language: "Czech"
content_theme: "interesting local news, city life, transport, events, accidents, and happenings in Prague"
min_score: 3

# 2. Output and Tone
output:
  target_language: "English"
  style_guide: >
    Punchy, engaging city news style.
    Start with an emoji and a bold headline.
    Follow with 2-3 informative sentences summarizing what happened.
    Include the exact Prague district (Prague 1, Žižkov, Vinohrady, etc.).
  channel_name: "Prague Daily"
  channel_link: "https://t.me/praguedaily"

# 3. RSS Feed Sources
sources:
  - name: "Pražský Patriot"
    rss_url: "https://www.prazskypatriot.cz/feed/"
    enabled: true
  - name: "iDNES Praha"
    rss_url: "https://servis.idnes.cz/rss.aspx?c=praha"
    enabled: true
```

Commit and push your changes to `config.yaml` — the pipeline immediately adapts to your new city and language!

---

## 💻 Local Development & Testing

If you want to run or test the bot on your local machine:

```bash
# 1. Clone your repository
git clone https://github.com/<your-username>/SlovakNewsBot.git
cd SlovakNewsBot

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up local environment variables
cp .env.example .env
# Edit .env and paste your TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, and GEMINI_API_KEY

# 5. Run a safe dry-run (prints post previews to terminal, sends nothing to Telegram)
python main.py --dry-run

# 6. Run live (publishes the top story to your channel)
python main.py
```

---

## 💰 Cost & Quota Breakdown ($0/Month)

This project is engineered from the ground up to operate within the 100% free tiers of all providers:

| Resource | Usage by Bot | Free Tier Allowance | Percentage Used |
|---|---|---|---|
| **GitHub Actions** | ~36 runs/day × 15 sec ≈ **270 min/month** | 2,000 min/month | **~13%** |
| **Gemini API** | ~36 runs/day × 2 calls ≈ **72 requests/day** | 1,500 requests/day | **~4.8%** |
| **Telegram Bot API** | ~20-30 messages/day | Unlimited | **0%** |
| **Server Hosting** | Serverless via GitHub Actions | $0 | **100% Free** |

---

## 🔒 Security & Privacy

- **No Secrets in Code:** Sensitive credentials (`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`) are **never** committed to the repository.
- **Local Isolation:** The `.env` file is excluded in [`.gitignore`](.gitignore) and remains strictly on your local computer.
- **Cloud Security:** When deployed to GitHub Actions, credentials are securely injected at runtime via encrypted **GitHub Secrets**.
- **Clean State:** The only file modified and committed by GitHub Actions is [`seen_urls.json`](seen_urls.json), which stores only public news URLs, headlines, and publication timestamps to prevent duplicate posts.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more details.
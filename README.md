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
* **Language:** Ukrainian (informative, clear, professional, strictly factual)

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

## 📋 Required Stuff (Prerequisites Checklist)

Before you launch the bot, make sure you have the following **3 free items**:

| # | Required Item | Where to Get It | Cost | What It Is For |
|:---:|---|---|:---:|---|
| **1** | **Telegram Bot Token** | [@BotFather](https://t.me/BotFather) on Telegram | **$0** | Allows the bot to publish messages to your channel |
| **2** | **Telegram Channel ID** | Your Telegram Channel | **$0** | The destination channel where posts will be published |
| **3** | **Google Gemini API Key** | [Google AI Studio](https://aistudio.google.com/) | **$0** | Powers AI scoring, deduplication, translation, and copywriting |

> [!NOTE]
> **No Paid Services Required:** You do **not** need a paid server, VPS, Docker hosting, or a credit card. The bot runs entirely on GitHub's free Actions tier and Google's free Gemini API tier.

### 🔑 Detailed Step-by-Step for Required Credentials:

#### 1. Telegram Bot Token (`TELEGRAM_BOT_TOKEN`)
1. Open Telegram and search for the official **[@BotFather](https://t.me/BotFather)** bot.
2. Send the command: `/newbot`.
3. Enter a friendly display name (e.g., `Bratislava Daily News`).
4. Enter a unique username ending in `bot` (e.g., `bratislava_news_feed_bot`).
5. BotFather will send you a message containing your **HTTP API Token** (format: `123456789:ABCdefGHIjklMNOpqrs-TUVWXYZ`).
6. Copy and save this token.

#### 2. Telegram Channel & Channel ID (`TELEGRAM_CHANNEL_ID`)
1. Create a new Telegram Channel (either **Public** or **Private**).
2. Open your channel profile $\rightarrow$ **Manage Channel** $\rightarrow$ **Administrators** $\rightarrow$ **Add Administrator**.
3. Search for your newly created bot username and add it.
4. **Crucial Permission:** Ensure the bot has the **Post Messages** permission enabled (all other permissions can be left off).
5. Identify your `TELEGRAM_CHANNEL_ID`:
   - **For Public Channels:** Your ID is simply `@yourchannelname` (e.g., `@bratislavanewss`).
   - **For Private Channels:** Post any test message in the channel, forward it to [@userinfobot](https://t.me/userinfobot), or visit `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` in your browser. Look for `"chat":{"id": -100xxxxxxxxxx}`. Your ID is the full number starting with `-100`.

#### 3. Google Gemini API Key (`GEMINI_API_KEY`)
1. Visit [Google AI Studio](https://aistudio.google.com/).
2. Sign in with your regular Google account.
3. Click the blue **Get API Key** button in the top left, then click **Create API Key**.
4. Select or create a free project and copy your API key (format: `AIzaSy...`).
5. *No billing account or credit card is required — Gemini's free tier provides 1,500 requests/day, whereas this bot only uses ~70 requests/day.*

---

## 📦 Project Stuff Explained (File-by-File Guide)

Here is a plain-English breakdown of every file in this repository and what it does:

| File | What It Does | Why It Matters |
|---|---|---|
| [`config.yaml`](config.yaml) | **The Central Control Panel** | Defines your target city, content theme, scoring threshold, RSS feed URLs, language, and posting style. **This is the only file you need to edit to customize the bot.** |
| [`config.py`](config.py) | **Configuration Loader** | Reads `config.yaml`, validates settings, and loads environment variables/secrets. |
| [`main.py`](main.py) | **The Pipeline Orchestrator** | Ties the whole system together: triggers the scraper, calls the AI evaluation, manages the candidate queue, dispatches to Telegram, and records state. Supports `--dry-run` for safe local testing. |
| [`scraper.py`](scraper.py) | **The News & Media Hunter** | Downloads and parses RSS feeds. Features a multi-layer image extraction engine (OpenGraph, Twitter Cards, Schema.org JSON-LD, lead figures) with pre-flight HTTP verification to guarantee valid photos. |
| [`ai_engine.py`](ai_engine.py) | **The AI Editor & Copywriter** | Connects to Google Gemini API. Handles batch article triage, cross-outlet duplicate detection, city location filtering, and writes engaging HTML captions in your chosen target language. |
| [`telegram_poster.py`](telegram_poster.py) | **The Post Dispatcher** | Communicates with the Telegram Bot API. Downloads verified image bytes and uploads them via binary `multipart/form-data` to guarantee CDN blocks never prevent photos from posting. Enforces strict photo-only policy. |
| [`state_manager.py`](state_manager.py) | **The Memory Manager** | Prevents duplicate posts. Manages `seen_urls.json`, keeps track of recent story headlines for semantic deduplication, and automatically prunes history older than 14 days to keep the repo clean. |
| [`seen_urls.json`](seen_urls.json) | **The Persistent Database** | A lightweight JSON database committed automatically by GitHub Actions. Ensures the bot never posts the same story twice even across serverless runs. |
| [`.github/workflows/run.yml`](.github/workflows/run.yml) | **The 24/7 Cloud Automation** | The GitHub Actions workflow that executes `python main.py` on a dual schedule (day: every 30m, night: every 2h), commits state back to git, and includes retry logic for cloud synchronization. |
| [`requirements.txt`](requirements.txt) | **Python Dependencies** | Lists the required lightweight libraries (`requests`, `feedparser`, `beautifulsoup4`, `pyyaml`, `python-dotenv`). |
| [`.env.example`](.env.example) | **Environment Template** | A sample file showing the 3 environment variables needed when developing or testing on your local machine. |
| [`.gitignore`](.gitignore) | **Security Shield** | Ensures local `.env` files, virtual environments, and temporary files are **never** accidentally committed to git. |

---

## 🚀 3-Minute Quick Start Guide

You can launch your own autonomous channel in under 5 minutes without writing any code:

### 1. Fork this Repository
Click the **Fork** button at the top right of this page to create your own copy on GitHub.

### 2. Add Secrets to your GitHub Repository
In your forked GitHub repository, go to **Settings** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions** $\rightarrow$ click **New repository secret** and add the 3 required secrets:

1. `TELEGRAM_BOT_TOKEN` — Your token from @BotFather
2. `TELEGRAM_CHANNEL_ID` — Your channel username (e.g., `@mycitynews`) or `-100...` ID
3. `GEMINI_API_KEY` — Your key from Google AI Studio

### 3. Enable GitHub Actions
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

### 🎛 Configuration Knobs Explained

Every behavior of the bot can be tuned in [`config.yaml`](config.yaml) without writing code:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `country` | string | `"Slovakia"` | Target country for the news pipeline. |
| `target_city` | string | `"Bratislava"` | Primary city. AI strictly rejects articles occurring outside this metropolitan area. |
| `content_theme` | string | *See config.yaml* | Instructions given to Gemini defining what topics and stories qualify. |
| `min_score` | integer (0-10) | `3` | Score threshold to post: `0` = off-target, `1-2` = boring bureaucracy, `3-5` = standard city news, `6-8` = notable incidents/crime, `9-10` = sensational/viral. |
| `output.target_language` | string | `"Ukrainian"` | Output language for Telegram posts (e.g. `Ukrainian`, `English`, `Slovak`, `German`). |
| `output.style_guide` | text | *See config.yaml* | Tone of voice and formatting rules (informative, clear, max 1 emoji, clean HTML). |
| `output.include_source_link` | boolean | `false` | If `true`, adds source link to footer. If `false`, keeps traffic inside your channel. |
| `output.channel_name` | string | `"Братислава"` | Text displayed for your channel branding link. |
| `output.channel_link` | string | `"https://t.me/..."` | Clickable channel link attached to the footer of every post. |
| `ai.model` | string | `"gemini-3.5-flash-lite"` | Primary Gemini model. Automatically falls back to other Flash models if needed. |
| `ai.temperature` | float | `0.2` | Low temperature (0.1–0.3) guarantees strictly factual content and eliminates hallucinations. |
| `ai.batch_size` | integer | `10` | Number of articles evaluated together per prompt to minimize API calls. |
| `settings.max_articles_per_run` | integer | `30` | Maximum candidate articles to fetch and evaluate per run. |
| `settings.max_posts_per_run` | integer | `1` | Pacing limit: publishes 1 story per run (e.g. 1 post every 30 minutes). Remaining qualified stories stay queued. |
| `settings.require_image` | boolean | `true` | If `true`, strictly requires a verified lead photo (refuses to fall back to plain text). |
| `settings.min_image_width` | integer | `500` | Minimum image width in pixels for high quality posts. |
| `settings.min_image_height` | integer | `300` | Minimum image height in pixels for high quality posts. |
| `settings.history_retention_days` | integer | `14` | Records older than this are pruned from `seen_urls.json` to keep repo size tiny. |

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
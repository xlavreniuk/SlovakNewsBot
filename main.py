import argparse
import logging
import os
import sys
import time
from typing import List

from config import AppConfig, load_config
from scraper import Article, NewsScraper
from state_manager import StateManager
from ai_engine import AIEngine, ScoredArticle
from telegram_poster import TelegramPoster

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("news_bot")


BRATISLAVA_KEYWORDS = [
    'bratislav', 'petržal', 'ružinov', 'staré mesto', 'stare mesto',
    'starom meste', 'starého mesta', 'nové mesto', 'nove mesto',
    'novom meste', 'nového mesta', 'karlov', 'dúbravk', 'dubravk',
    'rača', 'rači', 'račiansk', 'devín', 'devin', 'lamač', 'lamac',
    'vajnory', 'vajnor', 'vrakuň', 'vrakun', 'biskupic', 'jarovc',
    'rusovc', 'čunov', 'cunov', 'záhorsk', 'zahorsk', 'dunaj',
    'most snp', 'prístavn', 'pristavn', 'apollo', 'sitina', 'kamzík',
    'kamzik', 'koliba', 'trnavsk', 'patrónk', 'patronk', 'mlynsk',
    'hodžov', 'hodzov', 'obchodná', 'obchodna', 'poštová', 'postov',
    'pezinok', 'pezinsk', 'senec', 'seneck', 'malack'
]


def parse_args():
    parser = argparse.ArgumentParser(description="Universal News -> Telegram AI Bot")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("CONFIG_FILE", "config.yaml"),
        help="Path to configuration YAML file (default: config.yaml)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run scraper and AI without posting to Telegram or updating state file"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging output"
    )
    return parser.parse_args()


def run_pipeline(config: AppConfig, dry_run: bool = False) -> None:
    logger.info("Starting News Bot pipeline for country: %s", config.country)
    logger.info("Content theme: '%s'", config.content_theme)
    logger.info("Min score threshold: %d / 10", config.min_score)
    if dry_run:
        logger.info("Running in DRY-RUN mode (no posts will be sent, state won't be saved).")

    if not dry_run:
        config.validate_secrets(dry_run=False)

    # 1. Initialize State Manager
    state = StateManager(
        filepath="seen_urls.json",
        retention_days=config.settings.history_retention_days
    )

    # 2. Fetch new articles from sources
    scraper = NewsScraper(timeout=config.settings.request_timeout_seconds)
    new_candidates: List[Article] = []
    skipped_non_target_urls: List[str] = []

    enabled_sources = [s for s in config.sources if s.enabled]
    logger.info("Scanning %d enabled sources...", len(enabled_sources))

    for source in enabled_sources:
        is_dedicated = (
            "bratislav" in source.name.lower() or
            "bratislav" in source.rss_url.lower()
        )
        articles = scraper.fetch_articles_from_source(source.name, source.rss_url)
        for art in articles:
            if state.is_seen(art.url):
                continue

            text_to_check = f"{art.title} {art.summary}".lower()
            is_relevant = is_dedicated or any(kw in text_to_check for kw in BRATISLAVA_KEYWORDS)

            if is_relevant:
                new_candidates.append(art)
                if len(new_candidates) >= config.settings.max_articles_per_run:
                    logger.info("Reached maximum candidate articles limit (%d).", config.settings.max_articles_per_run)
                    break
            else:
                # Mark non-target articles so future runs do not re-scan them
                skipped_non_target_urls.append(art.url)

        if len(new_candidates) >= config.settings.max_articles_per_run:
            break

    logger.info(
        "Discovered %d relevant Bratislava candidates (%d skipped as non-target).",
        len(new_candidates),
        len(skipped_non_target_urls)
    )
    if not new_candidates:
        logger.info("No new Bratislava articles to process. Everything up to date!")
        if not dry_run:
            for u in skipped_non_target_urls:
                state.mark_seen(u)
            state.prune()
            state.save()
        return

    # 3. AI Evaluation & Scoring
    recent_titles = state.get_recent_posted_titles(hours=48)
    qualified: List[ScoredArticle] = []
    rejected_urls: List[str] = []

    if config.gemini_api_key:
        ai_engine = AIEngine(config)
        qualified, rejected_urls = ai_engine.evaluate_batch(new_candidates, recent_titles)
    else:
        if dry_run:
            logger.warning("GEMINI_API_KEY not set. Running mock evaluation for demonstration in dry-run.")
            for i, art in enumerate(new_candidates[:3]):
                qualified.append(ScoredArticle(article=art, score=8, reason="Demo dry-run mode"))
        else:
            raise ValueError("GEMINI_API_KEY must be provided to run live evaluation.")

    # 4. Enrich and verify images for qualified articles
    no_image_urls: List[str] = []
    if config.settings.require_image:
        valid_qualified: List[ScoredArticle] = []
        for q in qualified:
            scraper.enrich_image(
                q.article,
                min_width=config.settings.min_image_width,
                min_height=config.settings.min_image_height
            )
            if q.article.image_url:
                valid_qualified.append(q)
            else:
                logger.info("Filtered qualified article lacking verified image: '%s'", q.article.title)
                no_image_urls.append(q.article.url)
        qualified = valid_qualified

    logger.info("%d articles ready to post (score >= %d with images)", len(qualified), config.min_score)

    # 5. Limit to max posts per run
    selected = qualified[:config.settings.max_posts_per_run]

    # 6. Generate Styled Posts & Dispatch
    poster = None
    if not dry_run:
        config.validate_secrets(dry_run=False)
        poster = TelegramPoster(
            bot_token=config.telegram_bot_token,
            channel_id=config.telegram_channel_id,
            timeout=config.settings.request_timeout_seconds
        )

    for item in selected:
        art = item.article
        logger.info("Processing winning story [score=%d]: '%s' (%s)", item.score, art.title, art.source_name)

        if config.gemini_api_key:
            post = ai_engine.generate_styled_post(art)
        else:
            # Fallback format for mock dry-run (Ukrainian, 1 emoji max, clean link)
            post_caption = (
                f"<b>📍 {art.title}</b>\n\n"
                f"{art.summary}\n\n"
                f'<a href="{config.output.channel_link}">{config.output.channel_name}</a>'
            )
            from ai_engine import GeneratedPost
            post = GeneratedPost(article=art, headline=art.title, caption=post_caption)

        if not post:
            continue

        if dry_run:
            print("\n" + "=" * 60)
            print(f"DRY-RUN POST PREVIEW (Score: {item.score}/10 | Source: {art.source_name})")
            print(f"Image URL: {art.image_url or 'None'}")
            print("-" * 60)
            print(post.caption)
            print("=" * 60 + "\n")
        else:
            success = poster.post(
                caption=post.caption,
                image_url=art.image_url,
                require_image=config.settings.require_image
            )
            if success:
                state.record_posted_story(art.url, art.title)
                logger.info("Successfully posted to channel: '%s'", art.title)
                time.sleep(config.settings.post_delay_seconds)
            else:
                logger.error("Failed to post story to channel: '%s'", art.title)

    # 7. State management: Mark seen only for posted, rejected, and non-target articles.
    # Unposted qualified articles remain unseen so they are posted in subsequent runs.
    if not dry_run:
        for u in rejected_urls:
            state.mark_seen(u)
        for u in no_image_urls:
            state.mark_seen(u)
        for u in skipped_non_target_urls:
            state.mark_seen(u)
        state.save()

    logger.info("Pipeline execution completed successfully.")


def main():
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        cfg = load_config(args.config)
        run_pipeline(cfg, dry_run=args.dry_run)
    except Exception as e:
        logger.error("Pipeline failed with error: %s", e, exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()

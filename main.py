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

    # 2. Fetch new articles from sources with fair-share distribution and age filtering
    scraper = NewsScraper(timeout=config.settings.request_timeout_seconds)
    new_candidates: List[Article] = []
    skipped_non_target_urls: List[str] = []
    skipped_stale_urls: List[str] = []

    enabled_sources = [s for s in config.sources if s.enabled]
    logger.info("Scanning %d enabled sources...", len(enabled_sources))

    now_ts = time.time()
    max_age_seconds = config.settings.max_article_age_hours * 3600.0
    # Allow balanced representation from each source
    max_per_source = max(6, config.settings.max_articles_per_run // max(len(enabled_sources), 1) + 2)

    for source in enabled_sources:
        is_dedicated = (
            "bratislav" in source.name.lower() or
            "bratislav" in source.rss_url.lower()
        )
        articles = scraper.fetch_articles_from_source(source.name, source.rss_url)
        source_candidates = 0

        for art in articles:
            if state.is_seen(art.url):
                continue

            # Check article freshness: filter out stale articles
            if art.published_timestamp is not None:
                age_sec = now_ts - art.published_timestamp
                if age_sec > max_age_seconds:
                    logger.debug(
                        "Skipping stale article from %s (%.1f hours old > %d max): '%s'",
                        source.name, age_sec / 3600.0, config.settings.max_article_age_hours, art.title
                    )
                    skipped_stale_urls.append(art.url)
                    continue

            text_to_check = f"{art.title} {art.summary}".lower()
            is_relevant = is_dedicated or any(kw in text_to_check for kw in BRATISLAVA_KEYWORDS)

            if is_relevant:
                new_candidates.append(art)
                source_candidates += 1
                if source_candidates >= max_per_source:
                    logger.debug("Reached cap of %d candidate articles from %s.", max_per_source, source.name)
                    break
            else:
                # Mark non-target articles so future runs do not re-scan them
                skipped_non_target_urls.append(art.url)

    # Sort all candidates newest first (prioritize fresh breaking news)
    new_candidates.sort(key=lambda a: a.published_timestamp or 0, reverse=True)

    if len(new_candidates) > config.settings.max_articles_per_run:
        new_candidates = new_candidates[:config.settings.max_articles_per_run]

    logger.info(
        "Discovered %d fresh Bratislava candidates (%d skipped as non-target, %d skipped as stale).",
        len(new_candidates),
        len(skipped_non_target_urls),
        len(skipped_stale_urls)
    )
    if not new_candidates:
        logger.info("No new Bratislava articles to process. Everything up to date!")
        if not dry_run:
            for u in skipped_non_target_urls:
                state.mark_seen(u)
            for u in skipped_stale_urls:
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
        try:
            qualified, rejected_urls = ai_engine.evaluate_batch(new_candidates, recent_titles)
        except Exception as e:
            logger.warning(
                "AI evaluation temporarily unavailable (%s). "
                "Gracefully deferring %d candidate stories to next scheduled run.",
                e, len(new_candidates)
            )
            # Exit pipeline cleanly without marking candidates as seen
            return
    else:
        if dry_run:
            logger.warning("GEMINI_API_KEY not set. Running mock evaluation for demonstration in dry-run.")
            for i, art in enumerate(new_candidates[:3]):
                qualified.append(ScoredArticle(article=art, score=8, reason="Demo dry-run mode"))
        else:
            raise ValueError("GEMINI_API_KEY must be provided to run live evaluation.")

    # 4. Enrich full text, check paywalls, and verify images for qualified articles
    no_image_urls: List[str] = []
    skipped_paywall_urls: List[str] = []
    skipped_thin_urls: List[str] = []
    valid_qualified: List[ScoredArticle] = []

    for q in qualified:
        scraper.enrich_article(
            q.article,
            min_width=config.settings.min_image_width,
            min_height=config.settings.min_image_height,
            max_images=config.settings.max_images_per_post,
            is_seen_func=state.is_image_seen
        )

        # Filter out paywalled articles (prevent hallucinations from locked stubs)
        if q.article.is_paywalled:
            logger.info("Filtered qualified article as paywalled: '%s'", q.article.title)
            skipped_paywall_urls.append(q.article.url)
            continue

        # Filter out thin/empty content (require at least 80 chars of factual substance)
        content_len = len((q.article.full_text or q.article.summary or "").strip())
        if content_len < 80:
            logger.info("Filtered qualified article as thin/insufficient text (%d chars): '%s'", content_len, q.article.title)
            skipped_thin_urls.append(q.article.url)
            continue

        # Re-check freshness if timestamp was enriched from webpage metadata
        if q.article.published_timestamp is not None:
            age_sec = now_ts - q.article.published_timestamp
            if age_sec > max_age_seconds:
                logger.info("Filtered qualified article as stale (%.1f hours old): '%s'", age_sec / 3600.0, q.article.title)
                skipped_stale_urls.append(q.article.url)
                continue

        if config.settings.require_image:
            if q.article.image_url and not state.is_image_seen(q.article.image_url, q.article.image_hash):
                valid_qualified.append(q)
            else:
                logger.info("Filtered qualified article lacking verified unseen image: '%s'", q.article.title)
                no_image_urls.append(q.article.url)
        else:
            valid_qualified.append(q)

    # Freshness-weighted ranking: prioritize the latest breaking news published right now
    def story_freshness_priority(item: ScoredArticle) -> float:
        base = float(item.score)
        ts = item.article.published_timestamp
        if ts:
            age_hours = max(0.0, (now_ts - ts) / 3600.0)
            if age_hours <= 2.0:
                base += 2.5   # Massive bonus for breaking news in the last 2 hours
            elif age_hours <= 6.0:
                base += 1.5   # Bonus for fresh news in the last 6 hours
            elif age_hours <= 12.0:
                base += 0.5
            elif age_hours > 20.0:
                base -= 1.0   # Demote older day-old stories
        return base

    valid_qualified.sort(key=story_freshness_priority, reverse=True)
    qualified = valid_qualified
    logger.info("%d verified articles ranked by freshness & score ready to post (score >= %d, with text & images)", len(qualified), config.min_score)

    # 5. Initialize Telegram Poster
    poster = None
    if not dry_run:
        config.validate_secrets(dry_run=False)
        poster = TelegramPoster(
            bot_token=config.telegram_bot_token,
            channel_id=config.telegram_channel_id,
            timeout=config.settings.request_timeout_seconds
        )

    # 6. Generate Styled Posts & Dispatch (iterating until max_posts_per_run reached)
    posts_sent = 0
    for item in qualified:
        if posts_sent >= config.settings.max_posts_per_run:
            break

        art = item.article

        # In-batch deduplication: guarantee image has never been posted in prior runs or earlier in this run
        if state.is_image_seen(art.image_url, art.image_hash):
            logger.warning("Skipping '%s': image already used in channel!", art.title)
            continue

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
            logger.warning("Post generation or factual validation failed for '%s'. Trying next candidate.", art.title)
            continue

        if dry_run:
            print("\n" + "=" * 60)
            print(f"DRY-RUN POST PREVIEW (Score: {item.score}/10 | Source: {art.source_name})")
            print(f"Published: {art.published_at or 'Unknown'}")
            print(f"Images count: {len(art.image_urls)}")
            for idx, u in enumerate(art.image_urls):
                h = art.image_hashes[idx] if idx < len(art.image_hashes) else "N/A"
                print(f"  Photo #{idx + 1} (hash: {h}): {u}")
            print("-" * 60)
            print(post.caption)
            print("=" * 60 + "\n")
            posts_sent += 1
        else:
            success = poster.post(
                caption=post.caption,
                image_url=art.image_url,
                image_urls=art.image_urls,
                require_image=config.settings.require_image
            )
            if success:
                state.record_posted_story(art.url, art.title)
                for idx, img_u in enumerate(art.image_urls):
                    img_h = art.image_hashes[idx] if idx < len(art.image_hashes) else None
                    state.record_posted_image(img_u, img_h)
                state.save()  # Immediately persist so state is guaranteed saved
                logger.info("Successfully posted to channel: '%s'", art.title)
                posts_sent += 1
                time.sleep(config.settings.post_delay_seconds)
            else:
                logger.error("Failed to post story to channel: '%s'", art.title)

    # 7. State management: Mark seen for rejected, stale, paywalled, thin, and non-target articles.
    if not dry_run:
        for u in rejected_urls:
            state.mark_seen(u)
        for u in no_image_urls:
            state.mark_seen(u)
        for u in skipped_paywall_urls:
            state.mark_seen(u)
        for u in skipped_thin_urls:
            state.mark_seen(u)
        for u in skipped_non_target_urls:
            state.mark_seen(u)
        for u in skipped_stale_urls:
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
    except (ValueError, FileNotFoundError) as e:
        logger.error("Configuration/credential error: %s", e, exc_info=True)
        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error during pipeline run: %s", e, exc_info=True)
        # Avoid crashing automated cron workflows on transient network/API hiccups
        logger.info("Exiting cleanly to avoid failing scheduled GitHub Actions run.")
        sys.exit(0)


if __name__ == "__main__":
    main()

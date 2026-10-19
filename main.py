import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from config import AppConfig, load_config
from scraper import Article, NewsScraper
from state_manager import StateManager
from ai_engine import AIEngine, GeneratedPost, ScoredArticle
from telegram_poster import TelegramPoster

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("news_bot")

STATE_FILE = "seen_urls.json"

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

# Age assumed for stories whose source gives no publication time
UNKNOWN_AGE_HOURS = 3.0
# Breaking stories must be at most this old to skip the spacing between posts
BREAKING_MAX_AGE_HOURS = 3.0


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
    parser.add_argument(
        "--loop",
        type=float,
        default=0,
        metavar="MINUTES",
        help="Keep polling sources for this many minutes (interval: settings.poll_interval_seconds)"
    )
    parser.add_argument(
        "--push-state",
        action="store_true",
        default=os.environ.get("STATE_GIT_PUSH") == "1",
        help="Commit and push seen_urls.json after posts (used by the GitHub Actions loop)"
    )
    return parser.parse_args()


def register_failure(state: StateManager, art: Article, max_attempts: int) -> None:
    """Counts a failed attempt; after max_attempts the story is marked seen so it stops being retried every run."""
    attempts = state.record_failure(art.url)
    if attempts >= max_attempts:
        logger.warning("Giving up on '%s' after %d failed attempts.", art.title, attempts)
        state.mark_seen(art.url)
    else:
        logger.info("Will retry '%s' in a later run (attempt %d/%d).", art.title, attempts, max_attempts)


def push_state(message: str = "chore: update state [skip ci]") -> None:
    """Commits and pushes the state file so a crash or the next job never reposts a story."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], capture_output=True, text=True)

    git("add", STATE_FILE)
    if git("diff", "--staged", "--quiet").returncode == 0:
        return
    if git("commit", "-m", message).returncode != 0:
        logger.warning("State commit failed.")
        return
    for attempt in range(1, 4):
        if git("pull", "--rebase", "origin", "main").returncode == 0 and git("push", "origin", "HEAD:main").returncode == 0:
            logger.info("State pushed to repository.")
            return
        logger.warning("State push attempt %d failed, retrying...", attempt)
        time.sleep(5)
    logger.error("Could not push state after 3 attempts; it will be pushed with the next commit.")


def age_hours(art: Article, now_ts: float) -> Optional[float]:
    if art.published_timestamp is None:
        return None
    return max(0.0, (now_ts - art.published_timestamp) / 3600.0)


def is_quiet_hour(config: AppConfig) -> bool:
    """True inside settings.quiet_hours (e.g. "23-7", local time)."""
    spec = config.settings.quiet_hours.strip()
    if not spec or "-" not in spec:
        return False
    try:
        start, end = (int(x) for x in spec.split("-", 1))
    except ValueError:
        return False
    hour = datetime.now(ZoneInfo(config.timezone)).hour
    return start <= hour < end if start < end else (hour >= start or hour < end)


def collect_candidates(
    config: AppConfig,
    scraper: NewsScraper,
    state: StateManager,
    now_ts: float
) -> Tuple[List[Article], List[str], List[str]]:
    """Reads every enabled source and returns (fresh target-city candidates, non-target urls, stale urls)."""
    candidates: List[Article] = []
    skipped_non_target: List[str] = []
    skipped_stale: List[str] = []
    seen_this_run: Set[str] = set()

    enabled_sources = [s for s in config.sources if s.enabled]
    logger.info("Scanning %d enabled sources...", len(enabled_sources))
    max_age_hours = float(config.settings.max_article_age_hours)
    # Balanced representation from each source
    max_per_source = max(6, config.settings.max_articles_per_run // max(len(enabled_sources), 1) + 2)

    for source in enabled_sources:
        is_dedicated = source.dedicated or "bratislav" in source.name.lower()
        articles = scraper.fetch_articles(source.name, source.url, source.type)
        # Newest first, so the per-source cap keeps the freshest stories
        articles.sort(key=lambda a: a.published_timestamp or 0, reverse=True)
        source_candidates = 0

        for art in articles:
            # The same article often appears in several feeds of one outlet
            if art.url in seen_this_run or state.is_seen(art.url):
                continue
            seen_this_run.add(art.url)

            age = age_hours(art, now_ts)
            if age is not None and age > max_age_hours:
                skipped_stale.append(art.url)
                continue

            text_to_check = f"{art.title} {art.summary} {art.url}".lower()
            if not (is_dedicated or any(kw in text_to_check for kw in BRATISLAVA_KEYWORDS)):
                # Mark non-target articles so future runs do not re-scan them
                skipped_non_target.append(art.url)
                continue

            candidates.append(art)
            source_candidates += 1
            if source_candidates >= max_per_source:
                logger.debug("Reached cap of %d candidate articles from %s.", max_per_source, source.name)
                break

    candidates.sort(key=lambda a: a.published_timestamp or 0, reverse=True)
    return candidates, skipped_non_target, skipped_stale


def ranking_priority(item: ScoredArticle, now_ts: float) -> float:
    """Score with a steady freshness decay: every hour of age costs half a point."""
    age = age_hours(item.article, now_ts)
    return float(item.score) - 0.5 * (UNKNOWN_AGE_HOURS if age is None else age)


def is_breaking(item: ScoredArticle, config: AppConfig, now_ts: float) -> bool:
    age = age_hours(item.article, now_ts)
    return item.score >= config.settings.breaking_score and age is not None and age <= BREAKING_MAX_AGE_HOURS


def select_photos(
    art: Article,
    config: AppConfig,
    scraper: NewsScraper,
    ai_engine: Optional[AIEngine],
    state: StateManager
) -> bool:
    """
    Picks the photo(s) for a story. With the vision check on, only clean (no outlet watermark/logo, no burned-in
    text), relevant real photos pass. Returns False when no acceptable photo exists.
    """
    if not art.image_url:
        return False
    if not (ai_engine and config.ai.vision_check):
        return not state.is_image_seen(art.image_url, art.image_hash)

    approved = ai_engine.review_images(art, scraper.thumbnails)
    if approved is None:
        # Vision model unreachable: fall back to the best photo that passed the URL watermark filter
        logger.warning("Using unreviewed photo for '%s' (vision check unavailable).", art.title[:40])
        return not state.is_image_seen(art.image_url, art.image_hash)

    approved = [c for c in approved if not state.is_image_seen(c["url"], c["hash"])]
    if not approved:
        return False

    leads = [c for c in approved if not c["gallery"]] or approved
    lead = leads[0]
    extras = [c for c in approved if c["gallery"] and c["url"] != lead["url"]]
    chosen = [lead] + extras[:max(config.settings.max_images_per_post - 1, 0)]

    art.image_url = lead["url"]
    art.image_hash = lead["hash"]
    art.image_urls = [c["url"] for c in chosen]
    art.image_hashes = [c["hash"] for c in chosen]
    logger.info(
        "Photo approved (relevance=%d, quality=%d, %dx%d): %s",
        lead["relevance"], lead["quality"], lead["width"], lead["height"], lead["url"]
    )
    return True


def run_pipeline(config: AppConfig, dry_run: bool = False, scraper: Optional[NewsScraper] = None) -> int:
    """Runs one check of all sources. Returns the number of stories posted (or previewed in dry-run)."""
    logger.info("Starting News Bot pipeline (min score %d/10, max age %dh).", config.min_score, config.settings.max_article_age_hours)
    if dry_run:
        logger.info("Running in DRY-RUN mode (no posts will be sent, state won't be saved).")
    else:
        config.validate_secrets(dry_run=False)

    state = StateManager(filepath=STATE_FILE, retention_days=config.settings.history_retention_days)
    scraper = scraper or NewsScraper(timeout=config.settings.request_timeout_seconds)
    scraper.reset_image_caches()
    now_ts = time.time()

    # 1. Collect fresh target-city candidates from all sources
    candidates, skipped_non_target_urls, skipped_stale_urls = collect_candidates(config, scraper, state, now_ts)

    # 2. Score only stories the AI has not seen yet; reuse cached scores for the rest
    qualified: List[ScoredArticle] = []
    rejected_urls: List[str] = []
    new_candidates: List[Article] = []
    for art in candidates:
        cached = state.get_evaluation(art.url)
        if cached is None:
            new_candidates.append(art)
        elif cached["score"] >= config.min_score:
            qualified.append(ScoredArticle(article=art, score=cached["score"], reason=cached.get("reason", "")))
    new_candidates = new_candidates[:config.settings.max_articles_per_run]

    logger.info(
        "Found %d fresh Bratislava candidates (%d need scoring; %d skipped as non-target, %d as stale).",
        len(candidates), len(new_candidates), len(skipped_non_target_urls), len(skipped_stale_urls)
    )

    ai_engine: Optional[AIEngine] = AIEngine(config) if config.gemini_api_key else None
    recent_titles = state.get_recent_posted_titles(hours=48)

    if new_candidates:
        if ai_engine:
            try:
                newly_qualified, rejected_urls = ai_engine.evaluate_batch(new_candidates, recent_titles)
            except Exception as e:
                logger.warning("AI evaluation temporarily unavailable (%s). Deferring %d new stories.", e, len(new_candidates))
                newly_qualified = []
            for q in newly_qualified:
                state.record_evaluation(q.article.url, q.score, q.reason)
            qualified.extend(newly_qualified)
        elif dry_run:
            logger.warning("GEMINI_API_KEY not set. Running mock evaluation for demonstration in dry-run.")
            qualified.extend(ScoredArticle(article=a, score=8, reason="Demo dry-run mode") for a in new_candidates[:3])
        else:
            raise ValueError("GEMINI_API_KEY must be provided to run live evaluation.")

    # 3. Rank: freshness decays half a point per hour
    qualified.sort(key=lambda q: ranking_priority(q, now_ts), reverse=True)
    logger.info("%d qualified stories in the queue.", len(qualified))

    last_post = state.last_post_time()
    minutes_since_post = (now_ts - last_post.timestamp()) / 60.0 if last_post else float("inf")
    spacing_ok = minutes_since_post >= config.settings.min_minutes_between_posts

    poster = None
    if not dry_run:
        poster = TelegramPoster(
            bot_token=config.telegram_bot_token,
            channel_id=config.telegram_channel_id,
            timeout=config.settings.request_timeout_seconds
        )

    # 4. Enrich, verify photos, write and post the best stories (lazily: only as many as we post)
    seen_urls: List[str] = []
    posts_sent = 0
    max_age_hours = float(config.settings.max_article_age_hours)
    for item in qualified:
        if posts_sent >= config.settings.max_posts_per_run:
            break
        art = item.article
        if not dry_run and not spacing_ok and not is_breaking(item, config, now_ts):
            logger.info(
                "Next story waits: last post %.0f min ago (< %d min) and '%s' is not breaking.",
                minutes_since_post, config.settings.min_minutes_between_posts, art.title[:60]
            )
            break

        scraper.enrich_article(
            art,
            min_width=config.settings.min_image_width,
            min_height=config.settings.min_image_height,
            max_images=config.settings.max_images_per_post,
            is_seen_func=state.is_image_seen,
            max_candidates=config.settings.max_image_candidates
        )

        # Paywalled stubs would make the copywriter guess
        if art.is_paywalled:
            logger.info("Skipping paywalled story: '%s'", art.title)
            seen_urls.append(art.url)
            continue

        content_len = len((art.full_text or art.summary or "").strip())
        if content_len < 80:
            logger.info("Skipping story with too little text (%d chars): '%s'", content_len, art.title)
            seen_urls.append(art.url)
            continue

        # The page may reveal an older publication time than the feed did
        age = age_hours(art, now_ts)
        if age is not None and age > max_age_hours:
            logger.info("Skipping stale story (%.1f hours old): '%s'", age, art.title)
            seen_urls.append(art.url)
            continue

        if config.settings.require_image and not select_photos(art, config, scraper, ai_engine, state):
            logger.info("Skipping story without a clean, relevant, unused photo: '%s'", art.title)
            seen_urls.append(art.url)
            continue

        logger.info("Processing story [score=%d, age=%s]: '%s' (%s)",
                    item.score, f"{age:.1f}h" if age is not None else "?", art.title, art.source_name)

        if ai_engine:
            post = ai_engine.generate_styled_post(art, recent_titles)
        else:
            # Fallback format for mock dry-run
            post = GeneratedPost(
                article=art,
                headline=art.title,
                caption=(
                    f"<b>📍 {art.title}</b>\n\n{art.summary}\n\n"
                    f'<a href="{config.output.channel_link}">{config.output.channel_name}</a>'
                )
            )

        if post and post.is_duplicate:
            seen_urls.append(art.url)
            continue
        if not post:
            logger.warning("Post generation or factual validation failed for '%s'. Trying next candidate.", art.title)
            if not dry_run:
                register_failure(state, art, config.settings.max_post_attempts)
            continue

        if dry_run:
            print("\n" + "=" * 60)
            print(f"DRY-RUN POST PREVIEW (Score: {item.score}/10 | Source: {art.source_name})")
            print(f"Published: {art.published_at or 'Unknown'}")
            for idx, u in enumerate(art.image_urls):
                print(f"  Photo #{idx + 1}: {u}")
            print("-" * 60)
            print(post.caption)
            print("=" * 60 + "\n")
            posts_sent += 1
            continue

        silent = is_quiet_hour(config)
        success = poster.post(
            caption=post.caption,
            image_url=art.image_url,
            image_urls=art.image_urls,
            require_image=config.settings.require_image,
            silent=silent
        )
        if success:
            state.record_posted_story(art.url, art.title)
            for idx, img_u in enumerate(art.image_urls):
                img_h = art.image_hashes[idx] if idx < len(art.image_hashes) else None
                state.record_posted_image(img_u, img_h)
            state.save()  # Persist immediately so the story can never be posted twice
            recent_titles.append(art.title)
            logger.info("Posted%s: '%s'", " silently" if silent else "", art.title)
            posts_sent += 1
            time.sleep(config.settings.post_delay_seconds)
        else:
            logger.error("Failed to post story to channel: '%s'", art.title)
            register_failure(state, art, config.settings.max_post_attempts)

    # 5. Remember decided stories so they are not scanned again
    if not dry_run:
        for u in rejected_urls + seen_urls + skipped_non_target_urls + skipped_stale_urls:
            state.mark_seen(u)
        state.save()

    logger.info("Pipeline check completed (%d posted).", posts_sent)
    return posts_sent


def run_loop(config: AppConfig, minutes: float, dry_run: bool, push: bool) -> None:
    """Polls sources every poll_interval_seconds until `minutes` have passed (or SIGTERM arrives)."""
    deadline = time.time() + minutes * 60
    interval = max(60, config.settings.poll_interval_seconds)
    scraper = NewsScraper(timeout=config.settings.request_timeout_seconds)  # keeps feed ETags between checks
    stop = {"flag": False}

    def handle_term(signum, frame):
        logger.info("Received signal %d: finishing after the current check.", signum)
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle_term)
    last_push = time.time()

    while not stop["flag"] and time.time() < deadline:
        started = time.time()
        try:
            posted = run_pipeline(config, dry_run=dry_run, scraper=scraper)
        except ValueError:
            raise
        except Exception as e:
            logger.error("Check failed: %s", e, exc_info=True)
            posted = 0

        # Push right after posts; otherwise batch the "seen" bookkeeping every 30 minutes
        if push and not dry_run and (posted or time.time() - last_push > 1800):
            push_state()
            last_push = time.time()

        sleep_for = interval - (time.time() - started)
        while sleep_for > 0 and not stop["flag"] and time.time() < deadline:
            time.sleep(min(5, sleep_for))
            sleep_for -= 5

    if push and not dry_run:
        push_state()


def main():
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        cfg = load_config(args.config)
        if args.loop > 0:
            run_loop(cfg, args.loop, dry_run=args.dry_run, push=args.push_state)
        else:
            run_pipeline(cfg, dry_run=args.dry_run)
            if args.push_state and not args.dry_run:
                push_state()
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

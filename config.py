import os
import yaml
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()


@dataclass
class SourceConfig:
    name: str
    url: str
    type: str = "rss"  # "rss" (RSS/Atom feed) or "sitemap" (Google News sitemap: complete, exact timestamps)
    enabled: bool = True
    dedicated: bool = False  # Source covers only the target city: skip the keyword pre-filter


@dataclass
class OutputConfig:
    target_language: str = "Ukrainian"
    style_guide: str = ""
    max_caption_length: int = 900
    include_source_link: bool = True
    channel_name: str = "Братислава"
    channel_link: str = "https://t.me/bratislavanewss"


@dataclass
class AIConfig:
    provider: str = "gemini"
    model: str = "gemini-3.5-flash-lite"
    temperature: float = 0.3
    batch_size: int = 10
    vision_check: bool = True  # Let the model inspect candidate photos (watermarks, logos, relevance)
    vision_model: Optional[str] = None  # Defaults to `model`


@dataclass
class SettingsConfig:
    max_articles_per_run: int = 40
    max_posts_per_run: int = 1
    max_article_age_hours: int = 12
    max_images_per_post: int = 4
    max_image_candidates: int = 5
    require_image: bool = True
    min_image_width: int = 600
    min_image_height: int = 350
    history_retention_days: int = 14
    max_post_attempts: int = 3
    post_delay_seconds: int = 3
    request_timeout_seconds: int = 15
    min_minutes_between_posts: int = 20
    min_minutes_between_posts_night: int = 60
    breaking_score: int = 8  # Stories scoring this high skip the spacing between posts
    quiet_hours: str = ""  # e.g. "23-7": night window (local time): silent posts, slower checks, wider spacing
    poll_minutes_day: int = 15  # Base pause between checks in --loop mode
    poll_minutes_night: int = 45
    poll_backoff_max: float = 2.0  # Quiet periods stretch the pause up to this multiple of the base


@dataclass
class AppConfig:
    country: str
    source_language: str
    content_theme: str
    min_score: int
    timezone: str
    output: OutputConfig
    ai: AIConfig
    sources: List[SourceConfig]
    settings: SettingsConfig

    # Secrets from environment
    telegram_bot_token: Optional[str] = None
    telegram_channel_id: Optional[str] = None
    gemini_api_key: Optional[str] = None

    def validate_secrets(self, dry_run: bool = False) -> None:
        """Validates that necessary secrets are present based on execution mode."""
        missing = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not dry_run:
            if not self.telegram_bot_token:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not self.telegram_channel_id:
                missing.append("TELEGRAM_CHANNEL_ID")

        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}.\n"
                f"Please set them in your .env file or export them as environment variables."
            )


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Loads YAML configuration and merges with environment secrets."""
    if not config_path:
        config_path = os.environ.get("CONFIG_FILE", "config.yaml")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Parse nested configs
    output_data = data.get("output", {})
    output_cfg = OutputConfig(
        target_language=output_data.get("target_language", "Ukrainian"),
        style_guide=output_data.get("style_guide", "").strip(),
        max_caption_length=output_data.get("max_caption_length", 900),
        include_source_link=output_data.get("include_source_link", True),
        channel_name=output_data.get("channel_name", "Братислава"),
        channel_link=output_data.get("channel_link", "https://t.me/bratislavanewss")
    )

    ai_data = data.get("ai", {})
    ai_cfg = AIConfig(
        provider=ai_data.get("provider", "gemini"),
        model=ai_data.get("model", "gemini-3.5-flash-lite"),
        temperature=float(ai_data.get("temperature", 0.3)),
        batch_size=int(ai_data.get("batch_size", 10)),
        vision_check=bool(ai_data.get("vision_check", True)),
        vision_model=ai_data.get("vision_model") or None
    )

    sources_list = []
    for s in data.get("sources", []):
        sources_list.append(
            SourceConfig(
                name=s.get("name", "Unknown"),
                url=s.get("url") or s.get("rss_url", ""),
                type=str(s.get("type", "rss")).lower(),
                enabled=s.get("enabled", True),
                dedicated=bool(s.get("dedicated", False))
            )
        )

    settings_data = data.get("settings", {})
    settings_cfg = SettingsConfig(
        max_articles_per_run=int(settings_data.get("max_articles_per_run", 40)),
        max_posts_per_run=int(settings_data.get("max_posts_per_run", 1)),
        max_article_age_hours=int(settings_data.get("max_article_age_hours", 12)),
        max_images_per_post=int(settings_data.get("max_images_per_post", 4)),
        max_image_candidates=int(settings_data.get("max_image_candidates", 5)),
        require_image=bool(settings_data.get("require_image", True)),
        min_image_width=int(settings_data.get("min_image_width", 600)),
        min_image_height=int(settings_data.get("min_image_height", 350)),
        history_retention_days=int(settings_data.get("history_retention_days", 14)),
        max_post_attempts=int(settings_data.get("max_post_attempts", 3)),
        post_delay_seconds=int(settings_data.get("post_delay_seconds", 3)),
        request_timeout_seconds=int(settings_data.get("request_timeout_seconds", 15)),
        min_minutes_between_posts=int(settings_data.get("min_minutes_between_posts", 20)),
        min_minutes_between_posts_night=int(settings_data.get("min_minutes_between_posts_night", 60)),
        breaking_score=int(settings_data.get("breaking_score", 8)),
        quiet_hours=str(settings_data.get("quiet_hours", "") or ""),
        poll_minutes_day=int(settings_data.get("poll_minutes_day", 15)),
        poll_minutes_night=int(settings_data.get("poll_minutes_night", 45)),
        poll_backoff_max=float(settings_data.get("poll_backoff_max", 2.0))
    )

    return AppConfig(
        country=data.get("country", "Slovakia"),
        source_language=data.get("source_language", "Slovak"),
        content_theme=data.get("content_theme", "crazy tabloid news"),
        min_score=int(data.get("min_score", 7)),
        timezone=data.get("timezone", "Europe/Bratislava"),
        output=output_cfg,
        ai=ai_cfg,
        sources=sources_list,
        settings=settings_cfg,
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None,
        telegram_channel_id=os.environ.get("TELEGRAM_CHANNEL_ID", "").strip() or None,
        gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip() or None
    )

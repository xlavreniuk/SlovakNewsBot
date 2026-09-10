import html
import logging
import re
import warnings
from dataclasses import dataclass
from typing import List, Optional
from bs4 import BeautifulSoup
import feedparser
import requests

# Suppress urllib3 LibreSSL warning on macOS if present
warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL 1.1.1+.*")

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class Article:
    url: str
    title: str
    summary: str
    source_name: str
    published_at: Optional[str] = None
    image_url: Optional[str] = None


class NewsScraper:
    """Universal RSS & metadata scraper for any news source."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout: int = 15):
        self.user_agent = user_agent
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,sk;q=0.8,cs;q=0.7",
        })

    def fetch_articles_from_source(self, name: str, rss_url: str) -> List[Article]:
        """Fetches and parses a single RSS feed."""
        logger.info("Fetching RSS feed: %s (%s)", name, rss_url)
        try:
            resp = self.session.get(rss_url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Failed to fetch RSS from %s (%s): %s", name, rss_url, e)
            return []

        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            logger.warning("Feedparser error parsing %s: %s", name, parsed.bozo_exception)
            return []

        articles = []
        for entry in parsed.entries:
            link = entry.get("link", "")
            if not link:
                continue

            clean_url = link.split("?")[0].split("#")[0].strip()
            title = self._clean_text(entry.get("title", ""))
            if not title:
                continue

            summary = self._extract_summary(entry)
            published = entry.get("published") or entry.get("updated")
            image_url = self._extract_image(entry)

            articles.append(
                Article(
                    url=clean_url,
                    title=title,
                    summary=summary,
                    source_name=name,
                    published_at=published,
                    image_url=image_url
                )
            )

        logger.info("Retrieved %d entries from %s", len(articles), name)
        return articles

    def enrich_image_if_missing(self, article: Article) -> None:
        """Robust multi-source image scraper (OpenGraph, Twitter card, JSON-LD, and lead figures)."""
        # If image already present, verify it is accessible
        if article.image_url:
            if self._verify_image_url(article.image_url):
                return
            else:
                logger.debug("Existing image_url failed verification: %s", article.image_url)
                article.image_url = None

        try:
            resp = self.session.get(article.url, timeout=8)
            if resp.status_code != 200:
                return

            soup = BeautifulSoup(resp.content, "html.parser")

            # 1. OpenGraph meta (highest quality)
            for attr in [{"property": "og:image"}, {"name": "og:image"}, {"property": "og:image:secure_url"}]:
                tag = soup.find("meta", attrs=attr)
                if tag and tag.get("content"):
                    candidate = tag.get("content").strip()
                    if self._verify_image_url(candidate):
                        article.image_url = candidate
                        logger.info("Found verified og:image for '%s': %s", article.title[:40], candidate)
                        return

            # 2. Twitter Card image
            for attr in [{"name": "twitter:image"}, {"property": "twitter:image"}]:
                tag = soup.find("meta", attrs=attr)
                if tag and tag.get("content"):
                    candidate = tag.get("content").strip()
                    if self._verify_image_url(candidate):
                        article.image_url = candidate
                        logger.info("Found verified twitter:image for '%s'", article.title[:40])
                        return

            # 3. Schema.org JSON-LD structured data
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    import json
                    data = json.loads(script.string or "")
                    if isinstance(data, dict):
                        img = data.get("image")
                        candidate = None
                        if isinstance(img, str):
                            candidate = img
                        elif isinstance(img, list) and img and isinstance(img[0], str):
                            candidate = img[0]
                        elif isinstance(img, dict) and img.get("url"):
                            candidate = img.get("url")

                        if candidate and self._verify_image_url(candidate):
                            article.image_url = candidate
                            logger.info("Found verified JSON-LD image for '%s'", article.title[:40])
                            return
                except Exception:
                    pass

            # 4. Lead figure or article image
            for selector in ["figure img", "article img", ".lead-image img", ".article-body img"]:
                img = soup.select_one(selector)
                if img:
                    candidate = img.get("src") or img.get("data-src")
                    if candidate and candidate.startswith("http"):
                        if self._verify_image_url(candidate):
                            article.image_url = candidate
                            logger.info("Found verified figure image for '%s'", article.title[:40])
                            return
        except Exception as e:
            logger.debug("Failed to enrich image for %s: %s", article.url, e)

    def _verify_image_url(self, url: str) -> bool:
        """Verifies that an image URL is live, returns HTTP 200, and is a valid image > 3KB."""
        if not url or not url.startswith("http"):
            return False
        try:
            r = self.session.get(url, timeout=5, stream=True)
            if r.status_code == 200:
                ctype = r.headers.get("Content-Type", "").lower()
                clen = int(r.headers.get("Content-Length", 10000))
                # Must be an image and greater than 3KB (to filter out tracking pixels and tiny icons)
                if ("image" in ctype or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))) and clen > 3000:
                    return True
        except Exception:
            pass
        return False

    def _extract_summary(self, entry) -> str:
        """Extracts and strips HTML tags from description or summary."""
        raw = entry.get("summary") or entry.get("description") or ""
        return self._clean_text(raw)

    def _extract_image(self, entry) -> Optional[str]:
        """Extracts image URL from enclosures, media_content, media_thumbnail, or HTML body."""
        # 1. Check enclosures
        enclosures = entry.get("enclosures", [])
        if enclosures:
            for enc in enclosures:
                href = enc.get("href")
                enc_type = enc.get("type", "")
                if href and ("image" in enc_type or self._is_image_url(href)):
                    return href.strip()

        # 2. Check media_content
        media_content = entry.get("media_content", [])
        if media_content:
            for m in media_content:
                url = m.get("url")
                m_type = m.get("type", "")
                if url and ("image" in m_type or self._is_image_url(url)):
                    return url.strip()

        # 3. Check media_thumbnail
        media_thumbnail = entry.get("media_thumbnail", [])
        if media_thumbnail:
            for t in media_thumbnail:
                url = t.get("url")
                if url:
                    return url.strip()

        # 4. Check <img> tag inside summary/content
        content_text = entry.get("summary", "")
        if "content" in entry and entry.content:
            content_text += " " + "".join(c.get("value", "") for c in entry.content)

        if "<img" in content_text:
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content_text, re.IGNORECASE)
            if match:
                img_url = match.group(1).strip()
                if self._is_image_url(img_url) or img_url.startswith("http"):
                    return img_url

        return None

    @staticmethod
    def _is_image_url(url: str) -> bool:
        lower = url.lower().split("?")[0]
        return lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))

    @staticmethod
    def _clean_text(raw_html: str) -> str:
        if not raw_html:
            return ""
        # Remove HTML tags
        soup = BeautifulSoup(raw_html, "html.parser")
        text = soup.get_text(separator=" ")
        # Unescape HTML entities (&quot;, &amp;, etc.)
        text = html.unescape(text)
        # Collapse multiple spaces and line breaks
        text = re.sub(r"\s+", " ", text).strip()
        return text

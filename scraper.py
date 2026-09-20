import html
import io
import json
import logging
import re
import warnings
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import feedparser
from PIL import Image
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
    """Universal RSS & high-resolution metadata scraper for any news source."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout: int = 15):
        self.user_agent = user_agent
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,sk;q=0.8,cs;q=0.7",
        })
        self._image_cache: Dict[str, Optional[Tuple[int, int, str, int]]] = {}

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

    def upgrade_url_variants(self, url: str) -> List[str]:
        """
        Generates high-resolution upgrade variants for common Slovak news CMSs and image CDNs.
        Returns a list of URLs with the highest expected resolution variants first.
        """
        if not url or not url.startswith("http"):
            return []

        variants: List[str] = []

        # 1. WordPress: Strip resized dimension suffixes (e.g. image-724x1024.png -> image.png)
        wp = re.sub(r'-\d+x\d+(\.[a-zA-Z0-9]+)$', r'\1', url)
        if wp != url:
            variants.append(wp)

        # 2. Pravda CDN: Upgrade /4x3-medium/, /4x3-small/, etc. to /16x9-big/
        prv = re.sub(r'/(?:4x3|16x9)-(?:medium|small|big)/', '/16x9-big/', url)
        if prv != url:
            variants.append(prv)

        # 3. Topky CDN: Upgrade img.topky.sk/{id}.jpg to img.topky.sk/1280px/{id}.jpg
        topky = re.sub(r'img\.topky\.sk/(?:640px/)?(\d+\.jpe?g)', r'img.topky.sk/1280px/\1', url)
        if topky != url:
            variants.append(topky)

        # 4. Zoznam image proxy: Unwrap nested original Topky URL
        m_zoz = re.search(r'https://img\.topky\.sk/[^\s\'"]+', url)
        if m_zoz:
            unwrapped = m_zoz.group(0)
            variants.append(re.sub(r'img\.topky\.sk/(?:640px/)?(\d+\.jpe?g)', r'img.topky.sk/1280px/\1', unwrapped))
            variants.append(unwrapped)

        # 5. Generic query parameter stripping for resizers
        if any(param in url for param in ("?w=", "&w=", "width=", "resize=", "fit=")):
            stripped = url.split("?")[0]
            if stripped != url:
                variants.append(stripped)

        variants.append(url)

        # Deduplicate while preserving order
        seen: Set[str] = set()
        ordered: List[str] = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                ordered.append(v)
        return ordered

    def probe_image(self, url: str) -> Optional[Tuple[int, int, str, int]]:
        """
        Probes image URL using Pillow.
        Returns (width, height, format, byte_size) or None if inaccessible or invalid.
        """
        if not url or not url.startswith("http"):
            return None
        if url in self._image_cache:
            return self._image_cache[url]

        try:
            resp = self.session.get(url, timeout=6)
            if resp.status_code == 200 and len(resp.content) > 3000:
                img = Image.open(io.BytesIO(resp.content))
                info = (img.width, img.height, img.format or "JPEG", len(resp.content))
                self._image_cache[url] = info
                return info
        except Exception:
            pass

        self._image_cache[url] = None
        return None

    def _collect_page_candidates(self, page_url: str) -> List[Tuple[str, str]]:
        """Scrapes article webpage to discover OpenGraph, Twitter, JSON-LD, srcset, and lead images."""
        candidates: List[Tuple[str, str]] = []
        try:
            resp = self.session.get(page_url, timeout=8)
            if resp.status_code != 200:
                return candidates

            soup = BeautifulSoup(resp.content, "html.parser")

            # 1. OpenGraph meta (highest editorial quality)
            for attr in [{"property": "og:image"}, {"property": "og:image:secure_url"}, {"name": "og:image"}]:
                tag = soup.find("meta", attrs=attr)
                if tag and tag.get("content"):
                    candidates.append(("og:image", tag.get("content").strip()))

            # 2. Twitter Card meta
            for attr in [{"name": "twitter:image"}, {"property": "twitter:image"}, {"name": "twitter:image:src"}]:
                tag = soup.find("meta", attrs=attr)
                if tag and tag.get("content"):
                    candidates.append(("twitter:image", tag.get("content").strip()))

            # 3. Schema.org JSON-LD structured data
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")

                    def extract_jsonld_images(obj):
                        if isinstance(obj, dict):
                            if "image" in obj:
                                img = obj["image"]
                                if isinstance(img, str):
                                    candidates.append(("json-ld", img.strip()))
                                elif isinstance(img, list):
                                    for item in img:
                                        if isinstance(item, str):
                                            candidates.append(("json-ld", item.strip()))
                                        elif isinstance(item, dict) and "url" in item:
                                            candidates.append(("json-ld", str(item["url"]).strip()))
                                elif isinstance(img, dict) and "url" in img:
                                    candidates.append(("json-ld", str(img["url"]).strip()))
                            for val in obj.values():
                                extract_jsonld_images(val)
                        elif isinstance(obj, list):
                            for elem in obj:
                                extract_jsonld_images(elem)

                    extract_jsonld_images(data)
                except Exception:
                    pass

            # 4. srcset descriptors on picture/figure/article images
            for el in soup.select("article img, figure img, .lead-image img, picture source, picture img"):
                srcset = el.get("srcset")
                if srcset:
                    parts = [p.strip() for p in srcset.split(",") if p.strip()]
                    for p in parts:
                        tokens = p.split()
                        if tokens:
                            resolved = urljoin(page_url, tokens[0])
                            candidates.append(("srcset", resolved))

            # 5. Lead figures and article images
            for selector in [
                "figure img", "article img", ".lead-image img",
                ".article-body img", "img[data-src]", "img[data-full-src]", "img[data-original]"
            ]:
                for el in soup.select(selector):
                    src = el.get("src") or el.get("data-src") or el.get("data-full-src") or el.get("data-original")
                    if src and not src.startswith("data:"):
                        resolved = urljoin(page_url, src.strip())
                        candidates.append(("lead-img", resolved))

        except Exception as e:
            logger.debug("Failed to collect page candidates for %s: %s", page_url, e)

        return candidates

    def enrich_image(self, article: Article, min_width: int = 500, min_height: int = 300) -> None:
        """
        Extracts, verifies, and upgrades images to ensure the highest resolution photo is selected.
        Uses Pillow to probe dimensions and reject blurry thumbnails or tracking icons.
        """
        raw_candidates: List[Tuple[str, str]] = []

        # If RSS provided an image, include it as initial candidate
        if article.image_url:
            raw_candidates.append(("rss", article.image_url))

        # Scrape article webpage to discover high-resolution options
        page_candidates = self._collect_page_candidates(article.url)
        raw_candidates.extend(page_candidates)

        # Expand all candidates into high-res URL variants
        all_variants: List[Tuple[str, str]] = []
        seen_urls: Set[str] = set()

        for tag, url in raw_candidates:
            variants = self.upgrade_url_variants(url)
            for v in variants:
                if v not in seen_urls and v.startswith("http"):
                    seen_urls.add(v)
                    all_variants.append((tag, v))

        # Probe candidate variants with Pillow and score them
        scored_candidates: List[Tuple[float, int, int, str, int, str]] = []

        for tag, url in all_variants:
            info = self.probe_image(url)
            if not info:
                continue

            width, height, fmt, byte_size = info

            # Strictly require minimum HD dimensions and valid image size
            if width < min_width or height < min_height or byte_size < 8000:
                continue

            # Filter out extreme aspect ratios (thin separator banners or ultra-tall ribbons)
            ratio = width / max(height, 1)
            if ratio > 4.5 or ratio < 0.25:
                continue

            pixel_area = width * height
            score = float(pixel_area)

            # Strong boost for candidates meeting minimum HD dimensions
            if width >= min_width and height >= min_height:
                score += 5_000_000.0

            # Editorial bonus for OpenGraph, Twitter card, or JSON-LD lead images
            if tag in ("og:image", "twitter:image", "json-ld"):
                score += 500_000.0

            scored_candidates.append((score, width, height, fmt, byte_size, url))

        if scored_candidates:
            # Sort highest score first
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            best = scored_candidates[0]
            article.image_url = best[5]
            logger.info(
                "Selected high-res image for '%s': %dx%d (%s, %d bytes) -> %s",
                article.title[:40], best[1], best[2], best[3], best[4], best[5]
            )
        else:
            logger.warning("No suitable high-resolution image found for '%s'", article.title[:40])
            # If the original image was accessible, keep as fallback
            if article.image_url and not self._verify_image_url(article.image_url):
                article.image_url = None

    def enrich_image_if_missing(self, article: Article) -> None:
        """Backwards-compatible alias for enrich_image."""
        self.enrich_image(article)

    def _verify_image_url(self, url: str) -> bool:
        """Verifies that an image URL is live, returns HTTP 200, and is a valid image >= 500x300."""
        info = self.probe_image(url)
        return info is not None and info[0] >= 500 and info[1] >= 300

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

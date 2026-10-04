import calendar
import datetime
import email.utils
import html
import io
import json
import logging
import re
import warnings
from dataclasses import dataclass, field
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
    published_timestamp: Optional[float] = None
    image_url: Optional[str] = None
    image_urls: List[str] = field(default_factory=list)
    image_hash: Optional[str] = None
    image_hashes: List[str] = field(default_factory=list)
    full_text: Optional[str] = None
    is_paywalled: bool = False


class NewsScraper:
    """Universal RSS & high-resolution metadata scraper for any news source."""

    PLACEHOLDER_PATTERNS = [
        re.compile(p, re.IGNORECASE) for p in [
            r"ilustrac", r"placeholder", r"fb[-_]share", r"share[-_]image", r"social[-_]share",
            r"default[-_]image", r"symbolic", r"/logos?/", r"avatar", r"author[-_]",
            r"static/images/", r"pravda-fallback", r"logo[-_]", r"[-_]logo", r"no[-_]image",
            r"weather[-_]box", r"sample\.png", r"blank\.", r"transparent\.", r"spacer\.",
            r"pixel\.", r"icon[-_]", r"widget", r"banner[-_]", r"tasr[-_]logo",
            r"sita[-_]logo", r"zoznam[-_]logo", r"fb_share", r"fallback", r"preview-default",
            r"sme-logo", r"aktuality-logo",
            # Generic numbered uploads / CMS placeholders
            r"image[-_]?\d+\.(?:jpe?g|png|webp)",
            r"fotka[-_]?\d+\.(?:jpe?g|png|webp)",
            r"snimka[-_]?\d+\.(?:jpe?g|png|webp)",
            r"obrazok[-_]?\d+\.(?:jpe?g|png|webp)",
            r"img[-_]?\d+\.(?:jpe?g|png|webp)",
            r"screenshot[-_]?\d*",
            r"snimka[-_]?obrazovky",
            # Stock photo agencies & generic photobanks
            r"shutterstock", r"depositphotos", r"istock", r"getty", r"dreamstime",
            r"adobe[-_]?stock", r"freepik", r"unsplash", r"pixabay", r"profimedia",
            r"fotobanka", r"stock[-_]?photo", r"brianajackson",
            # Money / cash / currency filler graphics
            r"peniaz", r"peniaze", r"bankovk", r"euro[-_]?peniaze", r"cash[-_]", r"mena[-_]?euro",
            r"stoeurov", r"tisic[-_]?eur", r"valuty", r"dolare",
            # Senior / scam / fraud stock themes
            r"podvod[-_]na[-_]senior", r"podvodnik", r"fiktivn",
            r"dochodkyn", r"starenk", r"babick", r"babk", r"starec",
            # Generic police / handcuffs / courtroom / prison stock
            r"policia[-_]ilustr", r"puta[-_]na[-_]ruk", r"sud[-_]kladiv", r"vazenie[-_]mrez"
        ]
    ]

    STOCK_CAPTION_PATTERNS = [
        re.compile(p, re.IGNORECASE) for p in [
            r"ilustra[čc]n",
            r"symbolick",
            r"profimedia",
            r"shutterstock",
            r"getty",
            r"istock",
            r"depositphoto",
            r"fotobanka",
            r"brianajackson",
            r"peniaze",
            r"bankovk",
            r"arch[ií]v",
            r"stock"
        ]
    ]

    PAYWALL_PATTERNS = [
        re.compile(p, re.IGNORECASE) for p in [
            r"iba pre predplatiteľov",
            r"článok je zamknutý",
            r"zamknutý obsah",
            r"predplatitelia",
            r"kúpte si predplatné",
            r"piano predplatné",
            r"odomknite si článok",
            r"získajte prístup",
            r"obsah je spoplatnený",
            r"čítajte viac s predplatným",
            r"tento text je dostupný iba",
            r"vstúpte do klubu",
            r"predplatné denníka",
            r"predplatné sme",
            r"predplatné n",
            r"zamknute"
        ]
    ]

    BOILERPLATE_PATTERNS = [
        re.compile(p, re.IGNORECASE) for p in [
            r"^čítajte tiež:",
            r"^sledujte nás",
            r"^pozrite si viac",
            r"^viac k téme",
            r"^reklama",
            r"^zdroj:",
            r"^foto:",
            r"^tasr",
            r"^sita",
            r"všetky práva vyhradené",
            r"autorské práva vyhradené",
            r"^čítajte viac:"
        ]
    ]

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
            published_str, published_ts = self._extract_date_info(entry)
            image_url = self._extract_image(entry)

            art = Article(
                url=clean_url,
                title=title,
                summary=summary,
                source_name=name,
                published_at=published_str,
                published_timestamp=published_ts,
                image_url=image_url,
                image_urls=[image_url] if image_url else []
            )
            articles.append(art)

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

    @staticmethod
    def compute_image_hash(img: Image.Image) -> Optional[str]:
        """Computes a 64-bit difference hash (dHash) as a 16-hex character string."""
        try:
            resized = img.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(resized.getdata())
            diff = []
            for row in range(8):
                row_offset = row * 9
                for col in range(8):
                    pixel_left = pixels[row_offset + col]
                    pixel_right = pixels[row_offset + col + 1]
                    diff.append('1' if pixel_left > pixel_right else '0')
            return hex(int(''.join(diff), 2))[2:].zfill(16)
        except Exception:
            return None

    def probe_image(self, url: str) -> Optional[Tuple[int, int, str, int, Optional[str]]]:
        """
        Probes image URL using Pillow.
        Returns (width, height, format, byte_size, hash_hex) or None if inaccessible or invalid.
        """
        if not url or not url.startswith("http"):
            return None
        if url in self._image_cache:
            return self._image_cache[url]

        try:
            resp = self.session.get(url, timeout=6)
            if resp.status_code == 200 and len(resp.content) > 3000:
                img = Image.open(io.BytesIO(resp.content))
                img_hash = self.compute_image_hash(img)
                info = (img.width, img.height, img.format or "JPEG", len(resp.content), img_hash)
                self._image_cache[url] = info
                return info
        except Exception:
            pass

        self._image_cache[url] = None
        return None

    def _extract_date_info(self, entry) -> Tuple[Optional[str], Optional[float]]:
        """Extracts publication string and Unix epoch timestamp from feed entry."""
        st = entry.get("published_parsed") or entry.get("updated_parsed")
        ts: Optional[float] = None
        if st:
            try:
                ts = float(calendar.timegm(st))
            except Exception:
                ts = None

        raw_date = entry.get("published") or entry.get("updated") or entry.get("dc_date") or entry.get("created")

        if ts is None and raw_date and isinstance(raw_date, str):
            # Try RFC 2822 format (e.g. Fri, 02 Oct 2026 14:00:00 +0200)
            try:
                dt = email.utils.parsedate_to_datetime(raw_date)
                ts = dt.timestamp()
            except Exception:
                pass
            # Try ISO 8601 format (e.g. 2026-10-02T14:00:00+02:00)
            if ts is None:
                try:
                    dt = datetime.datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    ts = dt.timestamp()
                except Exception:
                    pass

        if ts is not None and not raw_date:
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            raw_date = dt.strftime("%Y-%m-%d %H:%M:%S UTC")

        return raw_date, ts

    def _is_placeholder_image(self, url: str, alt_text: str = "", caption_text: str = "") -> bool:
        """Detects whether an image URL, alt-text, or caption is a generic stock/placeholder, logo, or icon."""
        if not url:
            return True
        combined = f"{url} {alt_text} {caption_text}".lower()
        if any(pattern.search(combined) for pattern in self.PLACEHOLDER_PATTERNS):
            return True
        if (alt_text or caption_text) and any(pattern.search(f"{alt_text} {caption_text}".lower()) for pattern in self.STOCK_CAPTION_PATTERNS):
            return True
        return False

    @staticmethod
    def _is_stale_archive_image(url: str, article_year: Optional[int] = None) -> bool:
        """
        Detects whether an image is a stale archive upload from a previous year.
        CMSs (WordPress, Topky, Zoznam) embed upload dates in URL paths (e.g. /uploads/2024/08/...).
        For breaking local news, images uploaded 1+ years before publication are recycled stock fillers.
        """
        if not url:
            return False
        m = re.search(r'/(20[12]\d)(?:[/-]|\b)', url)
        if not m:
            return False
        try:
            upload_year = int(m.group(1))
            curr_year = article_year or datetime.datetime.now(datetime.timezone.utc).year
            if upload_year <= (curr_year - 2) or upload_year < 2025:
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _image_dedup_key(url: str) -> str:
        """Normalizes an image URL into a base filename key to avoid duplicate crops of the same photo."""
        clean = url.split("?")[0].lower()
        clean = re.sub(r'-\d+x\d+(\.[a-zA-Z0-9]+)$', r'\1', clean)
        clean = re.sub(r'/(?:4x3|16x9)-(?:medium|small|big)/', '/', clean)
        clean = re.sub(r'/(?:640px|1280px)/', '/', clean)
        parts = clean.split("/")
        return parts[-1] if parts else clean

    def _extract_page_text(self, soup: BeautifulSoup) -> Tuple[Optional[str], bool]:
        """
        Extracts verified factual paragraphs from article webpage.
        Detects paywalled articles and filters out promo boilerplate.
        """
        is_paywalled = False

        # 1. Check known paywall class indicators
        paywall_selectors = [
            ".c-locked-article", ".paywall", ".piano-article", ".spoplatnene",
            "[class*='locked']", "[class*='paywall']", "[class*='piano']",
            ".article-locked", "#paywall"
        ]
        if soup.select(", ".join(paywall_selectors)):
            is_paywalled = True

        # 2. Select main content container
        content_containers = soup.select(
            "article, [itemprop='articleBody'], .article-body, .article__content, "
            ".entry-content, .post-content, .detail-text, .detail_text, .c-article-content, .text-content"
        )
        container = content_containers[0] if content_containers else soup

        paragraphs: List[str] = []
        for p in container.find_all("p"):
            p_text = self._clean_text(p.get_text())
            if len(p_text) < 25:
                continue
            if any(pat.search(p_text) for pat in self.PAYWALL_PATTERNS):
                is_paywalled = True
                break
            if any(pat.search(p_text) for pat in self.BOILERPLATE_PATTERNS):
                continue
            paragraphs.append(p_text)

        full_text = " ".join(paragraphs).strip() if paragraphs else None

        # If text is very short and paywall was triggered, treat as paywalled
        if is_paywalled and (not full_text or len(full_text) < 180):
            return None, True

        return full_text, is_paywalled

    def _collect_page_candidates(
        self,
        page_url: str,
        article_year: Optional[int] = None
    ) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]], Optional[str], Optional[float], Optional[str], bool, bool]:
        """
        Scrapes article webpage to discover:
        1. Editorial lead images (OpenGraph, Twitter, JSON-LD, top lead figure).
        2. Genuine photo gallery images (STRICTLY inside .fotogaleria, .gallery containers).
        Also extracts publication date metadata, full article text, and checks for paywalls.
        """
        lead_candidates: List[Tuple[str, str]] = []
        gallery_candidates: List[Tuple[str, str]] = []
        page_date_str: Optional[str] = None
        page_date_ts: Optional[float] = None
        page_full_text: Optional[str] = None
        is_paywalled: bool = False
        has_genuine_gallery: bool = False

        try:
            resp = self.session.get(page_url, timeout=8)
            if resp.status_code != 200:
                return lead_candidates, gallery_candidates, page_date_str, page_date_ts, page_full_text, is_paywalled, has_genuine_gallery

            soup = BeautifulSoup(resp.content, "html.parser")
            page_full_text, is_paywalled = self._extract_page_text(soup)

            # Extract page publication date from HTML metadata if present
            for attr in [
                {"property": "article:published_time"},
                {"name": "article:published_time"},
                {"name": "pubdate"},
                {"property": "og:article:published_time"}
            ]:
                tag = soup.find("meta", attrs=attr)
                if tag and tag.get("content"):
                    c_val = tag.get("content").strip()
                    try:
                        dt = datetime.datetime.fromisoformat(c_val.replace("Z", "+00:00"))
                        page_date_str = c_val
                        page_date_ts = dt.timestamp()
                        if not article_year:
                            article_year = dt.year
                        break
                    except Exception:
                        pass

            if not page_date_ts:
                time_el = soup.find("time")
                if time_el and time_el.get("datetime"):
                    c_val = time_el.get("datetime").strip()
                    try:
                        dt = datetime.datetime.fromisoformat(c_val.replace("Z", "+00:00"))
                        page_date_str = c_val
                        page_date_ts = dt.timestamp()
                        if not article_year:
                            article_year = dt.year
                    except Exception:
                        pass

            # 1. OpenGraph meta (highest editorial quality lead image)
            for attr in [{"property": "og:image"}, {"property": "og:image:secure_url"}, {"name": "og:image"}]:
                tag = soup.find("meta", attrs=attr)
                if tag and tag.get("content"):
                    u = tag.get("content").strip()
                    if not self._is_placeholder_image(u) and not self._is_stale_archive_image(u, article_year):
                        lead_candidates.append(("og:image", u))

            # 2. Twitter Card meta
            for attr in [{"name": "twitter:image"}, {"property": "twitter:image"}, {"name": "twitter:image:src"}]:
                tag = soup.find("meta", attrs=attr)
                if tag and tag.get("content"):
                    u = tag.get("content").strip()
                    if not self._is_placeholder_image(u) and not self._is_stale_archive_image(u, article_year):
                        lead_candidates.append(("twitter:image", u))

            # 3. Schema.org JSON-LD structured data primary image
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")

                    def extract_jsonld_images(obj):
                        if isinstance(obj, dict):
                            # Skip publisher or author logos inside JSON-LD
                            if obj.get("@type") in ("Organization", "Person") and "logo" in obj:
                                pass
                            elif "image" in obj:
                                img = obj["image"]
                                if isinstance(img, str) and not self._is_placeholder_image(img) and not self._is_stale_archive_image(img, article_year):
                                    lead_candidates.append(("json-ld", img.strip()))
                                elif isinstance(img, list):
                                    for item in img:
                                        if isinstance(item, str) and not self._is_placeholder_image(item) and not self._is_stale_archive_image(item, article_year):
                                            lead_candidates.append(("json-ld", item.strip()))
                                        elif isinstance(item, dict) and "url" in item:
                                            u = str(item["url"]).strip()
                                            if not self._is_placeholder_image(u) and not self._is_stale_archive_image(u, article_year):
                                                lead_candidates.append(("json-ld", u))
                                elif isinstance(img, dict) and "url" in img:
                                    u = str(img["url"]).strip()
                                    if not self._is_placeholder_image(u) and not self._is_stale_archive_image(u, article_year):
                                        lead_candidates.append(("json-ld", u))
                            for k, val in obj.items():
                                if k not in ("publisher", "author", "creator"):
                                    extract_jsonld_images(val)
                        elif isinstance(obj, list):
                            for elem in obj:
                                extract_jsonld_images(elem)

                    extract_jsonld_images(data)
                except Exception:
                    pass

            # Decompose irrelevant navigation, sidebar, footer, side articles, and promo/read-more boxes
            for unwanted in soup.select(
                "nav, footer, header, aside, .sidebar, .widget, .comments, .related, .recommended, "
                ".menu, .ad, .advertisement, .most-read, .footer, article.article--side, .detail_text_item_readmore, "
                ".detail_related_list, [class*='readmore'], [class*='read-more'], [class*='related'], "
                "[class*='promo'], [class*='recommend'], [class*='najcitanejsie']"
            ):
                unwanted.decompose()

            # 4. Check for genuine event photo gallery containers (STRICTLY dedicated galleries)
            gallery_containers = soup.select(
                ".fotogaleria, .fotogalerie, .gallery, .c-gallery, [class*='gallery-'], "
                "[class*='fotogaleri'], .image-gallery, .wp-block-gallery, [id*='gallery']"
            )
            if gallery_containers:
                has_genuine_gallery = True
                for g_box in gallery_containers:
                    for g_img in g_box.select("img"):
                        alt = g_img.get("alt", "")
                        title = g_img.get("title", "")
                        fig = g_img.find_parent("figure")
                        caption = fig.find("figcaption").get_text() if (fig and fig.find("figcaption")) else ""
                        src = (
                            g_img.get("data-full-src") or
                            g_img.get("data-highres") or
                            g_img.get("data-original") or
                            g_img.get("data-src") or
                            g_img.get("src")
                        )
                        if src and not src.startswith("data:"):
                            resolved = urljoin(page_url, src.strip())
                            if not self._is_placeholder_image(resolved, alt, f"{title} {caption}") and not self._is_stale_archive_image(resolved, article_year):
                                gallery_candidates.append(("gallery-img", resolved))

                    # Direct high-res image links inside gallery
                    for a_el in g_box.select("a[href]"):
                        href = a_el.get("href", "")
                        if self._is_image_url(href):
                            resolved = urljoin(page_url, href.strip())
                            if not self._is_placeholder_image(resolved) and not self._is_stale_archive_image(resolved, article_year):
                                gallery_candidates.append(("gallery-link", resolved))

            # 5. Editorial lead figure on page (e.g. figure.lead, .lead-image)
            lead_selectors = [
                ".lead-image img", "figure.lead img", "picture.lead img",
                ".detail_text_image_lead img", "[itemprop='image']",
                "article > figure:first-of-type img", ".article-header img"
            ]
            for selector in lead_selectors:
                for el in soup.select(selector):
                    alt = el.get("alt", "")
                    title = el.get("title", "")
                    fig = el.find_parent("figure")
                    caption = fig.find("figcaption").get_text() if (fig and fig.find("figcaption")) else ""
                    src = (
                        el.get("data-full-src") or
                        el.get("data-highres") or
                        el.get("data-original") or
                        el.get("data-src") or
                        el.get("src")
                    )
                    if src and not src.startswith("data:"):
                        resolved = urljoin(page_url, src.strip())
                        if not self._is_placeholder_image(resolved, alt, f"{title} {caption}") and not self._is_stale_archive_image(resolved, article_year):
                            lead_candidates.append(("lead-elem", resolved))

                    srcset = el.get("srcset")
                    if srcset:
                        for p in [x.strip() for x in srcset.split(",") if x.strip()]:
                            tokens = p.split()
                            if tokens:
                                resolved = urljoin(page_url, tokens[0])
                                if not self._is_placeholder_image(resolved, alt, f"{title} {caption}") and not self._is_stale_archive_image(resolved, article_year):
                                    lead_candidates.append(("lead-srcset", resolved))

            # 6. Fallback: If no lead candidates found yet, check the first figure/img in article body
            if not lead_candidates:
                first_body_img = soup.select_one("article figure img, article picture img, .article-body figure img, .article-body img")
                if first_body_img:
                    alt = first_body_img.get("alt", "")
                    src = (
                        first_body_img.get("data-full-src") or
                        first_body_img.get("data-highres") or
                        first_body_img.get("data-original") or
                        first_body_img.get("data-src") or
                        first_body_img.get("src")
                    )
                    if src and not src.startswith("data:"):
                        resolved = urljoin(page_url, src.strip())
                        if not self._is_placeholder_image(resolved, alt) and not self._is_stale_archive_image(resolved, article_year):
                            lead_candidates.append(("fallback-lead", resolved))

        except Exception as e:
            logger.debug("Failed to collect page candidates for %s: %s", page_url, e)

        return lead_candidates, gallery_candidates, page_date_str, page_date_ts, page_full_text, is_paywalled, has_genuine_gallery

    def enrich_article(
        self,
        article: Article,
        min_width: int = 600,
        min_height: int = 350,
        max_images: int = 4,
        is_seen_func: Optional[callable] = None
    ) -> None:
        """
        Enriches article with full body text, checks against paywalls,
        and extracts, verifies, and upgrades images to ensure high-resolution photos are selected.
        Eliminates generic stock placeholders, currency fillers, and stale CMS archive placeholders.
        Default: Exactly ONE verified lead image.
        Albums (image_urls > 1): ONLY enabled if a genuine event photo gallery is present.
        """
        raw_lead_candidates: List[Tuple[str, str]] = []

        # Determine article publication year
        article_year = None
        if article.published_timestamp:
            try:
                article_year = datetime.datetime.fromtimestamp(article.published_timestamp, tz=datetime.timezone.utc).year
            except Exception:
                pass

        # Scrape article webpage to discover full text, paywalls, lead photos, and genuine galleries
        (
            page_lead_candidates,
            gallery_candidates,
            page_date_str,
            page_date_ts,
            page_full_text,
            is_paywalled,
            has_genuine_gallery
        ) = self._collect_page_candidates(article.url, article_year=article_year)

        # Update full text and paywall status
        if page_full_text:
            article.full_text = page_full_text
            logger.debug("Enriched full article text for '%s' (%d chars)", article.title[:30], len(page_full_text))
        article.is_paywalled = is_paywalled

        # Backfill timestamp if RSS lacked one
        if article.published_timestamp is None and page_date_ts is not None:
            article.published_timestamp = page_date_ts
            article.published_at = page_date_str
            article_year = datetime.datetime.fromtimestamp(page_date_ts, tz=datetime.timezone.utc).year
            logger.info("Enriched publication date for '%s': %s (ts=%s)", article.title[:30], page_date_str, page_date_ts)

        # If RSS provided an image, include if not a placeholder or stale archive
        if article.image_url and not self._is_placeholder_image(article.image_url) and not self._is_stale_archive_image(article.image_url, article_year):
            raw_lead_candidates.append(("rss", article.image_url))

        raw_lead_candidates.extend(page_lead_candidates)

        # Expand lead candidates into high-res URL variants
        all_lead_variants: List[Tuple[str, str]] = []
        seen_lead_variant_urls: Set[str] = set()

        for tag, url in raw_lead_candidates:
            variants = self.upgrade_url_variants(url)
            for v in variants:
                if v not in seen_lead_variant_urls and v.startswith("http") and not self._is_placeholder_image(v) and not self._is_stale_archive_image(v, article_year):
                    seen_lead_variant_urls.add(v)
                    all_lead_variants.append((tag, v))

        # Probe candidate lead variants with Pillow and score them
        scored_lead_candidates: List[Tuple[float, int, int, str, int, Optional[str], str]] = []

        for tag, url in all_lead_variants:
            info = self.probe_image(url)
            if not info:
                continue

            width, height, fmt, byte_size, hash_hex = info

            # Strictly require minimum HD dimensions and valid image size
            if width < min_width or height < min_height or byte_size < 10000:
                continue

            # Filter out extreme aspect ratios (thin separator banners or ultra-tall ribbons)
            ratio = width / max(height, 1)
            if ratio > 4.5 or ratio < 0.25:
                continue

            # Cross-post deduplication: reject if image was already posted previously
            if is_seen_func and is_seen_func(url, hash_hex):
                logger.info("Skipping candidate image as already seen in channel: %s (hash=%s)", url, hash_hex)
                continue

            score = float(width * height)

            # Strong boost for candidates meeting minimum HD dimensions
            if width >= min_width and height >= min_height:
                score += 5_000_000.0

            # Editorial bonus for OpenGraph, Twitter card, or JSON-LD lead images
            if tag in ("og:image", "twitter:image", "json-ld"):
                score += 500_000.0

            scored_lead_candidates.append((score, width, height, fmt, byte_size, hash_hex, url))

        if not scored_lead_candidates:
            logger.warning("No suitable high-resolution unseen image found for '%s'", article.title[:40])
            article.image_url = None
            article.image_urls = []
            article.image_hash = None
            article.image_hashes = []
            return

        # Sort highest score first to pick the premier lead photo
        scored_lead_candidates.sort(key=lambda x: x[0], reverse=True)
        best_lead = scored_lead_candidates[0]
        _, best_w, best_h, best_fmt, best_bytes, best_lead_hash, best_lead_url = best_lead

        # DEFAULT: Exactly ONE lead image
        article.image_url = best_lead_url
        article.image_urls = [best_lead_url]
        article.image_hash = best_lead_hash
        article.image_hashes = [best_lead_hash] if best_lead_hash else []

        # Genuine photo gallery handling: ONLY if explicit gallery was found and max_images > 1
        if has_genuine_gallery and max_images > 1:
            all_gallery_variants: List[Tuple[str, str]] = []
            seen_gallery_variant_urls: Set[str] = set()

            for tag, url in gallery_candidates:
                variants = self.upgrade_url_variants(url)
                for v in variants:
                    if v not in seen_gallery_variant_urls and v.startswith("http") and not self._is_placeholder_image(v) and not self._is_stale_archive_image(v, article_year):
                        seen_gallery_variant_urls.add(v)
                        all_gallery_variants.append((tag, v))

            lead_dedup_key = self._image_dedup_key(best_lead_url)
            seen_gallery_keys: Set[str] = {lead_dedup_key}
            accepted_gallery: List[Tuple[str, Optional[str]]] = []

            for tag, url in all_gallery_variants:
                k = self._image_dedup_key(url)
                if k in seen_gallery_keys:
                    continue

                info = self.probe_image(url)
                if not info:
                    continue

                w, h, fmt, b_size, h_hex = info
                if w < min_width or h < min_height or b_size < 10000:
                    continue

                ratio = w / max(h, 1)
                if ratio > 4.5 or ratio < 0.25:
                    continue

                # Check visual similarity against lead image (dHash Hamming distance <= 4 means duplicate)
                if best_lead_hash and h_hex:
                    try:
                        if bin(int(best_lead_hash, 16) ^ int(h_hex, 16)).count('1') <= 4:
                            continue
                    except Exception:
                        pass

                # Check visual similarity against already accepted gallery photos
                is_duplicate = False
                for _, prev_h in accepted_gallery:
                    if prev_h and h_hex:
                        try:
                            if bin(int(prev_h, 16) ^ int(h_hex, 16)).count('1') <= 4:
                                is_duplicate = True
                                break
                        except Exception:
                            pass
                if is_duplicate:
                    continue

                # Check against channel history
                if is_seen_func and is_seen_func(url, h_hex):
                    continue

                seen_gallery_keys.add(k)
                accepted_gallery.append((url, h_hex))

                if len(accepted_gallery) >= (max_images - 1):
                    break

            if accepted_gallery:
                article.image_urls = [best_lead_url] + [p[0] for p in accepted_gallery]
                article.image_hashes = ([best_lead_hash] if best_lead_hash else []) + [p[1] for p in accepted_gallery if p[1]]
                logger.info(
                    "Selected genuine photo gallery (%d photos) for '%s'",
                    len(article.image_urls), article.title[:40]
                )
            else:
                logger.info("Selected single lead photo for '%s': %dx%d -> %s", article.title[:40], best_w, best_h, best_lead_url)
        else:
            logger.info("Selected single lead photo (no gallery present) for '%s': %dx%d -> %s", article.title[:40], best_w, best_h, best_lead_url)

    def enrich_image(self, article: Article, min_width: int = 600, min_height: int = 350, max_images: int = 4) -> None:
        """Backwards-compatible alias for enrich_article."""
        self.enrich_article(article, min_width=min_width, min_height=min_height, max_images=max_images)

    def enrich_image_if_missing(self, article: Article) -> None:
        """Backwards-compatible alias for enrich_article."""
        self.enrich_article(article)

    def _verify_image_url(self, url: str) -> bool:
        """Verifies that an image URL is live, returns HTTP 200, and is a valid image >= 600x350."""
        if not url or self._is_placeholder_image(url):
            return False
        info = self.probe_image(url)
        return info is not None and info[0] >= 600 and info[1] >= 350

    def _extract_summary(self, entry) -> str:
        """Extracts and strips HTML tags from description or summary."""
        raw = entry.get("summary") or entry.get("description") or ""
        return self._clean_text(raw)

    def _extract_image(self, entry) -> Optional[str]:
        """Extracts image URL from enclosures, media_content, media_thumbnail, or HTML body."""
        candidate = None
        # 1. Check enclosures
        enclosures = entry.get("enclosures", [])
        if enclosures:
            for enc in enclosures:
                href = enc.get("href")
                enc_type = enc.get("type", "")
                if href and ("image" in enc_type or self._is_image_url(href)):
                    candidate = href.strip()
                    if not self._is_placeholder_image(candidate):
                        return candidate

        # 2. Check media_content
        media_content = entry.get("media_content", [])
        if media_content:
            for m in media_content:
                url = m.get("url")
                m_type = m.get("type", "")
                if url and ("image" in m_type or self._is_image_url(url)):
                    u = url.strip()
                    if not self._is_placeholder_image(u):
                        return u

        # 3. Check media_thumbnail
        media_thumbnail = entry.get("media_thumbnail", [])
        if media_thumbnail:
            for t in media_thumbnail:
                url = t.get("url")
                if url:
                    u = url.strip()
                    if not self._is_placeholder_image(u):
                        return u

        # 4. Check <img> tag inside summary/content
        content_text = entry.get("summary", "")
        if "content" in entry and entry.content:
            content_text += " " + "".join(c.get("value", "") for c in entry.content)

        if "<img" in content_text:
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content_text, re.IGNORECASE)
            if match:
                img_url = match.group(1).strip()
                if (self._is_image_url(img_url) or img_url.startswith("http")) and not self._is_placeholder_image(img_url):
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
        for el in soup.find_all(["script", "style"]):
            el.decompose()
        for a in soup.find_all("a"):
            a_text = a.get_text().lower()
            if any(w in a_text for w in ["čítaj", "viac", "read more", "celý článok"]):
                a.decompose()
        text = soup.get_text(separator=" ")
        # Unescape HTML entities (&quot;, &amp;, etc.)
        text = html.unescape(text)
        # Collapse multiple spaces and line breaks
        text = re.sub(r"\s+", " ", text).strip()
        return text

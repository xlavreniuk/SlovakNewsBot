import base64
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import requests
from config import AppConfig
from scraper import Article

logger = logging.getLogger(__name__)

# Tags Telegram's HTML parse mode accepts that we keep in captions (with an optional quoted href)
TELEGRAM_TAG_RE = re.compile(
    r"(</?(?:b|strong|i|em|u|ins|s|strike|del|a)(?:\s+href=\"[^\"]*\"|\s+href='[^']*')?\s*/?>)",
    re.IGNORECASE
)


class AIUnavailableError(Exception):
    """Raised when Gemini API is completely unavailable after all retries and model fallbacks."""
    pass


@dataclass
class ScoredArticle:
    article: Article
    score: int
    reason: str


@dataclass
class GeneratedPost:
    article: Article
    headline: str
    caption: str  # HTML formatted text ready for Telegram
    is_duplicate: bool = False  # Same event as a story the channel already posted


class AIEngine:
    """Universal AI content filter, deduplicator, and copywriter powered by Gemini API."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.api_key = config.gemini_api_key.strip().strip("'\"") if config.gemini_api_key else None
        self.candidate_models = self._build_model_candidates(config.ai.model)
        self.active_model = self.candidate_models[0]
        self.temperature = config.ai.temperature
        self.batch_size = config.ai.batch_size
        self.tz = ZoneInfo(config.timezone)

    def _local_dt(self, ts: Optional[float] = None) -> datetime:
        return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).astimezone(self.tz)

    def _now_text(self) -> str:
        """Current local date/time, so the model can judge 'today' vs 'yesterday' and freshness."""
        return self._local_dt().strftime(f"%A, %Y-%m-%d %H:%M ({self.config.timezone})")

    def _published_text(self, article: Article) -> str:
        """Publication time normalized to local time; falls back to the raw feed string."""
        if article.published_timestamp is not None:
            return self._local_dt(article.published_timestamp).strftime("%A, %Y-%m-%d %H:%M")
        return article.published_at or "Unknown"

    def evaluate_batch(
        self,
        articles: List[Article],
        recent_posted_titles: List[str]
    ) -> Tuple[List[ScoredArticle], List[str]]:
        """
        Evaluates a list of candidate articles in batches to score suitability and check for duplicate events.
        Returns a tuple of (qualified_articles, explicitly_rejected_urls).
        Raises AIUnavailableError if Gemini API fails after all retries.
        """
        scored_results: List[ScoredArticle] = []
        rejected_urls: List[str] = []
        if not articles:
            return scored_results, rejected_urls

        for i in range(0, len(articles), self.batch_size):
            chunk = articles[i:i + self.batch_size]
            logger.info("Evaluating batch of %d articles (chunk %d-%d)...", len(chunk), i + 1, i + len(chunk))

            batch_prompt = self._build_batch_prompt(chunk, recent_posted_titles)
            response_json = self._call_gemini_json(batch_prompt)

            if not response_json or not isinstance(response_json, list):
                logger.warning("Batch evaluation returned invalid response for chunk of %d articles. Attempting fallback...", len(chunk))
                # Fallback to single item evaluation for this chunk
                response_json = []
                for item_idx, single_art in enumerate(chunk):
                    single_prompt = self._build_single_eval_prompt(single_art, recent_posted_titles)
                    single_res = self._call_gemini_json(single_prompt)
                    if isinstance(single_res, dict):
                        single_res["id"] = item_idx
                        response_json.append(single_res)
                    time.sleep(1.0)

            if not response_json:
                logger.error("AI evaluation failed for chunk after all fallbacks.")
                raise AIUnavailableError("Gemini API evaluation temporarily unavailable.")

            for item in response_json:
                try:
                    idx = int(item.get("id", -1))
                    if 0 <= idx < len(chunk):
                        article = chunk[idx]
                        score = int(item.get("score", 0))
                        is_duplicate = bool(item.get("is_duplicate", False))
                        reason = str(item.get("reason", ""))

                        if is_duplicate:
                            logger.info("Filtered as duplicate event: '%s'", article.title)
                            rejected_urls.append(article.url)
                            continue

                        if score >= self.config.min_score:
                            logger.info("Qualified article [score=%d]: '%s'", score, article.title)
                            scored_results.append(ScoredArticle(article=article, score=score, reason=reason))
                        else:
                            logger.debug("Filtered out [score=%d]: '%s'", score, article.title)
                            rejected_urls.append(article.url)
                except Exception as e:
                    logger.warning("Error parsing item in batch evaluation: %s", e)

            # Polite pause between batches to respect free tier RPM
            time.sleep(1.5)

        # Sort highest score first
        scored_results.sort(key=lambda x: x.score, reverse=True)
        return scored_results, rejected_urls

    def generate_styled_post(self, article: Article, recent_posted_titles: Optional[List[str]] = None) -> Optional[GeneratedPost]:
        """
        Translates and rewrites an article in the requested target language and style with zero-hallucination validation.
        The same call also checks the story against recently posted titles, so cached scores never cause reposts.
        """
        prompt = self._build_generation_prompt(article, recent_posted_titles or [])
        response_json = self._call_gemini_json(prompt, forced_temperature=0.1)

        if not response_json or not isinstance(response_json, dict):
            logger.error("Failed to generate styled post for '%s'", article.title)
            return None

        if bool(response_json.get("is_duplicate", False)):
            logger.info("Generation step flagged '%s' as the same event as a recent post.", article.title)
            return GeneratedPost(article=article, headline="", caption="", is_duplicate=True)

        headline = str(response_json.get("headline", "")).strip()
        body = str(response_json.get("caption", "")).strip()

        if not body:
            logger.error("Empty caption returned for '%s'", article.title)
            return None

        # Strip any in-text HTML links or 'read more' teasers generated by AI
        body = re.sub(r'<a\s+[^>]*>.*?</a>', '', body)
        body = re.sub(r'(?:Детальніше|Читати далі|Більше деталей|Viac na|Čítajte).*', '', body, flags=re.IGNORECASE).strip()

        # Strict validation against hallucinations and invented numbers
        is_valid, reason = self._validate_post_facts(body, article)
        if not is_valid:
            logger.warning("Factual validation failed for '%s': %s", article.title[:40], reason)
            return None

        # Ensure caption begins with bold headline if not already present
        if headline and not body.startswith("<b>") and headline.lower() not in body.lower()[:len(headline) + 20]:
            body = f"<b>{headline}</b>\n\n{body}"

        # Build final HTML caption with source and channel branding
        caption = self._clean_telegram_html(body)
        caption = self._enforce_max_one_emoji(caption)
        footer = self._build_footer(article)
        if footer:
            caption += f"\n\n{footer}"

        # Check length limit for Telegram
        max_len = self.config.output.max_caption_length
        if len(caption) > max_len:
            logger.warning("Caption length (%d) exceeded max (%d), trimming.", len(caption), max_len)
            caption = self._truncate_html_caption(caption, max_len, article)

        return GeneratedPost(article=article, headline=headline, caption=caption)

    def review_images(self, article: Article, thumbnails: Dict[str, bytes]) -> Optional[List[Dict[str, Any]]]:
        """
        Shows the candidate photos to a vision model together with the story and returns the approved ones,
        best first. Rejects outlet watermarks/logos, burned-in text, stock or illustrative photos, screenshots
        and images unrelated to the story. Returns None when the check could not run (caller decides fallback).
        """
        candidates = [c for c in article.image_candidates if c["url"] in thumbnails]
        if not candidates:
            return []

        story = (article.full_text or article.summary or "")[:700]
        parts: List[Dict[str, Any]] = [{"text": f"""You are the photo editor of a Telegram news channel about Bratislava.
Choose photos for this story:
Title: {article.title}
Text: {story}

For EACH image below (index from 0), judge:
- has_watermark_or_logo: any news outlet / agency / TV logo, site name, watermark or "photo:" credit stamped on the image
- has_text_overlay: headline, caption, banner or other text graphics burned into the image (signs and plates in the scene are fine)
- is_stock_or_illustrative: generic stock / symbolic / illustrative picture (money, handcuffs, gavel, generic police car, posed models) rather than a real photo of this story's place, people or event
- is_screenshot_or_graphic: screenshot, social-media post capture, map, infographic, collage or poster
- relevance: 0-10, how directly the photo shows this story's actual subject, place or event
- quality: 0-10 sharpness, lighting and composition

Respond STRICTLY with a JSON array:
[{{"index": 0, "has_watermark_or_logo": false, "has_text_overlay": false, "is_stock_or_illustrative": false, "is_screenshot_or_graphic": false, "relevance": 8, "quality": 7}}]"""}]
        for idx, cand in enumerate(candidates):
            parts.append({"text": f"Image {idx}:"})
            parts.append({"inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(thumbnails[cand["url"]]).decode("ascii")
            }})

        verdicts = self._call_gemini_json(
            "", forced_temperature=0.0, parts=parts,
            preferred_model=self.config.ai.vision_model or self.active_model
        )
        if not isinstance(verdicts, list):
            logger.warning("Image review unavailable for '%s'", article.title[:40])
            return None

        approved: List[Dict[str, Any]] = []
        for v in verdicts:
            try:
                idx = int(v.get("index", -1))
                if not 0 <= idx < len(candidates):
                    continue
                cand = candidates[idx]
                flags = [k for k in ("has_watermark_or_logo", "has_text_overlay", "is_stock_or_illustrative", "is_screenshot_or_graphic") if v.get(k)]
                relevance = int(v.get("relevance", 0))
                quality = int(v.get("quality", 0))
                if flags or relevance < 5:
                    logger.info("Photo rejected (%s, relevance=%d): %s", ", ".join(flags) or "low relevance", relevance, cand["url"])
                    continue
                approved.append({**cand, "relevance": relevance, "quality": quality})
            except Exception as e:
                logger.debug("Bad image verdict %s: %s", v, e)

        # Prefer relevance, then quality, then resolution; keep the lead (non-gallery) photo first on ties
        approved.sort(key=lambda c: (c["relevance"], c["quality"], c["width"] * c["height"]), reverse=True)
        return approved

    def _validate_post_facts(self, generated_text: str, article: Article) -> Tuple[bool, str]:
        """
        Validates generated text against original source text to prevent hallucinations,
        template placeholder leakage, or invented numbers/statistics.
        """
        # 1. Template placeholder checks
        placeholder_pattern = re.compile(
            r'\[(?:місто|район|вулиця|дата|час|номер|ім\'я|деталі|посилання|ххх|xxx|placeholder)\]',
            re.IGNORECASE
        )
        if placeholder_pattern.search(generated_text):
            return False, "Detected template placeholder leak in output"

        # 2. Check for minimum substance
        clean_text = html.unescape(re.sub(r'<[^>]+>', '', generated_text)).strip()
        if len(clean_text) < 50:
            return False, "Generated post is too brief or empty"

        # 3. Check for hallucinated numbers/statistics
        source_corpus = f"{article.title} {article.full_text or ''} {article.summary or ''} {article.published_at or ''}"
        source_nums = set(re.findall(r'\b\d+\b', source_corpus))

        # Add Slovak written number equivalents
        slovak_number_map = {
            "jeden": "1", "jedna": "1", "jedno": "1", "dva": "2", "dvaja": "2", "dve": "2",
            "tri": "3", "traja": "3", "štyri": "4", "štyria": "4", "päť": "5", "piati": "5",
            "šesť": "6", "sedem": "7", "osem": "8", "deväť": "9", "desať": "10"
        }
        source_lower = source_corpus.lower()
        for word, num in slovak_number_map.items():
            if re.search(rf'\b{word}\b', source_lower):
                source_nums.add(num)

        # Numbers from generated text (excluding footer links)
        gen_nums = set(re.findall(r'\b\d+\b', clean_text))

        # Standard numbers allowed regardless of text (1 emoji rule, current/upcoming years)
        allowed_nums = {"1", "2024", "2025", "2026", "2027"}

        for n in gen_nums:
            if n not in allowed_nums and n not in source_nums:
                return False, f"Hallucinated number '{n}' not found in source text"

        return True, "Valid"

    def _build_footer(self, article: Article) -> str:
        """Constructs footer with original source link and channel branding link."""
        footer_parts = []
        if self.config.output.include_source_link and article.url:
            footer_parts.append(f'<a href="{html.escape(article.url)}">{html.escape(article.source_name, quote=False)}</a>')
        if self.config.output.channel_link:
            name = self.config.output.channel_name or "Братислава"
            footer_parts.append(f'<a href="{html.escape(self.config.output.channel_link)}">{html.escape(name, quote=False)}</a>')
        return " | ".join(footer_parts)

    @staticmethod
    def _enforce_max_one_emoji(text: str) -> str:
        """Enforces that at most one emoji exists across the entire generated text."""
        emoji_pattern = re.compile(
            r'(?:'
            r'[\U0001F600-\U0001F64F]|'
            r'[\U0001F300-\U0001F5FF]|'
            r'[\U0001F680-\U0001F6FF]|'
            r'[\U0001F1E0-\U0001F1FF]|'
            r'[\U0001F900-\U0001F9FF]|'
            r'[\U0001FA00-\U0001FAFF]|'
            r'[\U00002600-\U000027BF]|'
            r'[\U00002300-\U000023FF]|'
            r'[\U00002B00-\U00002BFF]'
            r')(?:[\U0000FE0E\U0000FE0F]|[\U0001F3FB-\U0001F3FF])?(?:\u200D(?:'
            r'[\U0001F600-\U0001F64F]|[\U0001F300-\U0001F5FF]|[\U0001F680-\U0001F6FF]|[\U0001F900-\U0001F9FF]|[\U0001FA00-\U0001FAFF]|[\U00002600-\U000027BF]'
            r')(?:[\U0000FE0E\U0000FE0F]|[\U0001F3FB-\U0001F3FF])?)*',
            flags=re.UNICODE
        )
        matches = list(emoji_pattern.finditer(text))
        if len(matches) <= 1:
            return text
        result = []
        first_end = matches[0].end()
        result.append(text[:first_end])
        last_pos = first_end
        for m in matches[1:]:
            result.append(text[last_pos:m.start()])
            last_pos = m.end()
        result.append(text[last_pos:])
        clean = ''.join(result)
        return re.sub(r'[ \t]{2,}', ' ', clean)

    @staticmethod
    def _clean_telegram_html(raw_html: str) -> str:
        """Sanitizes HTML for Telegram Bot API (converts <br> and <p> to newlines, preserves <b>, <i>, <a>)."""
        if not raw_html:
            return ""
        # Replace br tags with newlines
        text = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
        # Replace p tags with double newlines
        text = re.sub(r"<\s*p[^>]*>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*p\s*>", "\n\n", text, flags=re.IGNORECASE)
        # Strip unsupported tags while keeping text
        # Telegram only allows: b, strong, i, em, u, ins, s, strike, del, span, tg-spoiler, a, tg-emoji, code, pre
        allowed = r"</?(?:b|strong|i|em|u|ins|s|strike|del|a)(?:\s+href=[\"'][^\"']*[\"'])?\s*/?>"
        # Remove any other HTML tag
        # Only things shaped like tags count as tags, so text such as "<5 km" survives (and is escaped below)
        clean = re.sub(r"</?[a-zA-Z][^<>]*>", lambda m: m.group(0) if re.match(allowed, m.group(0), re.IGNORECASE) else "", text)
        # Escape stray &, <, > in text (e.g. "AT&T", "<5 km") so Telegram can parse the entities;
        # unescape first so entities the model already wrote are not double-escaped
        parts = TELEGRAM_TAG_RE.split(clean)
        clean = "".join(
            part if i % 2 else html.escape(html.unescape(part), quote=False)
            for i, part in enumerate(parts)
        )
        # Collapse excessive newlines
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
        return clean

    def _call_gemini_json(
        self,
        prompt: str,
        forced_temperature: Optional[float] = None,
        parts: Optional[List[Dict[str, Any]]] = None,
        preferred_model: Optional[str] = None
    ) -> Optional[Any]:
        """Calls Gemini API with JSON enforcement, safety settings, and exponential backoff retry."""
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set. Cannot call AI engine.")

        temp = forced_temperature if forced_temperature is not None else self.temperature

        # Put the preferred (or last working) model first
        first = preferred_model or self.active_model
        ordered_models = [first] + [m for m in self.candidate_models if m != first]

        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"}
        ]

        for model in ordered_models:
            # Key goes in a header, not the URL, so it can never leak into exception messages or logs
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": parts or [{"text": prompt}]
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": temp
                },
                "safetySettings": safety_settings
            }

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=45 if parts else 25)

                # Retry on 503 (model overloaded)
                if resp.status_code == 503:
                    logger.warning("Model %s returned 503 (overloaded). Waiting 3s before retrying...", model)
                    time.sleep(3)
                    resp = requests.post(url, headers=headers, json=payload, timeout=25)

                # Exponential backoff on 429 (rate limit)
                if resp.status_code == 429:
                    for attempt in range(1, 4):
                        wait_seconds = attempt * 5  # 5s, 10s, 15s
                        logger.warning("Gemini rate limit (429) on %s. Waiting %ds (retry %d/3)...", model, wait_seconds, attempt)
                        time.sleep(wait_seconds)
                        resp = requests.post(url, headers=headers, json=payload, timeout=25)
                        if resp.status_code == 200:
                            break

                if resp.status_code == 200:
                    if preferred_model is None:
                        self.active_model = model
                    result = resp.json()
                    candidates = result.get("candidates", [])
                    if not candidates:
                        logger.warning("Gemini returned empty candidates list (model=%s)", model)
                        continue
                    first_cand = candidates[0]
                    parts = first_cand.get("content", {}).get("parts", [])
                    if not parts:
                        logger.warning(
                            "Gemini returned candidate without content parts (model=%s, finishReason=%s)",
                            model, first_cand.get("finishReason")
                        )
                        continue
                    raw_text = parts[0].get("text", "")
                    parsed = self._parse_json_safe(raw_text)
                    if parsed is not None:
                        return parsed

                if resp.status_code in (404, 503):
                    logger.warning("Model %s returned status %d, trying next candidate...", model, resp.status_code)
                    continue

                logger.error("Gemini API error (model=%s, status=%d): %s", model, resp.status_code, resp.text[:200])
            except Exception as e:
                logger.error("Request to Gemini model %s failed: %s", model, e)

        return None

    def _build_single_eval_prompt(self, article: Article, recent_posted_titles: List[str]) -> str:
        """Prompt to evaluate a single article if batch evaluation fails."""
        snippet = article.summary[:200] if article.summary else ""
        return f"""You are an editorial assistant for a Telegram channel dedicated EXCLUSIVELY to BRATISLAVA, Slovakia.
Channel theme: "{self.config.content_theme}"
Current local date and time: {self._now_text()}

Published: {self._published_text(article)}
Title: {article.title}
Summary: {snippet}

Task:
Evaluate this single article.
- score: 0 to 10 (STRICTLY 0 if not occurring in Bratislava or Bratislava boroughs)
- is_duplicate: false
- reason: one short phrase

Respond with valid JSON:
{{
  "score": 5,
  "is_duplicate": false,
  "reason": "local news"
}}
"""

    def _build_batch_prompt(self, articles: List[Article], recent_posted_titles: List[str]) -> str:
        articles_formatted = []
        for idx, a in enumerate(articles):
            snippet = a.summary[:200] if a.summary else ""
            pub = self._published_text(a)
            articles_formatted.append(
                f"ID: {idx}\nSource: {a.source_name}\nPublished: {pub}\nTitle: {a.title}\nSummary: {snippet}"
            )
        articles_text = "\n\n---\n\n".join(articles_formatted)

        recent_text = (
            "\n".join([f"- {t}" for t in recent_posted_titles[-15:]])
            if recent_posted_titles
            else "None yet"
        )

        return f"""You are an editorial assistant for a Telegram channel dedicated EXCLUSIVELY to BRATISLAVA, Slovakia.
Channel theme:
"{self.config.content_theme}"

CURRENT LOCAL DATE AND TIME: {self._now_text()}
(All "Published" times below are in the same local time zone.)

STRICT LOCATION REQUIREMENT - BRATISLAVA ONLY:
1. The incident/event ITSELF MUST physically take place in Bratislava or its immediate boroughs/districts (Staré Mesto, Petržalka, Ružinov, Nové Mesto, Rača, Dúbravka, Karlova Ves, Devín, Devínska Nová Ves, Vrakuňa, Podunajské Biskupice, Lamač, Záhorská Bystrica, Vajnory, Jarovce, Rusovce, Čunovo, Pezinok, Senec, Malacky).
2. If the story happened in ANY OTHER CITY (for example: Košice, Prešov, Žilina, Banská Bystrica, Nitra, Trnava, Poprad, Martin, or abroad), even if a Bratislava resident or politician was involved or commented, you MUST REJECT IT by setting score = 0 and reason = "Not in Bratislava".

SCORING CRITERIA FOR BRATISLAVA STORIES:
- 0: Not in Bratislava / outside Bratislava region (STRICTLY 0).
- 1-2: Extremely boring / dull bureaucratic announcements, dry administrative notices, obscure procurement tenders, routine statistical tables.
- 3-5: Standard interesting local news: culture, city events, festivals, concerts, weather alerts, transport changes, local improvements, urban life, community stories.
- 6-8: Notable crime blotters, road accidents, police actions, bizarre situations, disputes, funny/viral incidents, fresh breaking events.
- 9-10: Huge, sensational, extraordinary, or viral breaking event in Bratislava.

FRESHNESS & BREAKING NEWS PRIORITY:
- ALWAYS prioritize fresh, breaking events happening TODAY or RECENTLY over older multi-day background recaps. Award +1 to +2 extra points to fresh breaking news (accidents, transport disruptions, crime blotters, today's events).

Recently posted stories on the channel (AVOID DUPLICATE REPORTING OF THE SAME EVENT):
{recent_text}

Here is a batch of new incoming articles:
{articles_text}

Task:
Evaluate each article by its ID.
1. Assign a score from 0 to 10 (STRICTLY 0 if not in Bratislava!).
2. Check `is_duplicate`: Set true if this article covers the exact same event as any of the recently posted stories.
3. Brief reason (one short phrase).

Respond STRICTLY with a valid JSON array of objects:
[
  {{
    "id": 0,
    "score": 8,
    "is_duplicate": false,
    "reason": "Petržalka car arsonist"
  }}
]
"""

    def _build_generation_prompt(self, article: Article, recent_posted_titles: List[str]) -> str:
        recent_text = "\n".join(f"- {t}" for t in recent_posted_titles[-20:]) or "None yet"
        source_content = (
            article.full_text
            if (article.full_text and len(article.full_text.strip()) > 80)
            else article.summary
        )
        return f"""You are a professional copywriter for a Telegram news channel dedicated EXCLUSIVELY to BRATISLAVA.
Adapt this Bratislava news article into natural, flawless {self.config.output.target_language}.

CURRENT LOCAL DATE AND TIME: {self._now_text()}

VERIFIED SOURCE ARTICLE (READ CAREFULLY):
Source: {article.source_name}
Title: {article.title}
Published (local time): {self._published_text(article)}
Text:
{source_content}

STYLE AND TONE GUIDE:
{self.config.output.style_guide}

CRITICAL ZERO-HALLUCINATION & FACTUAL PRECISION RULES:
1. 100% FACTUAL GROUNDING: Every single fact, name, street name, route/line number, date, time, cause of incident, casualty count, and outcome in your summary MUST be explicitly stated in the source text above.
2. ZERO INVENTED DETAILS: If a detail (such as the route number, name of victim/driver, hospital name, or exact cause of delay) is NOT mentioned in the source, DO NOT GUESS OR INVENT IT. Do not fill gaps with assumed knowledge.
3. CONCISE ACCURACY OVER FILLER: If the article is brief, write a crisp 2-sentence summary of ONLY what is known. Never pad the post with speculative filler.
4. STRICT NUMBER & DATE FIDELITY: Never modify, invent, or approximate any numbers, monetary sums, statistics, or dates.
5. NO LINKS OR PROMOS: Never generate any <a href="..."> links in the body and never write "Детальніше читайте..." or call-to-action text.
6. LOCATION PRECISION:
   - Identify the specific Bratislava borough or street ONLY if explicitly mentioned in the source text.
   - Always decline Bratislava locations accurately according to standard Ukrainian grammar:
     * 'у Братиславі' (not 'в Братиславе')
     * 'у Петржалці' (not 'в Петржалке', 'в Петржялці')
     * 'у Ружинові' (not 'в Ружинове')
     * 'у Старому місті' (not 'в Старе Место')
     * 'у Новому місті' (Nové Mesto)
     * 'у Дубравці' (Dúbravka)
     * 'у Карловій Весі' (Karlova Ves)
     * 'у Рачі' (Rača)
     * 'у Ламачі' (Lamač)
     * 'у Девіні' (Devín)
     * 'у Вракуні' (Vrakuňa)
     * 'у Подунайських Біскупицях' (Podunajské Biskupice)
     * 'на Золотих пісках' (Zlaté piesky)
     * 'на Набережній / Дунайській набережній' (Nábrežie)
     * 'на Головному вокзалі' (Hlavná stanica)
7. TERMINOLOGY & TRANSLATION QUALITY:
   - 'medici' in Slovak means 'студенти-медики' (medical students), NOT practicing doctors.
   - Fully translate Slovak institutional terms into standard Ukrainian:
     * 'Dopravný podnik Bratislava (DPB)' -> 'транспортне підприємство Братислави (DPB)'
     * 'magistrát / mestské zastupiteľstvo' -> 'мерія / магістрат / міська рада Братислави'
     * 'mestská polícia' -> 'муніципальна поліція'
     * 'krajské riaditeľstvo PZ' -> 'поліція Братиславського краю'
     * 'hasiči' -> 'рятувальники' or 'пожежники'
     * 'nehoda' -> 'аварія' or 'ДТП'
     * 'vodič' -> 'водій'
     * 'záchranári' -> 'медики' or 'лікарі швидкої'
   - Eliminate any literal Slovakisms or calques. Maintain Ukrainian phonetic euphony (чергування у/в, і/й).
8. TEMPORAL GROUNDING:
   - Compare the publication time ({self._published_text(article)}) and any day mentioned in the text with the CURRENT LOCAL DATE AND TIME above.
   - If the event occurred on a specific day or time (e.g. 'у середу', 'у п'ятницю вранці', 'цими вихідними'), state it accurately.
   - Relative words in the source ('dnes', 'včera', 'zajtra') refer to the PUBLICATION date, not to now: re-anchor them to the current date.
   - Say 'сьогодні' only if the event happens on the current local date; never if it happened yesterday or earlier.
9. EMOJI RULE:
   - Use at most ONE relevant emoji in the entire post (e.g. 📍, 🏛️, 🚗, ⚠️ at the start of the headline, or no emoji).
   - Never use multiple emojis or informal emoji clutter.
10. LENGTH:
   - Total length of caption must be under {self.config.output.max_caption_length - 100} characters.

RECENTLY POSTED STORIES ON THE CHANNEL:
{recent_text}
Set "is_duplicate" to true ONLY if this article reports the same specific event as one of those stories
(a genuinely new development of that event, e.g. an arrest after an earlier robbery report, is NOT a duplicate).

Return STRICTLY a JSON object with this structure:
{{
  "is_duplicate": false,
  "headline": "Short punchy title without HTML tags",
  "caption": "Full formatted post in HTML ready for Telegram (beginning with <b>headline</b> followed by empty line and 2-3 body sentences)"
}}
"""

    def _truncate_html_caption(self, caption: str, max_len: int, article: Article) -> str:
        """Safely trims text if it exceeds maximum length, preserving footer links."""
        footer = self._build_footer(article)
        footer_tag = f"\n\n{footer}" if footer else ""
        allowed_body = max_len - len(footer_tag) - 10
        if allowed_body <= 50:
            return caption[:max_len]

        body = caption[:-len(footer_tag)] if footer_tag and caption.endswith(footer_tag) else caption

        # Keep the bold headline intact, then trim the rest as plain text so no tag or entity gets cut in half
        headline = ""
        m = re.match(r"\s*<b>(.*?)</b>", body, re.DOTALL)
        if m and len(m.group(0)) < allowed_body // 2:
            headline = m.group(0).strip()
            body = body[m.end():]
        clean = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
        budget = allowed_body - len(headline) - 2
        cut = budget
        trimmed = html.escape(clean[:cut].rstrip(), quote=False) + "..."
        # Escaping lengthens text ("&" -> "&amp;"), so shrink until the escaped body fits
        while len(trimmed) > budget and cut > 0:
            cut -= len(trimmed) - budget
            trimmed = html.escape(clean[:max(cut, 0)].rstrip(), quote=False) + "..."
        result = f"{headline}\n\n{trimmed}" if headline else trimmed
        return f"{result}{footer_tag}"

    @staticmethod
    def _parse_json_safe(text: str) -> Optional[Any]:
        if not text:
            return None
        text = text.strip()
        # Remove markdown fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()

        try:
            return json.loads(text)
        except Exception:
            # Try to locate JSON array or object
            array_match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
            if array_match:
                try:
                    return json.loads(array_match.group(0))
                except Exception:
                    pass
            obj_match = re.search(r"\{.*\}", text, re.DOTALL)
            if obj_match:
                try:
                    return json.loads(obj_match.group(0))
                except Exception:
                    pass
            logger.warning("Could not parse JSON from text: %s", text[:200])
            return None

    @staticmethod
    def _build_model_candidates(preferred: str) -> List[str]:
        standard = [
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3.5-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest"
        ]
        candidates = [preferred] if preferred else []
        for m in standard:
            if m not in candidates:
                candidates.append(m)
        return candidates

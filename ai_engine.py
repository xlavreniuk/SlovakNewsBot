import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import requests
from config import AppConfig
from scraper import Article

logger = logging.getLogger(__name__)


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


class AIEngine:
    """Universal AI content filter, deduplicator, and copywriter powered by Gemini API."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.api_key = config.gemini_api_key.strip().strip("'\"") if config.gemini_api_key else None
        self.candidate_models = self._build_model_candidates(config.ai.model)
        self.active_model = self.candidate_models[0]
        self.temperature = config.ai.temperature
        self.batch_size = config.ai.batch_size

    def evaluate_batch(
        self,
        articles: List[Article],
        recent_posted_titles: List[str]
    ) -> Tuple[List[ScoredArticle], List[str]]:
        """
        Evaluates a list of candidate articles in batches to score suitability and check for duplicate events.
        Returns a tuple of (qualified_articles, explicitly_rejected_urls).
        Raises RuntimeError if Gemini API fails to prevent discarding un-evaluated articles.
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
                logger.error("Batch evaluation returned invalid response from Gemini API. Aborting batch to protect candidates.")
                raise RuntimeError("Gemini API batch evaluation failed. Aborting to avoid discarding candidates.")

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

    def generate_styled_post(self, article: Article) -> Optional[GeneratedPost]:
        """Translates and rewrites an article in the requested target language and style."""
        prompt = self._build_generation_prompt(article)
        response_json = self._call_gemini_json(prompt)

        if not response_json or not isinstance(response_json, dict):
            logger.error("Failed to generate styled post for '%s'", article.title)
            return None

        headline = str(response_json.get("headline", "")).strip()
        body = str(response_json.get("caption", "")).strip()

        if not body:
            logger.error("Empty caption returned for '%s'", article.title)
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

    def _build_footer(self, article: Article) -> str:
        """Constructs footer with original source link and channel branding link."""
        footer_parts = []
        if self.config.output.include_source_link and article.url:
            footer_parts.append(f'<a href="{article.url}">{article.source_name}</a>')
        if self.config.output.channel_link:
            name = self.config.output.channel_name or "Братислава"
            footer_parts.append(f'<a href="{self.config.output.channel_link}">{name}</a>')
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
        clean = re.sub(r"<[^>]+>", lambda m: m.group(0) if re.match(allowed, m.group(0), re.IGNORECASE) else "", text)
        # Collapse excessive newlines
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
        return clean

    def _call_gemini_json(self, prompt: str) -> Optional[Any]:
        """Calls Gemini API with JSON enforcement, handling model fallbacks."""
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set. Cannot call AI engine.")

        # Put active model first
        ordered_models = [self.active_model] + [m for m in self.candidate_models if m != self.active_model]

        for model in ordered_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}]
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": self.temperature
                }
            }

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=20)
                if resp.status_code == 200:
                    self.active_model = model
                    result = resp.json()
                    raw_text = (
                        result.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    )
                    return self._parse_json_safe(raw_text)

                if resp.status_code in (404, 503):
                    logger.warning("Model %s returned status %d, trying next candidate...", model, resp.status_code)
                    continue

                if resp.status_code == 429:
                    logger.warning("Gemini rate limit (429) hit. Waiting 4s before retrying...")
                    time.sleep(4)
                    resp = requests.post(url, headers=headers, json=payload, timeout=20)
                    if resp.status_code == 200:
                        self.active_model = model
                        result = resp.json()
                        raw_text = (
                            result.get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [{}])[0]
                            .get("text", "")
                        )
                        return self._parse_json_safe(raw_text)

                logger.error("Gemini API error (model=%s, status=%d): %s", model, resp.status_code, resp.text)
            except Exception as e:
                logger.error("Request to Gemini model %s failed: %s", model, e)

        return None

    def _build_batch_prompt(self, articles: List[Article], recent_posted_titles: List[str]) -> str:
        articles_formatted = []
        for idx, a in enumerate(articles):
            snippet = a.summary[:200] if a.summary else ""
            articles_formatted.append(
                f"ID: {idx}\nSource: {a.source_name}\nTitle: {a.title}\nSummary: {snippet}"
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

STRICT LOCATION REQUIREMENT - BRATISLAVA ONLY:
1. The story MUST take place in Bratislava or its immediate boroughs/districts (Staré Mesto, Petržalka, Ružinov, Nové Mesto, Rača, Dúbravka, Karlova Ves, Devín, Devínska Nová Ves, Vrakuňa, Podunajské Biskupice, Lamač, Záhorská Bystrica, Vajnory, Jarovce, Rusovce, Čunovo, Pezinok, Senec, Malacky).
2. If the story happened in ANY OTHER CITY (for example: Košice, Prešov, Žilina, Banská Bystrica, Nitra, Trnava, Poprad, Martin, or abroad), you MUST REJECT IT by setting score = 0 and reason = "Not in Bratislava".

SCORING CRITERIA FOR BRATISLAVA STORIES:
- 0: Not in Bratislava / outside Bratislava region (STRICTLY 0).
- 1-2: Extremely boring / dull bureaucratic announcements, dry administrative notices, obscure procurement tenders, routine statistical tables.
- 3-5: Standard interesting local news: culture, city events, festivals, concerts, weather alerts, transport changes, local improvements, urban life, community stories.
- 6-8: Notable crime blotters, road accidents, police actions, bizarre situations, disputes, funny/viral incidents.
- 9-10: Huge, sensational, extraordinary, or viral event in Bratislava.

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

    def _build_generation_prompt(self, article: Article) -> str:
        return f"""You are a professional copywriter for a Telegram news channel dedicated EXCLUSIVELY to BRATISLAVA.
Adapt this Bratislava news article into natural, flawless {self.config.output.target_language}.

ARTICLE DETAILS:
Source: {article.source_name}
Title: {article.title}
Published: {article.published_at or 'Recently'}
Summary: {article.summary}

STYLE AND TONE GUIDE:
{self.config.output.style_guide}

STRICT FORMATTING, GRAMMAR, AND FACTUAL REQUIREMENTS:
1. Output language: Flawless, natural {self.config.output.target_language}.
2. Use valid HTML for formatting (<b>bold</b>, <i>italic</i>). Do NOT use Markdown (no asterisks, no hash headers).
3. The post must start with a concise bold headline (<b>...</b>) with at most 1 relevant emoji (or no emoji).
4. LOCATION & DECLENSIONS IN UKRAINIAN:
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
5. TERMINOLOGY & TRANSLATION QUALITY:
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
6. TEMPORAL GROUNDING:
   - Check the publication time ({article.published_at or 'recently'}).
   - If the event occurred on a specific day or time (e.g. 'у середу', 'у п'ятницю вранці', 'цими вихідними'), state it accurately.
   - Do NOT say 'сьогодні' if the story happened yesterday or earlier.
7. EMOJI RULE:
   - Use at most ONE relevant emoji in the entire post (e.g. 📍, 🏛️, 🚗, ⚠️ at the start of the headline, or no emoji).
   - Never use multiple emojis or informal emoji clutter.
8. TONE:
   - Informative, professional, calm, but simple and easily understandable for the average reader.
   - Avoid tabloid sensationalism, vulgarity, sarcasm, or trash clickbait.
9. LENGTH:
   - Total length of caption must be under {self.config.output.max_caption_length - 100} characters.
10. STRICT FACTUAL INTEGRITY:
   - Base the summary 100% on the facts reported in the source. NEVER invent details, do NOT exaggerate facts into falsehoods, and do NOT make up quotes or unverified rumors. Real news only.

Return STRICTLY a JSON object with this structure:
{{
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

        # Strip html tags before slicing to avoid malformed HTML
        clean = re.sub(r"<[^>]+>", "", caption)
        trimmed = clean[:allowed_body] + "..."
        return f"<b>{trimmed}</b>{footer_tag}"

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
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
            "gemini-flash-latest"
        ]
        candidates = [preferred] if preferred else []
        for m in standard:
            if m not in candidates:
                candidates.append(m)
        return candidates

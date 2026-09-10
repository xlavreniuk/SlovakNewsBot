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
        self.api_key = config.gemini_api_key
        self.candidate_models = self._build_model_candidates(config.ai.model)
        self.active_model = self.candidate_models[0]
        self.temperature = config.ai.temperature
        self.batch_size = config.ai.batch_size

    def evaluate_batch(
        self,
        articles: List[Article],
        recent_posted_titles: List[str]
    ) -> List[ScoredArticle]:
        """
        Evaluates a list of candidate articles in batches to score suitability and check for duplicate events.
        Saves API quota and respects the 15 RPM free-tier limit.
        """
        scored_results: List[ScoredArticle] = []
        if not articles:
            return scored_results

        for i in range(0, len(articles), self.batch_size):
            chunk = articles[i:i + self.batch_size]
            logger.info("Evaluating batch of %d articles (chunk %d-%d)...", len(chunk), i + 1, i + len(chunk))

            batch_prompt = self._build_batch_prompt(chunk, recent_posted_titles)
            response_json = self._call_gemini_json(batch_prompt)

            if not response_json or not isinstance(response_json, list):
                logger.warning("Batch evaluation returned invalid JSON response, skipping chunk.")
                continue

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
                            continue

                        if score >= self.config.min_score:
                            logger.info("Qualified article [score=%d]: '%s'", score, article.title)
                            scored_results.append(ScoredArticle(article=article, score=score, reason=reason))
                        else:
                            logger.debug("Filtered out [score=%d]: '%s'", score, article.title)
                except Exception as e:
                    logger.warning("Error parsing item in batch evaluation: %s", e)

            # Polite pause between batches to respect free tier RPM
            time.sleep(1.5)

        # Sort highest craziness/theme score first
        scored_results.sort(key=lambda x: x.score, reverse=True)
        return scored_results

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

        # Build final HTML caption
        caption = body
        if self.config.output.include_source_link and article.url:
            source_tag = f'\n\n👉 <a href="{article.url}">{article.source_name}</a>'
            caption += source_tag

        # Check length limit for Telegram
        max_len = self.config.output.max_caption_length
        if len(caption) > max_len:
            logger.warning("Caption length (%d) exceeded max (%d), trimming.", len(caption), max_len)
            caption = self._truncate_html_caption(caption, max_len, article)

        return GeneratedPost(article=article, headline=headline, caption=caption)

    def _call_gemini_json(self, prompt: str) -> Optional[Any]:
        """Calls Gemini API with JSON enforcement, handling model fallbacks."""
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set. Cannot call AI engine.")

        for model in self.candidate_models:
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
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
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

                if resp.status_code == 404:
                    logger.warning("Model %s returned 404 Not Found, trying next candidate...", model)
                    continue

                if resp.status_code == 429:
                    logger.warning("Gemini rate limit (429) hit. Waiting 5s before retrying...")
                    time.sleep(5)
                    resp = requests.post(url, headers=headers, json=payload, timeout=30)
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

        return f"""You are an editorial assistant for a Telegram channel monitoring news from {self.config.country}.
Our channel specifically seeks stories fitting this theme:
"{self.config.content_theme}"

We want to filter out routine news (boring politics, standard business/finance, scheduled weather forecasts, generic sports results, regular announcements).
We only want wild, bizarre, funny, shocking, tabloid, or extraordinary human-interest incidents and local crazy events.

Recently posted stories on the channel (AVOID DUPLICATE REPORTING OF THE SAME EVENT):
{recent_text}

Here is a batch of new incoming articles from {self.config.country}:
{articles_text}

Task:
Evaluate each article by its ID.
1. Assign a score from 1 to 10:
   - 1-4: Routine, ordinary, political statements, business, sports match results, dull news.
   - 5-6: Mildly interesting local news, but nothing extraordinary.
   - 7-8: Genuinely crazy, funny, bizarre, shocking tabloid crime, bizarre human incident, or viral occurrence.
   - 9-10: Mind-blowing, unbelievable, absurd tabloid event.
2. Check `is_duplicate`: Set true if this article covers the exact same event as any of the recently posted stories.
3. Brief reason (one short phrase).

Respond STRICTLY with a valid JSON array of objects:
[
  {{
    "id": 0,
    "score": 8,
    "is_duplicate": false,
    "reason": "Drunk driver in shopping cart"
  }}
]
"""

    def _build_generation_prompt(self, article: Article) -> str:
        return f"""You are a professional copywriter for a Telegram news channel.
Adapt this {self.config.country} news article into {self.config.output.target_language}.

ARTICLE DETAILS:
Source: {article.source_name}
Title: {article.title}
Summary: {article.summary}

STYLE AND TONE GUIDE:
{self.config.output.style_guide}

STRICT FORMATTING REQUIREMENTS:
1. Output language: {self.config.output.target_language}.
2. Use valid HTML for formatting (<b>bold</b>, <i>italic</i>). Do NOT use Markdown (no asterisks, no hash headers).
3. The post must be catchy, punchy, engaging, and clear.
4. Total length of caption must be under {self.config.output.max_caption_length - 100} characters.

Return STRICTLY a JSON object with this structure:
{{
  "headline": "Short punchy title",
  "caption": "Full formatted text in HTML ready for Telegram"
}}
"""

    @staticmethod
    def _truncate_html_caption(caption: str, max_len: int, article: Article) -> str:
        """Safely trims text if it exceeds maximum length, preserving link."""
        source_tag = f'\n\n👉 <a href="{article.url}">{article.source_name}</a>'
        allowed_body = max_len - len(source_tag) - 10
        if allowed_body <= 50:
            return caption[:max_len]

        # Strip html tags before slicing to avoid malformed HTML
        clean = re.sub(r"<[^>]+>", "", caption)
        trimmed = clean[:allowed_body] + "..."
        return f"<b>{trimmed}</b>{source_tag}"

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
        standard = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
        candidates = [preferred] if preferred else []
        for m in standard:
            if m not in candidates:
                candidates.append(m)
        return candidates

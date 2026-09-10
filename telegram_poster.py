import html
import logging
import re
import time
from typing import Optional
import requests

logger = logging.getLogger(__name__)


class TelegramPoster:
    """Dispatches formatted articles to a Telegram channel with photo support and fallback resilience."""

    def __init__(self, bot_token: str, channel_id: str, timeout: int = 15):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.timeout = timeout
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def post(self, caption: str, image_url: Optional[str] = None) -> bool:
        """
        Posts article to Telegram.
        Tries sendPhoto if an image is provided; falls back to sendMessage on failure.
        """
        clean_caption = self._sanitize_html(caption)

        if image_url:
            success = self._send_photo(image_url, clean_caption)
            if success:
                return True
            logger.warning("sendPhoto failed or rejected image. Falling back to sendMessage.")

        return self._send_message(clean_caption)

    @staticmethod
    def _sanitize_html(text: str) -> str:
        """Converts <br> and <p> into real newlines for Telegram parse_mode='HTML'."""
        if not text:
            return ""
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*p[^>]*>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*p\s*>", "\n\n", text, flags=re.IGNORECASE)
        return text.strip()

    def _send_photo(self, photo_url: str, caption: str) -> bool:
        url = f"{self.base_url}/sendPhoto"
        payload = {
            "chat_id": self.channel_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML"
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            data = resp.json()
            if data.get("ok"):
                logger.info("Successfully posted photo message to %s", self.channel_id)
                return True
            logger.warning("Telegram sendPhoto error (%d): %s", resp.status_code, data.get("description"))
        except Exception as e:
            logger.warning("Network exception during sendPhoto: %s", e)
        return False

    def _send_message(self, text: str) -> bool:
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.channel_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            data = resp.json()
            if data.get("ok"):
                logger.info("Successfully posted text message to %s", self.channel_id)
                return True

            # If HTML parsing failed, strip tags and send as plain text
            if "can't parse entities" in data.get("description", "").lower():
                logger.warning("Telegram failed to parse HTML. Stripping tags and retrying as plain text...")
                plain_text = re.sub(r"<[^>]+>", "", text)
                payload["text"] = plain_text
                del payload["parse_mode"]
                retry_resp = requests.post(url, json=payload, timeout=self.timeout)
                if retry_resp.json().get("ok"):
                    logger.info("Successfully posted plain text fallback to %s", self.channel_id)
                    return True

            logger.error("Telegram sendMessage failed (%d): %s", resp.status_code, data.get("description"))
        except Exception as e:
            logger.error("Network exception during sendMessage: %s", e)
        return False

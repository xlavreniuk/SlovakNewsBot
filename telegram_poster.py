import html
import io
import logging
import re
import time
from typing import Optional
from PIL import Image, ImageOps
import requests

logger = logging.getLogger(__name__)


class TelegramPoster:
    """Dispatches formatted articles to a Telegram channel with photo support and fallback resilience."""

    def __init__(self, bot_token: str, channel_id: str, timeout: int = 15):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.timeout = timeout
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def post(self, caption: str, image_url: Optional[str] = None, require_image: bool = True) -> bool:
        """
        Posts article to Telegram.
        If require_image is True, it MUST succeed as sendPhoto, and will NEVER fall back to plain text.
        """
        clean_caption = self._sanitize_html(caption)

        if image_url:
            success = self._send_photo(image_url, clean_caption)
            if success:
                return True
            logger.warning("sendPhoto failed or image was inaccessible.")
            if require_image:
                logger.error("require_image is True: refusing to fall back to plain text.")
                return False

        if require_image:
            logger.error("No image provided and require_image is True: refusing to post plain text.")
            return False

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
        try:
            # Download image bytes directly using browser headers to bypass CDN blocks and verify valid image
            img_resp = requests.get(
                photo_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10
            )
            if img_resp.status_code == 200 and len(img_resp.content) > 3000:
                upload_bytes = img_resp.content
                filename = "photo.jpg"
                mime_type = "image/jpeg"

                try:
                    img = Image.open(io.BytesIO(img_resp.content))
                    img = ImageOps.exif_transpose(img)
                    logger.info("Image verified: %dx%d (%s, %d bytes) for Telegram upload", img.width, img.height, img.format, len(upload_bytes))

                    if img.width < 500 or img.height < 300:
                        logger.error("Image resolution (%dx%d) below acceptable quality threshold (500x300)! Refusing upload.", img.width, img.height)
                        return False

                    # Preserve PNG or convert to highest-quality sRGB JPEG (quality=95, subsampling=0)
                    if img.format == "PNG" and img.mode in ("RGB", "RGBA"):
                        filename = "photo.png"
                        mime_type = "image/png"
                    else:
                        out_io = io.BytesIO()
                        rgb_img = img.convert("RGB")
                        rgb_img.save(out_io, format="JPEG", quality=95, subsampling=0, optimize=True)
                        upload_bytes = out_io.getvalue()
                        filename = "photo.jpg"
                        mime_type = "image/jpeg"
                except Exception as e:
                    logger.warning("Pillow processing error: %s", e)
                    return False

                files = {"photo": (filename, upload_bytes, mime_type)}
                data = {
                    "chat_id": self.channel_id,
                    "caption": caption,
                    "parse_mode": "HTML"
                }
                resp = requests.post(url, data=data, files=files, timeout=self.timeout)
                res_data = resp.json()
                if res_data.get("ok"):
                    logger.info("Successfully uploaded and posted photo message to %s", self.channel_id)
                    return True
                logger.warning("Telegram sendPhoto upload error (%d): %s", resp.status_code, res_data.get("description"))
        except Exception as e:
            logger.warning("Exception during photo download/upload: %s", e)

        # Fallback to direct URL if download failed
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
                logger.info("Successfully posted photo message via URL to %s", self.channel_id)
                return True
            logger.warning("Telegram sendPhoto URL error (%d): %s", resp.status_code, data.get("description"))
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

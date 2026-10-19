import html
import io
import json
import logging
import re
import time
from typing import List, Optional, Tuple
from PIL import Image, ImageOps
import requests

logger = logging.getLogger(__name__)


class TelegramPoster:
    """Dispatches formatted articles to a Telegram channel with photo/album support and fallback resilience."""

    def __init__(self, bot_token: str, channel_id: str, timeout: int = 15):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.timeout = timeout
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.parse_mode: Optional[str] = "HTML"
        self.silent = False
        self._parse_error = False

    def post(
        self,
        caption: str,
        image_url: Optional[str] = None,
        image_urls: Optional[List[str]] = None,
        require_image: bool = True,
        silent: bool = False
    ) -> bool:
        """
        Posts article to Telegram.
        Supports multi-image albums (sendMediaGroup) when image_urls has >= 2 items.
        If require_image is True, it MUST succeed as photo/album, and will NEVER fall back to plain text.
        """
        clean_caption = self._sanitize_html(caption)
        self.parse_mode = "HTML"
        self.silent = silent
        self._parse_error = False
        if self._post_once(clean_caption, image_url, image_urls, require_image):
            return True
        if self._parse_error:
            # Telegram rejected the HTML entities: resend once as plain text instead of losing the story
            logger.warning("Telegram could not parse caption HTML. Retrying with plain-text caption...")
            self.parse_mode = None
            plain_caption = html.unescape(re.sub(r"<[^>]+>", "", clean_caption))
            try:
                return self._post_once(plain_caption, image_url, image_urls, require_image)
            finally:
                self.parse_mode = "HTML"
        return False

    def _note_error(self, description: Optional[str]) -> None:
        if description and "can't parse entities" in description.lower():
            self._parse_error = True

    def _with_options(self, payload: dict) -> dict:
        """Adds parse mode and (during quiet hours) silent delivery to a request payload."""
        if self.parse_mode:
            payload["parse_mode"] = self.parse_mode
        if self.silent:
            payload["disable_notification"] = True
        return payload

    def _post_once(
        self,
        clean_caption: str,
        image_url: Optional[str],
        image_urls: Optional[List[str]],
        require_image: bool
    ) -> bool:
        # Consolidate candidate image URLs
        all_images: List[str] = []
        if image_urls:
            all_images.extend([u for u in image_urls if u])
        if not all_images and image_url:
            all_images.append(image_url)

        if len(all_images) >= 2:
            success = self._send_media_group(all_images, clean_caption)
            if success:
                return True
            logger.warning("sendMediaGroup failed, attempting single photo fallback...")
            success = self._send_photo(all_images[0], clean_caption)
            if success:
                return True

        elif len(all_images) == 1:
            success = self._send_photo(all_images[0], clean_caption)
            if success:
                return True
            logger.warning("sendPhoto failed or image was inaccessible.")

        if require_image:
            logger.error("No valid image/album posted and require_image is True: refusing to post plain text.")
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

    def _prepare_photo(self, photo_url: str) -> Optional[Tuple[str, bytes, str]]:
        """Downloads, verifies, and optimizes an image for Telegram upload."""
        try:
            img_resp = requests.get(
                photo_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10
            )
            if img_resp.status_code == 200 and len(img_resp.content) > 3000:
                upload_bytes = img_resp.content
                filename = "photo.jpg"
                mime_type = "image/jpeg"

                img = Image.open(io.BytesIO(img_resp.content))
                img = ImageOps.exif_transpose(img)

                if img.width < 500 or img.height < 300:
                    logger.warning("Image resolution (%dx%d) below acceptable quality threshold! Skipping.", img.width, img.height)
                    return None

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

                return (filename, upload_bytes, mime_type)
        except Exception as e:
            logger.warning("Error preparing photo %s: %s", photo_url, e)
        return None

    def _send_photo(self, photo_url: str, caption: str) -> bool:
        url = f"{self.base_url}/sendPhoto"
        prep = self._prepare_photo(photo_url)
        if prep:
            filename, upload_bytes, mime_type = prep
            files = {"photo": (filename, upload_bytes, mime_type)}
            data = self._with_options({
                "chat_id": self.channel_id,
                "caption": caption
            })
            # Multipart form fields are strings; the Bot API expects lowercase "true"
            data = {k: ("true" if v is True else v) for k, v in data.items()}
            try:
                resp = requests.post(url, data=data, files=files, timeout=self.timeout)
                res_data = resp.json()
                if res_data.get("ok"):
                    logger.info("Successfully uploaded and posted photo message to %s", self.channel_id)
                    return True
                logger.warning("Telegram sendPhoto upload error (%d): %s", resp.status_code, res_data.get("description"))
                self._note_error(res_data.get("description"))
                if self._parse_error:
                    return False
            except Exception as e:
                logger.warning("Exception during photo upload: %s", e)

        # Fallback to direct URL if upload failed
        payload = self._with_options({
            "chat_id": self.channel_id,
            "photo": photo_url,
            "caption": caption
        })
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            data = resp.json()
            if data.get("ok"):
                logger.info("Successfully posted photo message via URL to %s", self.channel_id)
                return True
            logger.warning("Telegram sendPhoto URL error (%d): %s", resp.status_code, data.get("description"))
            self._note_error(data.get("description"))
        except Exception as e:
            logger.warning("Network exception during sendPhoto: %s", e)
        return False

    def _send_media_group(self, photo_urls: List[str], caption: str) -> bool:
        """Dispatches a multi-photo album to Telegram using sendMediaGroup."""
        url = f"{self.base_url}/sendMediaGroup"
        prepared: List[Tuple[str, bytes, str]] = []
        for u in photo_urls[:10]:
            p = self._prepare_photo(u)
            if p:
                prepared.append(p)

        if len(prepared) < 2:
            logger.info("Fewer than 2 valid photos for media group (got %d), falling back to single photo.", len(prepared))
            return False

        media = []
        files = {}
        for idx, (filename, upload_bytes, mime_type) in enumerate(prepared):
            attach_id = f"photo_{idx}"
            item: dict = {
                "type": "photo",
                "media": f"attach://{attach_id}"
            }
            if idx == 0:
                item["caption"] = caption
                if self.parse_mode:
                    item["parse_mode"] = self.parse_mode
            media.append(item)
            files[attach_id] = (filename, upload_bytes, mime_type)

        data = {
            "chat_id": self.channel_id,
            "media": json.dumps(media)
        }
        if self.silent:
            data["disable_notification"] = "true"

        try:
            resp = requests.post(url, data=data, files=files, timeout=self.timeout * 2)
            res_data = resp.json()
            if res_data.get("ok"):
                logger.info("Successfully uploaded and posted %d-photo album to %s", len(prepared), self.channel_id)
                return True
            logger.warning("Telegram sendMediaGroup error (%d): %s", resp.status_code, res_data.get("description"))
            self._note_error(res_data.get("description"))
        except Exception as e:
            logger.warning("Exception during sendMediaGroup: %s", e)

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

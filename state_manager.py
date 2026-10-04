import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class StateManager:
    """Manages persistent deduplication state (seen URLs, seen images, and recently posted story titles)."""

    def __init__(self, filepath: str = "seen_urls.json", retention_days: int = 14):
        self.filepath = filepath
        self.retention_days = retention_days
        self.seen_urls: Dict[str, str] = {}  # url -> ISO timestamp
        self.seen_images: Dict[str, str] = {}  # canonical image key -> ISO timestamp
        self.seen_hashes: Dict[str, str] = {}  # 16-hex perceptual dHash -> ISO timestamp
        self.recent_posts: List[Dict[str, str]] = []  # list of {url, title, posted_at}
        self._dirty = False
        self.load()

    def load(self) -> None:
        """Loads state from file. Handles both dict and legacy list formats."""
        if not os.path.exists(self.filepath):
            logger.info("No existing state file found at %s. Starting fresh.", self.filepath)
            return

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                # Backward compatibility: legacy format was a simple list of URLs
                now_iso = datetime.now(timezone.utc).isoformat()
                self.seen_urls = {url: now_iso for url in data if isinstance(url, str)}
                self.seen_images = {}
                self.seen_hashes = {}
                self.recent_posts = []
            elif isinstance(data, dict):
                self.seen_urls = data.get("seen_urls", {})
                self.seen_images = data.get("seen_images", {})
                self.seen_hashes = data.get("seen_hashes", {})
                self.recent_posts = data.get("recent_posts", [])
            else:
                logger.warning("Unrecognized format in %s, resetting state.", self.filepath)
                self.seen_urls = {}
                self.seen_images = {}
                self.seen_hashes = {}
                self.recent_posts = []

            logger.info(
                "Loaded state: %d seen URLs, %d seen images, %d seen hashes, %d recent posts.",
                len(self.seen_urls), len(self.seen_images), len(self.seen_hashes), len(self.recent_posts)
            )
        except Exception as e:
            logger.error("Failed to load state from %s: %s. Starting fresh.", self.filepath, e)
            self.seen_urls = {}
            self.seen_images = {}
            self.seen_hashes = {}
            self.recent_posts = []

    def is_seen(self, url: str) -> bool:
        """Checks if a URL has already been processed."""
        clean_url = self._canonicalize_url(url)
        return clean_url in self.seen_urls

    def mark_seen(self, url: str) -> None:
        """Marks a URL as processed."""
        clean_url = self._canonicalize_url(url)
        if clean_url not in self.seen_urls:
            self.seen_urls[clean_url] = datetime.now(timezone.utc).isoformat()
            self._dirty = True

    def record_posted_story(self, url: str, title: str) -> None:
        """Records a successfully posted story to avoid cross-source duplicate reporting."""
        clean_url = self._canonicalize_url(url)
        self.mark_seen(clean_url)
        self.recent_posts.append({
            "url": clean_url,
            "title": title.strip(),
            "posted_at": datetime.now(timezone.utc).isoformat()
        })
        self._dirty = True

    def get_recent_posted_titles(self, hours: int = 48) -> List[str]:
        """Returns titles of stories posted within the given hour window for semantic deduplication."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        titles = []
        for post in self.recent_posts:
            posted_at_str = post.get("posted_at")
            if not posted_at_str:
                continue
            try:
                posted_at = datetime.fromisoformat(posted_at_str)
                if posted_at.tzinfo is None:
                    posted_at = posted_at.replace(tzinfo=timezone.utc)
                if posted_at >= cutoff:
                    title = post.get("title")
                    if title:
                        titles.append(title)
            except Exception:
                continue
        return titles

    def prune(self) -> None:
        """Removes entries older than retention_days to keep file size small and clean."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        initial_seen_count = len(self.seen_urls)

        # Prune seen_urls
        pruned_urls = {}
        for url, ts_str in self.seen_urls.items():
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    pruned_urls[url] = ts_str
            except Exception:
                # If date parsing fails, keep recent or drop
                pruned_urls[url] = ts_str
        self.seen_urls = pruned_urls

        # Prune recent_posts (keep max 3 days for deduplication)
        recent_posts_cutoff = datetime.now(timezone.utc) - timedelta(days=3)
        pruned_posts = []
        for post in self.recent_posts:
            try:
                ts = datetime.fromisoformat(post.get("posted_at", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= recent_posts_cutoff:
                    pruned_posts.append(post)
            except Exception:
                pass
        self.recent_posts = pruned_posts

        if len(self.seen_urls) != initial_seen_count:
            self._dirty = True
            logger.info("Pruned %d stale URLs from state.", initial_seen_count - len(self.seen_urls))

        # Prune seen_images (keep for retention_days)
        initial_seen_images_count = len(self.seen_images)
        pruned_images = {}
        for img_key, ts_str in self.seen_images.items():
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    pruned_images[img_key] = ts_str
            except Exception:
                pruned_images[img_key] = ts_str
        self.seen_images = pruned_images

        if len(self.seen_images) != initial_seen_images_count:
            self._dirty = True
            logger.info("Pruned %d stale images from state.", initial_seen_images_count - len(self.seen_images))

        # Prune seen_hashes (keep for retention_days)
        initial_seen_hashes_count = len(self.seen_hashes)
        pruned_hashes = {}
        for h_key, ts_str in self.seen_hashes.items():
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    pruned_hashes[h_key] = ts_str
            except Exception:
                pruned_hashes[h_key] = ts_str
        self.seen_hashes = pruned_hashes

        if len(self.seen_hashes) != initial_seen_hashes_count:
            self._dirty = True
            logger.info("Pruned %d stale hashes from state.", initial_seen_hashes_count - len(self.seen_hashes))

    def save(self) -> bool:
        """Atomically saves state to disk if modified."""
        if not self._dirty:
            return False

        self.prune()
        data = {
            "seen_urls": self.seen_urls,
            "seen_images": self.seen_images,
            "seen_hashes": self.seen_hashes,
            "recent_posts": self.recent_posts
        }

        temp_file = f"{self.filepath}.tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, self.filepath)
            self._dirty = False
            logger.info("State successfully saved to %s.", self.filepath)
            return True
        except Exception as e:
            logger.error("Failed to save state to %s: %s", self.filepath, e)
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            return False

    @staticmethod
    def canonicalize_image_key(url: str) -> str:
        """
        Normalizes image URL into a base filename or key to prevent duplicate images
        across different query params, CDN resize suffixes, or protocols.
        """
        if not url:
            return ""
        import re
        clean = url.split("?")[0].lower()
        clean = re.sub(r'-\d+x\d+(\.[a-zA-Z0-9]+)$', r'\1', clean)
        clean = re.sub(r'/(?:4x3|16x9)-(?:medium|small|big)/', '/', clean)
        clean = re.sub(r'/(?:640px|1280px|1600px)/', '/', clean)
        parts = [p for p in clean.split("/") if p and p not in ("http:", "https:")]
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
        return parts[-1] if parts else clean

    MAX_HAMMING_DISTANCE = 6  # Strictly catches duplicate, re-compressed, re-cropped, or watermarked wire photos

    def is_image_seen(self, url: Optional[str] = None, img_hash: Optional[str] = None) -> bool:
        """
        Checks if an image has already been posted in a prior post.
        Checks canonical image key, base filename, and visual dHash perceptual distance <= MAX_HAMMING_DISTANCE.
        """
        if url:
            key = self.canonicalize_image_key(url)
            if key in self.seen_images:
                return True
            basename = key.split("/")[-1]
            if len(basename) > 6 and basename in self.seen_images:
                return True

        if img_hash:
            if img_hash in self.seen_hashes:
                return True
            try:
                target_int = int(img_hash, 16)
                for seen_h in self.seen_hashes:
                    dist = bin(target_int ^ int(seen_h, 16)).count('1')
                    if dist <= self.MAX_HAMMING_DISTANCE:
                        return True
            except Exception:
                pass

        return False

    def record_posted_image(self, url: Optional[str] = None, img_hash: Optional[str] = None) -> None:
        """Marks an image as posted to ensure images NEVER repeat in future posts."""
        if not url and not img_hash:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        if url:
            key = self.canonicalize_image_key(url)
            self.seen_images[key] = now_iso
            basename = key.split("/")[-1]
            if len(basename) > 6:
                self.seen_images[basename] = now_iso
        if img_hash:
            self.seen_hashes[img_hash] = now_iso
        self._dirty = True

    @staticmethod
    def _canonicalize_url(url: str) -> str:
        """Strips query parameters like ?utm_source=... to prevent duplicate tracking."""
        if not url:
            return ""
        # Cut at first '?' or '#'
        url = url.split("?")[0].split("#")[0].strip()
        return url

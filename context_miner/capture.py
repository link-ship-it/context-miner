"""Screenshot capture with pHash deduplication."""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import imagehash
import mss
from PIL import Image

logger = logging.getLogger("context_miner.capture")


class ScreenCapture:
    def __init__(self, cfg: dict):
        self._screenshot_dir = os.path.expanduser(cfg["capture"]["screenshot_dir"])
        self._format = cfg["capture"].get("screenshot_format", "png")
        self._max_size = cfg["capture"].get("max_image_size", 1920)
        self._retention_days = cfg["capture"].get("retention_days", 7)
        self._dedup_enabled = cfg["dedup"].get("enabled", True)
        self._hash_threshold = cfg["dedup"].get("hash_threshold", 7)
        self._last_hashes: dict[int, imagehash.ImageHash] = {}
        self._sct = mss.mss()
        os.makedirs(self._screenshot_dir, exist_ok=True)

    def take_screenshots(self) -> list[dict]:
        """Capture each monitor separately, deduplicate individually.

        Returns a list of metadata dicts for new (non-duplicate) screenshots.
        """
        results = []
        real_monitors = self._sct.monitors[1:]
        if not real_monitors:
            real_monitors = [self._sct.monitors[0]]

        for idx, monitor in enumerate(real_monitors):
            try:
                result = self._capture_monitor(idx, monitor)
                if result:
                    results.append(result)
            except Exception:
                logger.exception("Screenshot capture failed for monitor %d", idx)

        if results:
            self._cleanup_old()

        return results

    def take_screenshot(self) -> dict | None:
        """Backward-compatible single-result method. Returns first new capture or None."""
        results = self.take_screenshots()
        return results[0] if results else None

    def _capture_monitor(self, idx: int, monitor: dict) -> dict | None:
        raw = self._sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        if self._max_size and max(img.size) > self._max_size:
            ratio = self._max_size / max(img.size)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        current_hash = imagehash.phash(img)
        if self._dedup_enabled and idx in self._last_hashes:
            diff = current_hash - self._last_hashes[idx]
            if diff <= self._hash_threshold:
                logger.debug("Monitor %d deduplicated (hash diff=%d)", idx, diff)
                return None

        self._last_hashes[idx] = current_hash
        ts = datetime.now()
        suffix = f"_mon{idx}" if len(self._sct.monitors) > 2 else ""
        filename = ts.strftime("%Y%m%d_%H%M%S") + suffix + f".{self._format}"
        filepath = os.path.join(self._screenshot_dir, filename)
        img.save(filepath, self._format.upper())

        return {
            "path": filepath,
            "timestamp": ts.isoformat(),
            "hash": str(current_hash),
            "width": img.width,
            "height": img.height,
            "monitor": idx,
        }

    def _cleanup_old(self):
        """Remove screenshots older than retention_days."""
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        try:
            for f in Path(self._screenshot_dir).iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                    f.unlink()
                    logger.debug("Cleaned up old screenshot: %s", f.name)
        except Exception:
            logger.exception("Cleanup failed")

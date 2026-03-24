"""Lightweight Gemini API client using httpx directly (bypasses google-genai SDK)."""

import base64
import io
import logging
import os

import httpx
from PIL import Image

logger = logging.getLogger("context_miner.gemini")

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_TIMEOUT = 60
MAX_IMAGE_SIZE = 1024
JPEG_QUALITY = 75


class GeminiClient:
    def __init__(self, model: str = "gemini-3-pro-preview", timeout: int = DEFAULT_TIMEOUT):
        self._api_key = os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable is required")
        self._model = model
        self._http = httpx.Client(timeout=timeout)
        self._url = f"{API_BASE}/models/{model}:generateContent"

    def generate(self, text: str, image: Image.Image | None = None) -> str | None:
        """Send a text (+ optional image) request to Gemini. Returns response text or None."""
        parts = [{"text": text}]

        if image is not None:
            img_b64, mime = self._encode_image(image)
            parts.append({"inline_data": {"mime_type": mime, "data": img_b64}})

        payload = {"contents": [{"parts": parts}]}

        try:
            resp = self._http.post(
                self._url,
                params={"key": self._api_key},
                json=payload,
            )
        except httpx.TimeoutException:
            logger.error("Gemini API request timed out")
            return None
        except Exception:
            logger.exception("Gemini API request failed")
            return None

        if resp.status_code != 200:
            logger.error("Gemini API returned %d: %s", resp.status_code, resp.text[:200])
            return None

        try:
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            logger.error("Unexpected Gemini response structure: %s", resp.text[:200])
            return None

    def _encode_image(self, img: Image.Image) -> tuple[str, str]:
        """Resize and encode image to base64 JPEG for API payload."""
        if max(img.size) > MAX_IMAGE_SIZE:
            ratio = MAX_IMAGE_SIZE / max(img.size)
            img = img.resize(
                (int(img.width * ratio), int(img.height * ratio)),
                Image.LANCZOS,
            )

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"

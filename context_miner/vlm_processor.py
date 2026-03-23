"""Gemini 3 Pro VLM processing — extract structured context from screenshots."""

import json
import logging
import os
import uuid

import httpx
from google import genai
from PIL import Image

from context_miner.prompts import SCREENSHOT_ANALYZE_SYSTEM, SCREENSHOT_ANALYZE_USER

logger = logging.getLogger("context_miner.vlm")


class VLMProcessor:
    def __init__(self, cfg: dict):
        self._model = cfg["vlm"].get("model", "gemini-3-pro-preview")
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable is required")
        self._client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 60},
        )

    async def process_batch(self, screenshots: list[dict]) -> list[dict]:
        """Process a batch of screenshot metadata dicts through VLM.

        Each screenshot dict has: path, timestamp, hash, width, height.
        Returns list of extracted context dicts.
        """
        results = []
        for shot in screenshots:
            try:
                ctx = await self._analyze_single(shot)
                if ctx:
                    results.append(ctx)
            except Exception:
                logger.exception("Failed to analyze screenshot: %s", shot.get("path"))
        return results

    async def _analyze_single(self, shot: dict) -> dict | None:
        img_path = shot["path"]
        if not os.path.exists(img_path):
            logger.warning("Screenshot file not found: %s", img_path)
            return None

        img = Image.open(img_path)

        user_prompt = SCREENSHOT_ANALYZE_USER.format(timestamp=shot["timestamp"])

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=[
                    SCREENSHOT_ANALYZE_SYSTEM,
                    img,
                    user_prompt,
                ],
            )
        except Exception:
            logger.exception("Gemini API call failed for %s", img_path)
            return None

        raw_text = response.text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Failed to parse VLM JSON response: %s", raw_text[:200])
            return None

        return {
            "id": str(uuid.uuid4()),
            "timestamp": shot["timestamp"],
            "application": data.get("application", "unknown"),
            "activity": data.get("activity", ""),
            "entities": data.get("entities", []),
            "intent": data.get("intent", ""),
            "category": data.get("category", "other"),
            "screenshot_path": img_path,
            "raw_vlm_response": raw_text,
        }

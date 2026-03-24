"""Gemini VLM processing — extract structured context from screenshots."""

import json
import logging
import os
import uuid

from PIL import Image

from context_miner.gemini_client import GeminiClient
from context_miner.prompts import SCREENSHOT_ANALYZE_SYSTEM, SCREENSHOT_ANALYZE_USER

logger = logging.getLogger("context_miner.vlm")


class VLMProcessor:
    def __init__(self, cfg: dict):
        model = cfg["vlm"].get("model", "gemini-3-pro-preview")
        self._client = GeminiClient(model=model)

    def process_batch(self, screenshots: list[dict]) -> list[dict]:
        results = []
        for shot in screenshots:
            try:
                ctx = self._analyze_single(shot)
                if ctx:
                    results.append(ctx)
            except Exception:
                logger.exception("Failed to analyze screenshot: %s", shot.get("path"))
        return results

    def _analyze_single(self, shot: dict) -> dict | None:
        img_path = shot["path"]
        if not os.path.exists(img_path):
            logger.warning("Screenshot file not found: %s", img_path)
            return None

        img = Image.open(img_path)
        prompt = SCREENSHOT_ANALYZE_SYSTEM + "\n\n" + SCREENSHOT_ANALYZE_USER.format(
            timestamp=shot["timestamp"]
        )

        raw_text = self._client.generate(prompt, image=img)
        if not raw_text:
            return None

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

"""Activity summary generator — periodically summarizes recent screen contexts."""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta

from google import genai

from context_miner.prompts import ACTIVITY_SUMMARY_SYSTEM, ACTIVITY_SUMMARY_USER

logger = logging.getLogger("context_miner.activity")


class ActivityGenerator:
    def __init__(self, cfg: dict, storage):
        self._storage = storage
        self._interval = cfg["generation"]["activity"]["interval"]
        self._model = cfg["vlm"].get("model", "gemini-3-pro-preview")
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable is required")
        self._client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 60},
        )

    async def generate(self) -> dict | None:
        """Generate an activity summary for the most recent interval."""
        window_minutes = self._interval // 60
        contexts = self._storage.get_recent_contexts(minutes=window_minutes)
        if not contexts:
            logger.debug("No contexts in the last %d minutes, skipping activity generation", window_minutes)
            return None

        now = datetime.now()
        start = now - timedelta(minutes=window_minutes)

        context_lines = []
        for c in contexts:
            entities = c.get("entities", [])
            if isinstance(entities, str):
                entities = [entities]
            context_lines.append(
                f"[{c['timestamp']}] {c.get('application', '?')}: {c.get('activity', '?')} "
                f"(intent: {c.get('intent', '?')}, entities: {', '.join(entities)})"
            )

        user_prompt = ACTIVITY_SUMMARY_USER.format(
            start_time=start.isoformat(),
            end_time=now.isoformat(),
            current_time=now.isoformat(),
            context_data="\n".join(context_lines),
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=[ACTIVITY_SUMMARY_SYSTEM, user_prompt],
            )
        except Exception:
            logger.exception("Gemini activity summary call failed")
            return None

        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("Failed to parse activity summary JSON: %s", raw[:300])
            return None

        return {
            "id": str(uuid.uuid4()),
            "title": data.get("title", "Activity"),
            "description": data.get("description", ""),
            "start_time": start.isoformat(),
            "end_time": now.isoformat(),
            "category_distribution": data.get("category_distribution", {}),
            "insights": data.get("insights", {}),
        }

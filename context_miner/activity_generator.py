"""Activity summary generator — periodically summarizes recent screen contexts."""

import json
import logging
import uuid
from datetime import datetime, timedelta

from context_miner.gemini_client import GeminiClient
from context_miner.prompts import ACTIVITY_SUMMARY_SYSTEM, ACTIVITY_SUMMARY_USER

logger = logging.getLogger("context_miner.activity")


class ActivityGenerator:
    def __init__(self, cfg: dict, storage):
        self._storage = storage
        self._interval = cfg["generation"]["activity"]["interval"]
        model = cfg["vlm"].get("model", "gemini-3-pro-preview")
        self._client = GeminiClient(model=model)

    def generate(self) -> dict | None:
        window_minutes = self._interval // 60
        contexts = self._storage.get_recent_contexts(minutes=window_minutes)
        if not contexts:
            logger.debug("No contexts in the last %d minutes, skipping", window_minutes)
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

        prompt = ACTIVITY_SUMMARY_SYSTEM + "\n\n" + ACTIVITY_SUMMARY_USER.format(
            start_time=start.isoformat(),
            end_time=now.isoformat(),
            current_time=now.isoformat(),
            context_data="\n".join(context_lines),
        )

        raw = self._client.generate(prompt)
        if not raw:
            return None

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

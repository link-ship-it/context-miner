"""Workflow pattern extractor — identifies recurring work patterns from activities."""

import json
import logging
import uuid
from datetime import datetime, timedelta

from context_miner.gemini_client import GeminiClient
from context_miner.prompts import WORKFLOW_EXTRACT_SYSTEM, WORKFLOW_EXTRACT_USER

logger = logging.getLogger("context_miner.workflow")


class WorkflowExtractor:
    def __init__(self, cfg: dict, storage):
        self._storage = storage
        model = cfg["vlm"].get("model", "gemini-3-pro-preview")
        self._client = GeminiClient(model=model)

    def extract(self) -> list[dict] | None:
        activities = self._storage.get_recent_activities(hours=24)
        if len(activities) < 3:
            logger.debug("Not enough activities for workflow extraction (%d)", len(activities))
            return None

        existing = self._storage.get_workflows()

        now = datetime.now()
        start = now - timedelta(hours=24)

        activities_text = "\n".join(
            f"[{a.get('start_time', '?')} - {a.get('end_time', '?')}] "
            f"{a.get('title', '?')}: {a.get('description', a.get('content', ''))}"
            for a in activities
        )

        existing_text = "None" if not existing else "\n".join(
            f"• {w.get('name', '?')} (freq={w.get('frequency', '?')}, conf={w.get('confidence', 0):.1f}): "
            f"{' → '.join(w.get('steps', []))}"
            for w in existing
        )

        prompt = WORKFLOW_EXTRACT_SYSTEM + "\n\n" + WORKFLOW_EXTRACT_USER.format(
            start_time=start.isoformat(),
            end_time=now.isoformat(),
            activities_data=activities_text,
            existing_patterns=existing_text,
        )

        raw = self._client.generate(prompt)
        if not raw:
            return None

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("Failed to parse workflow extraction JSON: %s", raw[:300])
            return None

        patterns = data.get("patterns", [])
        results = []
        for p in patterns:
            matched = self._find_existing(p, existing)
            if matched:
                results.append({
                    "id": matched["id"],
                    "name": p.get("name", matched["name"]),
                    "description": p.get("description", matched["description"]),
                    "steps": p.get("steps", matched["steps"]),
                    "frequency": p.get("frequency", matched["frequency"]),
                    "confidence": min(1.0, matched.get("confidence", 0) + 0.1),
                })
            else:
                results.append({
                    "id": str(uuid.uuid4()),
                    "name": p.get("name", "Unknown pattern"),
                    "description": p.get("description", ""),
                    "steps": p.get("steps", []),
                    "frequency": p.get("frequency", "occasional"),
                    "confidence": p.get("confidence", 0.3),
                })

        if data.get("observations"):
            logger.info("Workflow observations: %s", data["observations"])

        return results if results else None

    def _find_existing(self, new_pattern: dict, existing: list[dict]) -> dict | None:
        new_name = (new_pattern.get("name") or "").lower()
        for e in existing:
            old_name = (e.get("name") or "").lower()
            if new_name == old_name or new_name in old_name or old_name in new_name:
                return e
        return None

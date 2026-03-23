"""Push notifications to OpenClaw instances."""

import json
import logging
import os
import subprocess
from datetime import datetime

from google import genai

from context_miner.prompts import DAILY_REPORT_SYSTEM, DAILY_REPORT_USER

logger = logging.getLogger("context_miner.push")

API_TIMEOUT = 60


class PushService:
    def __init__(self, cfg: dict, writer=None):
        self._enabled = cfg["push"].get("enabled", True)
        self._profiles = cfg["push"].get("openclaw_profiles", ["default"])
        self._daily_report_time = cfg["push"].get("daily_report_time", "20:00")
        self._model = cfg["vlm"].get("model", "gemini-3-pro-preview")
        self._last_daily_report_date: str | None = None
        self._writer = writer
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            self._client = genai.Client(
                api_key=api_key,
                http_options={"timeout": API_TIMEOUT},
            )
        else:
            self._client = None

    def _send_to_openclaw(self, message: str, profile: str = "default"):
        try:
            nvm_dir = os.environ.get("NVM_DIR", os.path.expanduser("~/.nvm"))
            cmd = (
                f'source "{nvm_dir}/nvm.sh" && nvm use 22 --silent && '
                f'openclaw agent --message {json.dumps(message)}'
            )
            if profile != "default":
                cmd += f" --profile {profile}"
            subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True,
                text=True,
                timeout=30,
            )
            logger.info("Pushed message to OpenClaw (profile=%s): %s", profile, message[:80])
        except Exception:
            logger.exception("Failed to push to OpenClaw (profile=%s)", profile)

    def push_activity(self, activity: dict):
        if not self._enabled:
            return

        insights = activity.get("insights", {})
        tips = insights.get("tips", [])
        todos = insights.get("potential_todos", [])

        if not tips and not todos:
            return

        parts = [f"[ContextMiner] Activity: {activity.get('title', 'update')}"]
        if tips:
            parts.append("Tips: " + "; ".join(tips))
        if todos:
            parts.append("Suggested TODOs: " + "; ".join(todos))

        message = "\n".join(parts)
        for profile in self._profiles:
            self._send_to_openclaw(message, profile)

    def push_workflow_insight(self, patterns: list[dict]):
        if not self._enabled or not patterns:
            return

        new_patterns = [p for p in patterns if p.get("confidence", 0) < 0.5]
        if not new_patterns:
            return

        lines = ["[ContextMiner] New workflow patterns discovered:"]
        for p in new_patterns[:3]:
            steps = " → ".join(p.get("steps", []))
            lines.append(f"  • {p.get('name', '?')}: {steps}")

        message = "\n".join(lines)
        for profile in self._profiles:
            self._send_to_openclaw(message, profile)

    def maybe_send_daily_report(self, storage):
        if not self._enabled or not self._client:
            return

        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")

        if self._last_daily_report_date == today_str:
            return
        if current_time < self._daily_report_time:
            return

        self._last_daily_report_date = today_str

        activities = storage.get_today_activities()
        patterns = storage.get_workflows()

        if not activities:
            return

        activities_text = "\n".join(
            f"[{a.get('start_time', '?')} - {a.get('end_time', '?')}] {a.get('title', '?')}: "
            f"{a.get('description', a.get('content', ''))}"
            for a in activities
        )
        patterns_text = "\n".join(
            f"• {p.get('name', '?')}: {p.get('description', '')}"
            for p in patterns[:5]
        ) or "None identified yet."

        user_prompt = DAILY_REPORT_USER.format(
            date=today_str,
            activities=activities_text,
            patterns=patterns_text,
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=[DAILY_REPORT_SYSTEM, user_prompt],
            )
            report = response.text.strip()
        except Exception:
            logger.exception("Failed to generate daily report")
            return

        if self._writer:
            self._writer.write_daily_report(report, date=today_str)

        for profile in self._profiles:
            self._send_to_openclaw(f"[ContextMiner Daily Report]\n\n{report}", profile)
        logger.info("Daily report sent for %s", today_str)

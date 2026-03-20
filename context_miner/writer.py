"""Markdown file writer — produces timeline and daily report files."""

import logging
import os
from datetime import datetime

logger = logging.getLogger("context_miner.writer")


class MarkdownWriter:
    def __init__(self, cfg: dict):
        self._enabled = cfg.get("output", {}).get("enabled", True)
        base_dir = os.path.expanduser(cfg.get("output", {}).get("dir", "~/.context-miner/output"))
        self._timeline_dir = os.path.join(base_dir, cfg.get("output", {}).get("timeline_dir", "timeline"))
        self._daily_dir = os.path.join(base_dir, cfg.get("output", {}).get("daily_dir", "daily"))

        if self._enabled:
            os.makedirs(self._timeline_dir, exist_ok=True)
            os.makedirs(self._daily_dir, exist_ok=True)

    def append_timeline_entry(self, ctx: dict):
        """Append a single VLM context result to today's timeline file."""
        if not self._enabled:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        filepath = os.path.join(self._timeline_dir, f"{today}.md")

        ts = ctx.get("timestamp", "")
        try:
            time_str = datetime.fromisoformat(ts).strftime("%H:%M")
        except (ValueError, TypeError):
            time_str = ts[:5] if len(ts) >= 5 else "??:??"

        app = ctx.get("application", "Unknown")
        activity = ctx.get("activity", "")
        intent = ctx.get("intent", "")
        category = ctx.get("category", "other")
        entities = ctx.get("entities", [])
        if isinstance(entities, str):
            entities = [entities]

        lines = [f"## {time_str} — {app}\n"]
        lines.append(f"{activity}\n")
        if intent:
            lines.append(f"- **Intent**: {intent}\n")
        if entities:
            lines.append(f"- **Entities**: {', '.join(entities)}\n")
        lines.append(f"- **Category**: {category}\n")
        lines.append("\n")

        is_new = not os.path.exists(filepath)
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                if is_new:
                    f.write(f"# Timeline — {today}\n\n")
                f.write("".join(lines))
            logger.debug("Timeline entry appended to %s", filepath)
        except Exception:
            logger.exception("Failed to write timeline entry")

    def write_daily_report(self, report_text: str, date: str | None = None):
        """Write the daily report to a markdown file."""
        if not self._enabled:
            return

        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        filepath = os.path.join(self._daily_dir, f"{date}.md")

        content = f"# Daily Report — {date}\n\n{report_text}\n"

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Daily report written to %s", filepath)
        except Exception:
            logger.exception("Failed to write daily report")

    @property
    def timeline_dir(self) -> str:
        return self._timeline_dir

    @property
    def daily_dir(self) -> str:
        return self._daily_dir

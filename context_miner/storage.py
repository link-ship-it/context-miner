"""Dual storage layer — SQLite for structured data, ChromaDB for vector search."""

import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta

import chromadb

logger = logging.getLogger("context_miner.storage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contexts (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    application TEXT,
    activity TEXT,
    entities TEXT,
    intent TEXT,
    category TEXT,
    screenshot_path TEXT,
    raw_vlm_response TEXT
);
CREATE INDEX IF NOT EXISTS idx_contexts_timestamp ON contexts(timestamp);
CREATE INDEX IF NOT EXISTS idx_contexts_category ON contexts(category);

CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,
    title TEXT,
    content TEXT,
    start_time TEXT,
    end_time TEXT,
    category_distribution TEXT,
    insights TEXT
);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_time);

CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    steps TEXT,
    frequency TEXT,
    last_seen TEXT,
    confidence REAL
);
"""


class StorageLayer:
    def __init__(self, cfg: dict):
        data_dir = os.path.expanduser(cfg["storage"]["data_dir"])
        os.makedirs(data_dir, exist_ok=True)

        db_path = os.path.join(data_dir, cfg["storage"]["sqlite_db"])
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

        chroma_dir = os.path.join(data_dir, cfg["storage"]["chromadb_dir"])
        self._chroma = chromadb.PersistentClient(path=chroma_dir)
        self._collection = self._chroma.get_or_create_collection(
            name="contexts",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Storage initialized (sqlite=%s, chroma=%s)", db_path, chroma_dir)

    # ── Context CRUD ──

    def save_context(self, ctx: dict):
        entities_json = json.dumps(ctx.get("entities", []), ensure_ascii=False)
        self._conn.execute(
            """INSERT OR REPLACE INTO contexts
               (id, timestamp, application, activity, entities, intent, category, screenshot_path, raw_vlm_response)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ctx["id"],
                ctx["timestamp"],
                ctx.get("application"),
                ctx.get("activity"),
                entities_json,
                ctx.get("intent"),
                ctx.get("category"),
                ctx.get("screenshot_path"),
                ctx.get("raw_vlm_response"),
            ),
        )
        self._conn.commit()

        doc_text = f"{ctx.get('activity', '')} | {ctx.get('intent', '')} | {' '.join(ctx.get('entities', []))}"
        if doc_text.strip(" |"):
            try:
                self._collection.add(
                    ids=[ctx["id"]],
                    documents=[doc_text],
                    metadatas=[{
                        "timestamp": ctx["timestamp"],
                        "application": ctx.get("application", ""),
                        "category": ctx.get("category", "other"),
                    }],
                )
            except Exception:
                logger.exception("ChromaDB add failed (SQLite write succeeded)")

    def get_recent_contexts(self, minutes: int = 15) -> list[dict]:
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM contexts WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_latest_context(self) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM contexts ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def search_contexts(self, query: str, n: int = 10) -> list[dict]:
        try:
            results = self._collection.query(query_texts=[query], n_results=n)
        except Exception:
            logger.exception("ChromaDB query failed")
            return []

        if not results["ids"] or not results["ids"][0]:
            return []

        ids = results["ids"][0]
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT * FROM contexts WHERE id IN ({placeholders})", ids
        ).fetchall()

        id_to_row = {r["id"]: self._row_to_dict(r) for r in rows}
        distances = results.get("distances", [[]])[0]
        out = []
        for i, cid in enumerate(ids):
            if cid in id_to_row:
                entry = id_to_row[cid]
                entry["relevance"] = 1.0 - (distances[i] if i < len(distances) else 0.0)
                out.append(entry)
        return out

    # ── Activity CRUD ──

    def save_activity(self, activity: dict):
        aid = activity.get("id") or str(uuid.uuid4())
        self._conn.execute(
            """INSERT OR REPLACE INTO activities
               (id, title, content, start_time, end_time, category_distribution, insights)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                aid,
                activity.get("title"),
                activity.get("description", activity.get("content", "")),
                activity.get("start_time"),
                activity.get("end_time"),
                json.dumps(activity.get("category_distribution", {}), ensure_ascii=False),
                json.dumps(activity.get("insights", {}), ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def get_recent_activities(self, hours: int = 24) -> list[dict]:
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM activities WHERE start_time >= ? ORDER BY start_time DESC",
            (cutoff,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_today_activities(self) -> list[dict]:
        today = datetime.now().strftime("%Y-%m-%d")
        rows = self._conn.execute(
            "SELECT * FROM activities WHERE start_time >= ? ORDER BY start_time",
            (today,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Workflow CRUD ──

    def save_workflow(self, wf: dict):
        wid = wf.get("id") or str(uuid.uuid4())
        self._conn.execute(
            """INSERT OR REPLACE INTO workflows
               (id, name, description, steps, frequency, last_seen, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                wid,
                wf.get("name"),
                wf.get("description"),
                json.dumps(wf.get("steps", []), ensure_ascii=False),
                wf.get("frequency", "occasional"),
                datetime.now().isoformat(),
                wf.get("confidence", 0.0),
            ),
        )
        self._conn.commit()

    def get_workflows(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM workflows ORDER BY confidence DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Helpers ──

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in ("entities", "category_distribution", "insights", "steps"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

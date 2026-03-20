"""FastAPI query server — exposes ContextMiner data to OpenClaw skills."""

import logging
import os
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Query

logger = logging.getLogger("context_miner.api")

app = FastAPI(title="ContextMiner", version="0.1.0")

_storage = None


def start_api_server(cfg: dict, storage):
    """Start the API server in a blocking manner (intended for a daemon thread)."""
    global _storage
    _storage = storage

    host = cfg["api"]["host"]
    port = cfg["api"]["port"]
    logger.info("Starting API server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


@app.get("/api/status")
def get_status():
    pid_file = os.path.expanduser("~/.context-miner/daemon.pid")
    running = os.path.exists(pid_file)
    return {
        "status": "running" if running else "unknown",
        "pid_file": pid_file,
        "time": datetime.now().isoformat(),
    }


@app.get("/api/context/now")
def get_current_context():
    if not _storage:
        return {"error": "storage not initialized"}
    ctx = _storage.get_latest_context()
    if not ctx:
        return {"message": "No context captured yet"}
    ctx.pop("raw_vlm_response", None)
    return ctx


@app.get("/api/activity/recent")
def get_recent_activities(hours: int = Query(default=24, ge=1, le=168)):
    if not _storage:
        return {"error": "storage not initialized"}
    activities = _storage.get_recent_activities(hours=hours)
    return {"count": len(activities), "activities": activities}


@app.get("/api/activity/daily")
def get_daily_activities():
    if not _storage:
        return {"error": "storage not initialized"}
    activities = _storage.get_today_activities()
    return {"date": datetime.now().strftime("%Y-%m-%d"), "count": len(activities), "activities": activities}


@app.get("/api/search")
def search_contexts(q: str = Query(..., min_length=1), n: int = Query(default=10, ge=1, le=50)):
    if not _storage:
        return {"error": "storage not initialized"}
    results = _storage.search_contexts(query=q, n=n)
    for r in results:
        r.pop("raw_vlm_response", None)
    return {"query": q, "count": len(results), "results": results}


@app.get("/api/workflow/patterns")
def get_workflow_patterns():
    if not _storage:
        return {"error": "storage not initialized"}
    patterns = _storage.get_workflows()
    return {"count": len(patterns), "patterns": patterns}


@app.get("/api/todos")
def get_todo_suggestions():
    """Smart TODO suggestions derived from recent activities and patterns."""
    if not _storage:
        return {"error": "storage not initialized"}

    recent = _storage.get_recent_activities(hours=8)
    todos = []
    for act in recent:
        insights = act.get("insights", {})
        if isinstance(insights, dict):
            for item in insights.get("potential_todos", []):
                todos.append(item)
    return {"count": len(todos), "todos": todos}

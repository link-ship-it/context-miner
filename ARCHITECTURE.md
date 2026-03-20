# ContextMiner Architecture

## Overview

ContextMiner is a single-process asyncio daemon that continuously captures screenshots, understands them via Gemini VLM, and builds a searchable activity history. It produces two daily document outputs (timeline + daily report) and exposes a FastAPI query interface. OpenClaw instances consume data through two parallel pipelines: real-time API queries and a document-based three-tier memory system.

## Dual Pipeline Design

```
                         ┌─────────────────────────────────────────────────┐
                         │          ContextMiner Daemon (:18900)           │
                         │                                                 │
Screenshot (10s)         │  pHash Dedup → Batch Queue → Gemini VLM        │
         │               │         │                        │              │
         └──────────────►│         │                        ▼              │
                         │         │              SQLite + ChromaDB        │
                         │         │                   │    │    │         │
                         │         │    Activity (15m)──┘    │    │         │
                         │         │    Workflow (1h) ───────┘    │         │
                         │         │    Daily Report (20:00) ─────┘         │
                         └─────────┼───────────────────────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              Pipeline 1     Pipeline 2     Pipeline 2
             (real-time)    (documents)    (documents)
                    │              │              │
                    ▼              ▼              ▼
            FastAPI API     timeline/*.md    daily/*.md
            (:18900)        (per-entry)     (end-of-day)
                    │              │              │
                    │              └──────┬───────┘
                    │                     │
                    ▼                     ▼
              ┌──────────┐     ┌───────────────────┐
              │ OpenClaw  │     │ Three-Tier Memory │
              │ curl API  │     │                   │
              │ (instant) │     │ Tier 1: raw files │
              └──────────┘     │ Tier 2: context-  │
                               │   observations.md │
                               │ Tier 3: MEMORY.md │
                               └───────────────────┘
```

### Pipeline 1 — Real-Time API Query

For immediate questions like "what am I doing now?" or "search my activity for voice features". OpenClaw calls `curl localhost:18900/api/...`, gets JSON, and presents a human-readable answer.

### Pipeline 2 — Document → Three-Tier Memory

For long-term context accumulation. The daemon writes markdown files daily; OpenClaw reads them each morning, extracts observations into a working memory file, and promotes confirmed insights to core memory.

| Tier | File | Who Writes | Who Reads | Lifecycle |
|---|---|---|---|---|
| 1 (Raw) | `~/.context-miner/output/timeline/YYYY-MM-DD.md` | Daemon | OpenClaw | Accumulates daily, never edited by OpenClaw |
| 1 (Raw) | `~/.context-miner/output/daily/YYYY-MM-DD.md` | Daemon | OpenClaw | One per day, never edited by OpenClaw |
| 2 (Observations) | `memory/context-observations.md` | OpenClaw | OpenClaw | Growing log; consolidated monthly |
| 3 (Core Memory) | `MEMORY.md` | OpenClaw | OpenClaw | Only confirmed, stable insights |

**Promotion criteria**: An observation in Tier 2 should only be promoted to Tier 3 (`MEMORY.md`) when it has been confirmed consistently over 5+ days — e.g. a stable work habit, a strong preference, or a major milestone.

## Module Breakdown

### 1. `capture.py` — Screenshot + Dedup

- Uses `mss` to grab the primary monitor every 10 seconds
- Resizes images larger than 1920px (LANCZOS downscale) to save disk and reduce VLM cost
- Computes a perceptual hash (`imagehash.phash`) and compares with the previous screenshot
- If hash difference ≤ 7 (configurable), the screenshot is considered duplicate and skipped
- Saves unique screenshots to `~/.context-miner/screenshots/` as `YYYYMMDD_HHMMSS.png`
- Auto-cleans files older than 7 days on each capture cycle
- Returns a metadata dict `{path, timestamp, hash, width, height}` or `None` if deduplicated

### 2. `vlm_processor.py` — Gemini VLM Analysis

- Does NOT call the API for every screenshot. Instead, the daemon batches them:
  - Trigger when pending queue reaches `batch_size` (5), OR
  - Trigger when `batch_timeout` (30s) has elapsed since last flush
- For each screenshot, sends three items to Gemini 3 Pro via `google-genai` SDK:
  1. System prompt (role + output rules)
  2. PIL Image object (the screenshot)
  3. User prompt (timestamp + JSON schema)
- Gemini extracts 5 structured fields:
  - `application`: focused app name (VSCode, Chrome, Feishu, etc.)
  - `activity`: one-sentence description of current action
  - `entities`: concrete names — files, projects, URLs, people, ticket IDs
  - `intent`: user's high-level goal
  - `category`: one of `coding | browsing | communication | writing | design | meeting | other`
- Handles markdown-wrapped JSON responses (strips ``` fences)
- Each result gets a UUID and is returned as a context dict

### 3. `storage.py` — Dual Storage Layer

Two storage backends serve different query patterns:

**SQLite** (`~/.context-miner/data/app.db`) — structured queries:
- `contexts` table: every VLM analysis result (indexed by timestamp and category)
- `activities` table: 15-minute activity summaries (indexed by start_time)
- `workflows` table: identified workflow patterns (sorted by confidence)
- JSON fields (entities, insights, steps, category_distribution) stored as TEXT, auto-deserialized on read

**ChromaDB** (`~/.context-miner/data/chromadb/`) — semantic search:
- `contexts` collection with cosine distance
- Document text: `"{activity} | {intent} | {entity1 entity2 ...}"`
- Metadata: timestamp, application, category
- `search_contexts(query, n)` → ChromaDB vector search → get IDs → SQLite full-row lookup → merge relevance scores

### 4. `writer.py` — Markdown Document Output

Produces the two document types that feed Pipeline 2:

**Timeline** (`~/.context-miner/output/timeline/YYYY-MM-DD.md`):
- Appended to after every VLM batch processing
- Each entry formatted as:
  ```markdown
  ## 10:30 — VSCode
  Writing voice memo processor for the project
  - **Intent**: Implement audio processing feature
  - **Entities**: memo_processor.py, VoiceSession
  - **Category**: coding
  ```
- New file created automatically each day with a `# Timeline — YYYY-MM-DD` header
- Chronological, append-only — never modified after writing

**Daily Report** (`~/.context-miner/output/daily/YYYY-MM-DD.md`):
- Written once at 20:00 when the daily report is generated
- Contains a Gemini-generated natural language narrative: accomplishments, time distribution, patterns, tomorrow's priorities
- Overwrites if regenerated (single write per day)

### 5. `activity_generator.py` — Activity Summaries

- Runs every 15 minutes (900s)
- Pulls all contexts from the last 15-minute window via SQLite
- Formats them as timestamped text lines:
  `[timestamp] App: activity (intent: ..., entities: ...)`
- Sends to Gemini with `ACTIVITY_SUMMARY_SYSTEM` prompt
- Gemini returns:
  - `title`: ≤10 word summary
  - `description`: 2-4 sentence narrative
  - `category_distribution`: e.g. `{"coding": 0.7, "communication": 0.3}`
  - `insights`: `{potential_todos: [...], tips: [...], focus_areas: [...]}`
- Saved to SQLite `activities` table

### 6. `workflow_extractor.py` — Workflow Pattern Recognition

- Runs every 1 hour (3600s), requires ≥3 activities in the last 24h
- Sends both recent activities AND existing workflow patterns to Gemini
- Gemini identifies recurring sequences like:
  `"Morning Routine": check Feishu → open VSCode → write code → code review`
- **Incremental update logic**:
  - New patterns matched to existing ones by fuzzy name matching (substring containment)
  - Matched: reuse existing ID, increment confidence by 0.1 (capped at 1.0)
  - Unmatched: create new pattern with confidence 0.3
- This way, frequently recurring patterns gain confidence over time

### 7. `push.py` — Proactive Push to OpenClaw

Three push scenarios, all via `openclaw agent --message "..."` CLI command:

| Scenario | Trigger | Content |
|---|---|---|
| Activity insights | After each activity generation, IF tips or todos exist | Tips and suggested TODOs |
| Workflow discovery | After workflow extraction, IF new patterns found (confidence < 0.5) | New pattern names and step sequences |
| Daily report | Once per day at 20:00 | Full day narrative generated by Gemini; also written to `daily/YYYY-MM-DD.md` |

Push mechanism: `subprocess.run(["bash", "-c", "source nvm.sh && nvm use 22 && openclaw agent --message ..."])` — supports both `default` (卡比兽) and `alpha` (马铃薯) profiles.

### 8. `api_server.py` — FastAPI Query Interface

Runs on `127.0.0.1:18900` in a daemon thread. All endpoints return JSON.

| Endpoint | Method | Description |
|---|---|---|
| `/api/status` | GET | Daemon running status (checks PID file) |
| `/api/context/now` | GET | Latest single context record (what you're doing right now) |
| `/api/activity/recent?hours=N` | GET | Activity summaries from last N hours (default 24, max 168) |
| `/api/activity/daily` | GET | All activity summaries for today |
| `/api/search?q=...&n=10` | GET | Semantic search via ChromaDB (query by meaning, not exact text) |
| `/api/workflow/patterns` | GET | All identified workflow patterns, sorted by confidence DESC |
| `/api/todos` | GET | Aggregated TODO suggestions from last 8 hours of activity insights |

All responses strip `raw_vlm_response` to keep payloads small.

### 9. `daemon.py` — Main Loop Orchestrator

Single async loop that drives everything:

```python
while not shutdown:
    # 1. Capture screenshot (may return None if deduplicated)
    # 2. Append to pending queue if unique
    # 3. If queue full OR timeout elapsed:
    #    a. Batch VLM process
    #    b. Save each context to SQLite + ChromaDB
    #    c. Append each context to timeline markdown file
    # 4. If ≥15min since last activity → generate activity summary → push if insightful
    # 5. If ≥1h since last workflow → extract workflow patterns → push if new
    # 6. Check if 20:00 → generate daily report → write to file + push to OpenClaw
    # 7. Sleep for capture_interval (10s), or wake on shutdown signal
```

- Graceful shutdown via `SIGTERM`/`SIGINT` → sets `asyncio.Event`
- PID file at `~/.context-miner/daemon.pid`
- Logs to `~/.context-miner/daemon.log`
- API server runs in a background `threading.Thread`

### 10. `prompts.py` — Prompt Templates

Four prompt pairs (system + user) for different Gemini calls:

| Prompt | Used By | Output Format |
|---|---|---|
| `SCREENSHOT_ANALYZE_*` | VLM processor | JSON: application, activity, entities, intent, category |
| `ACTIVITY_SUMMARY_*` | Activity generator | JSON: title, description, category_distribution, insights |
| `WORKFLOW_EXTRACT_*` | Workflow extractor | JSON: patterns[{name, description, steps, frequency, confidence}], observations |
| `DAILY_REPORT_*` | Push service (daily) | Plain text narrative in user's language |

### 11. `cli.py` — CLI Entry Point

```bash
python -m context_miner.cli start [--foreground] [--config path]
python -m context_miner.cli stop
python -m context_miner.cli status
```

- `start`: loads config, ensures directories, writes PID, runs daemon loop
- `stop`: reads PID file, sends SIGTERM
- `status`: checks if PID process is alive

## Configuration (`config.yaml`)

```yaml
capture:
  interval: 10          # seconds between screenshots
  max_image_size: 1920  # downscale threshold
  retention_days: 7     # auto-cleanup

dedup:
  hash_threshold: 7     # pHash diff ≤ 7 = duplicate

vlm:
  model: gemini-3-pro-preview
  batch_size: 5         # screenshots per VLM batch
  batch_timeout: 30     # max seconds before forcing batch

generation:
  activity:
    interval: 900       # 15 minutes
  workflow:
    interval: 3600      # 1 hour

output:
  enabled: true
  dir: ~/.context-miner/output
  timeline_dir: timeline    # → output/timeline/YYYY-MM-DD.md
  daily_dir: daily          # → output/daily/YYYY-MM-DD.md

push:
  daily_report_time: "20:00"
  openclaw_profiles: [default, alpha]

api:
  port: 18900
```

## Data Storage Locations

| Path | Content | Written By |
|---|---|---|
| `~/.context-miner/screenshots/` | Raw screenshot PNGs (auto-cleaned after 7 days) | capture.py |
| `~/.context-miner/data/app.db` | SQLite database (contexts, activities, workflows) | storage.py |
| `~/.context-miner/data/chromadb/` | ChromaDB vector store | storage.py |
| `~/.context-miner/output/timeline/` | Daily timeline markdown files | writer.py |
| `~/.context-miner/output/daily/` | Daily report markdown files | writer.py |
| `~/.context-miner/daemon.pid` | PID file | daemon.py |
| `~/.context-miner/daemon.log` | Daemon log | daemon.py |

## OpenClaw Integration

### Skill Installation

SKILL.md installed at:
- `~/.openclaw/workspace/skills/context-miner/SKILL.md` (卡比兽)
- `~/.openclaw-alpha/workspace/skills/context-miner/SKILL.md` (马铃薯)

### Two Consumption Modes

**Mode 1 — Real-time query (Pipeline 1)**:
When a user asks "what am I doing now?" or "search my activity for voice features", the skill instructs OpenClaw to `curl` the appropriate API endpoint, parse the JSON, and present a human-readable answer.

**Mode 2 — Memory absorption (Pipeline 2)**:
Each morning, OpenClaw reads yesterday's timeline and daily report files, extracts noteworthy observations, and maintains the three-tier memory system:

1. **Read** raw files (Tier 1 — never edit):
   ```bash
   cat ~/.context-miner/output/timeline/$(date -v-1d +%Y-%m-%d).md
   cat ~/.context-miner/output/daily/$(date -v-1d +%Y-%m-%d).md
   ```

2. **Extract** observations into `memory/context-observations.md` (Tier 2):
   - Recurring patterns, project focus shifts, work style traits, notable events
   - Dated, concise entries; consolidated monthly

3. **Promote** confirmed insights to `MEMORY.md` (Tier 3):
   - Only after 5+ days of consistent observation
   - Stable habits, strong preferences, major milestones

## Resource Consumption

| Resource | Estimate | Notes |
|---|---|---|
| Gemini API | ~200-400 calls/day | 10s capture, 50-70% filtered by dedup, batched 5 at a time |
| Disk | ~500MB-1GB/day | Screenshots (7-day retention auto-cleanup) |
| Memory | ~200MB | Daemon process + ChromaDB |
| CPU | Low | Only pHash computation and HTTP calls |

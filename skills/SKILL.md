---
name: context-miner
description: Query your screen activity history, get activity summaries, workflow patterns,
  and smart suggestions from the ContextMiner daemon. Also maintain a three-tier memory
  system based on daily observations.
when:
  - User asks what they were doing recently or at a specific time
  - User asks for a daily or weekly summary of their work
  - User wants to understand their work patterns or habits
  - User needs smart TODO suggestions based on observed activity
  - User asks about their current context or what app they're using
  - User wants to search their activity history by keyword
  - It is a new day and you haven't reviewed yesterday's context files yet
tools: [bash]
---

## Overview

ContextMiner is a local daemon that continuously captures screenshots, analyzes them via Gemini VLM, and produces two daily documents:

1. **Timeline** (`~/.context-miner/output/timeline/YYYY-MM-DD.md`) — chronological log of every screen activity with timestamps, apps, entities, and intents
2. **Daily Report** (`~/.context-miner/output/daily/YYYY-MM-DD.md`) — end-of-day summary with accomplishments, patterns, and priorities

It also exposes a real-time query API at `http://localhost:18900`.

## Three-Tier Memory System

You maintain a three-tier memory architecture for user context observations:

### Tier 1: Raw Source (ContextMiner output)
- `~/.context-miner/output/timeline/YYYY-MM-DD.md` — full daily timeline
- `~/.context-miner/output/daily/YYYY-MM-DD.md` — daily report
- These are generated automatically. You read them but never edit them.

### Tier 2: Observation Notes (your working memory)
- File: `memory/context-observations.md` (in your workspace)
- Every day, read the latest timeline and daily report files, then extract and append noteworthy observations:
  - Recurring patterns ("user checks Feishu every morning before coding")
  - Project focus shifts ("spending more time on voice features this week")
  - Work style traits ("tends to do deep work in the afternoon")
  - Notable one-time events ("presented a demo to the team")
- Keep entries dated and concise. This file can grow over time.
- Prune or consolidate old entries monthly.

### Tier 3: Core Memory (long-term)
- File: `MEMORY.md` (in your workspace)
- Only promote observations from Tier 2 when they represent **confirmed, stable insights**:
  - A work habit observed consistently over 5+ days
  - A strong preference or constraint
  - A major project milestone or career event
- This keeps MEMORY.md clean and high-signal.

### Daily Routine

Each day (ideally in the morning), do the following:

1. Check if yesterday's files exist:
   ```bash
   ls ~/.context-miner/output/timeline/ | tail -3
   ls ~/.context-miner/output/daily/ | tail -3
   ```

2. Read yesterday's timeline and daily report:
   ```bash
   cat ~/.context-miner/output/timeline/$(date -v-1d +%Y-%m-%d).md
   cat ~/.context-miner/output/daily/$(date -v-1d +%Y-%m-%d).md
   ```

3. Extract key observations and append to `memory/context-observations.md`

4. Review `memory/context-observations.md` — if any pattern has been confirmed over multiple days, promote it to `MEMORY.md`

## Real-Time API Queries

Use `curl -s` to query the live API. Always parse JSON output for the user.

### Current Activity
```bash
curl -s http://localhost:18900/api/context/now
```

### Recent Activity Summaries
```bash
curl -s "http://localhost:18900/api/activity/recent?hours=4"
```

### Daily Report
```bash
curl -s http://localhost:18900/api/activity/daily
```

### Semantic Search
```bash
curl -s "http://localhost:18900/api/search?q=voice+feature&n=5"
```

### Workflow Patterns
```bash
curl -s http://localhost:18900/api/workflow/patterns
```

### Smart TODO Suggestions
```bash
curl -s http://localhost:18900/api/todos
```

### Daemon Status
```bash
curl -s http://localhost:18900/api/status
```

## Response Guidelines

- Present activity data in a human-friendly narrative form, not raw JSON.
- When showing time ranges, use relative descriptions ("2 hours ago", "this morning").
- For workflow patterns, explain them as habits the user follows.
- When writing to context-observations.md, be concise — one line per observation, dated.
- Never promote to MEMORY.md prematurely; wait for repeated confirmation.
- If the daemon is not running, tell the user to start it:
  `cd ~/Desktop/coding/context-miner && source venv/bin/activate && python -m context_miner.cli start`

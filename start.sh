#!/bin/bash
# One-line start script for ContextMiner
# Usage: ./start.sh [--foreground]
#
# GEMINI_API_KEY can be set in three ways (in priority order):
#   1. Already exported in your shell environment
#   2. Via CONFIG_SCRIPT_DIR env var pointing to a project with scripts/fetch_config.sh
#   3. Via a .env file in this directory: echo "GEMINI_API_KEY=your-key" > .env

set -e
cd "$(dirname "$0")"

# Load from .env if present
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Auto-detect from external config script
if [ -z "$GEMINI_API_KEY" ]; then
    CONFIG_SCRIPT_DIR="${CONFIG_SCRIPT_DIR:-}"
    if [ -n "$CONFIG_SCRIPT_DIR" ] && [ -f "$CONFIG_SCRIPT_DIR/scripts/fetch_config.sh" ]; then
        GEMINI_API_KEY=$(
            zsh -l -c "
                cd '$CONFIG_SCRIPT_DIR' &&
                source venv/bin/activate &&
                source scripts/fetch_config.sh > /dev/null 2>&1 &&
                echo \"\$GEMINI_API_KEY\"
            " 2>/dev/null
        )
        export GEMINI_API_KEY
    fi
fi

source venv/bin/activate
python -m context_miner.cli start "$@"

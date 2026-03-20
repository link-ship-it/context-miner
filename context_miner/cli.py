#!/usr/bin/env python3
"""CLI entry point for ContextMiner daemon."""

import argparse
import os
import signal
import sys

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
PID_FILE = os.path.expanduser("~/.context-miner/daemon.pid")


def _read_pid() -> int | None:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                return int(f.read().strip())
        except (ValueError, OSError):
            pass
    return None


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


CONFIG_SCRIPT_DIR = os.environ.get("CONFIG_SCRIPT_DIR", "")
FETCH_CONFIG_SCRIPT = os.path.join(CONFIG_SCRIPT_DIR, "scripts/fetch_config.sh") if CONFIG_SCRIPT_DIR else ""


def _load_env_from_fetch_config():
    """Source an external fetch_config.sh to load GEMINI_API_KEY."""
    if os.environ.get("GEMINI_API_KEY"):
        return

    if not CONFIG_SCRIPT_DIR or not FETCH_CONFIG_SCRIPT or not os.path.exists(FETCH_CONFIG_SCRIPT):
        return

    import subprocess
    cmd = (
        f'cd "{CONFIG_SCRIPT_DIR}" && '
        f'source venv/bin/activate && '
        f'source scripts/fetch_config.sh > /dev/null 2>&1 && '
        f'echo "GEMINI_API_KEY=$GEMINI_API_KEY"'
    )
    try:
        result = subprocess.run(
            ["zsh", "-l", "-c", cmd],
            capture_output=True, text=True, timeout=60,
        )
        for line in result.stdout.strip().splitlines():
            if line.startswith("GEMINI_API_KEY=") and len(line) > len("GEMINI_API_KEY="):
                os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1]
                print("Loaded GEMINI_API_KEY from fetch_config.sh")
                return
    except Exception as e:
        print(f"Warning: failed to load env from fetch_config.sh: {e}")


def cmd_start(args):
    pid = _read_pid()
    if pid and _is_running(pid):
        print(f"ContextMiner already running (PID {pid})")
        return

    _load_env_from_fetch_config()

    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY not set. Either:")
        print(f"  1. source {FETCH_CONFIG_SCRIPT}")
        print("  2. export GEMINI_API_KEY=your-key")
        sys.exit(1)

    from context_miner.daemon import run_daemon

    config_path = args.config or DEFAULT_CONFIG
    print(f"Starting ContextMiner daemon (config: {config_path}) ...")
    run_daemon(config_path, foreground=args.foreground)


def cmd_stop(args):
    pid = _read_pid()
    if not pid or not _is_running(pid):
        print("ContextMiner is not running.")
        return
    print(f"Stopping ContextMiner (PID {pid}) ...")
    os.kill(pid, signal.SIGTERM)
    print("Stopped.")


def cmd_status(args):
    pid = _read_pid()
    if pid and _is_running(pid):
        print(f"ContextMiner is running (PID {pid})")
    else:
        print("ContextMiner is not running.")


def main():
    parser = argparse.ArgumentParser(
        prog="context-miner",
        description="ContextMiner — real-time screen context awareness for OpenClaw",
    )
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Start the daemon")
    p_start.add_argument("--config", help="Path to config.yaml")
    p_start.add_argument("--foreground", "-f", action="store_true", help="Run in foreground")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop the daemon")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="Show daemon status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()

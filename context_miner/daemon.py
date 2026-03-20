"""Main daemon process — orchestrates capture, VLM, generation, and API server."""

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import yaml

logger = logging.getLogger("context_miner")

PID_FILE = os.path.expanduser("~/.context-miner/daemon.pid")


def _load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    return cfg


def _ensure_dirs(cfg: dict):
    dirs = [
        os.path.expanduser(cfg["capture"]["screenshot_dir"]),
        os.path.expanduser(cfg["storage"]["data_dir"]),
        os.path.expanduser(os.path.join(cfg["storage"]["data_dir"], cfg["storage"]["chromadb_dir"])),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def _write_pid():
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid():
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


async def _run_loop(cfg: dict):
    from context_miner.capture import ScreenCapture
    from context_miner.vlm_processor import VLMProcessor
    from context_miner.storage import StorageLayer
    from context_miner.activity_generator import ActivityGenerator
    from context_miner.workflow_extractor import WorkflowExtractor
    from context_miner.api_server import start_api_server
    from context_miner.push import PushService
    from context_miner.writer import MarkdownWriter

    storage = StorageLayer(cfg)
    capture = ScreenCapture(cfg)
    vlm = VLMProcessor(cfg)
    activity_gen = ActivityGenerator(cfg, storage)
    workflow_ext = WorkflowExtractor(cfg, storage)
    writer = MarkdownWriter(cfg)
    push_svc = PushService(cfg, writer=writer)

    shutdown = asyncio.Event()

    def _handle_signal():
        logger.info("Shutdown signal received")
        shutdown.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    api_thread = threading.Thread(
        target=start_api_server,
        args=(cfg, storage),
        daemon=True,
    )
    api_thread.start()
    logger.info("API server thread started")

    capture_interval = cfg["capture"]["interval"]
    vlm_batch_size = cfg["vlm"]["batch_size"]
    vlm_batch_timeout = cfg["vlm"]["batch_timeout"]
    activity_interval = cfg["generation"]["activity"]["interval"]
    workflow_interval = cfg["generation"]["workflow"]["interval"]

    pending_screenshots: list[dict] = []
    last_activity_time = time.time()
    last_workflow_time = time.time()
    last_vlm_flush = time.time()

    logger.info(
        "Daemon loop started (capture=%ds, activity=%ds, workflow=%ds)",
        capture_interval,
        activity_interval,
        workflow_interval,
    )

    while not shutdown.is_set():
        try:
            for result in capture.take_screenshots():
                pending_screenshots.append(result)
                logger.debug("Screenshot captured: %s (pending=%d)", result["path"], len(pending_screenshots))

            batch_ready = (
                len(pending_screenshots) >= vlm_batch_size
                or (pending_screenshots and time.time() - last_vlm_flush >= vlm_batch_timeout)
            )
            if batch_ready:
                batch = pending_screenshots[:vlm_batch_size]
                pending_screenshots = pending_screenshots[vlm_batch_size:]
                try:
                    contexts = await vlm.process_batch(batch)
                    for ctx in contexts:
                        try:
                            storage.save_context(ctx)
                        except Exception:
                            logger.exception("Failed to save context to storage")
                        try:
                            writer.append_timeline_entry(ctx)
                        except Exception:
                            logger.exception("Failed to write timeline entry")
                    logger.info("VLM processed %d screenshots → %d contexts", len(batch), len(contexts))
                except Exception:
                    logger.exception("VLM batch processing failed")
                last_vlm_flush = time.time()

            now = time.time()
            if cfg["generation"]["activity"]["enabled"] and now - last_activity_time >= activity_interval:
                try:
                    activity = await activity_gen.generate()
                    if activity:
                        storage.save_activity(activity)
                        await push_svc.push_activity(activity)
                        logger.info("Activity generated: %s", activity.get("title"))
                except Exception:
                    logger.exception("Activity generation failed")
                last_activity_time = now

            if cfg["generation"]["workflow"]["enabled"] and now - last_workflow_time >= workflow_interval:
                try:
                    patterns = await workflow_ext.extract()
                    if patterns:
                        for p in patterns:
                            storage.save_workflow(p)
                        await push_svc.push_workflow_insight(patterns)
                        logger.info("Workflow patterns extracted: %d", len(patterns))
                except Exception:
                    logger.exception("Workflow extraction failed")
                last_workflow_time = now

            try:
                await push_svc.maybe_send_daily_report(storage)
            except Exception:
                logger.exception("Daily report push failed")

        except Exception:
            logger.exception("Unhandled error in daemon loop")

        try:
            await asyncio.wait_for(shutdown.wait(), timeout=capture_interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Daemon loop exiting")


def run_daemon(config_path: str, foreground: bool = False):
    cfg = _load_config(config_path)
    _ensure_dirs(cfg)

    log_dir = os.path.expanduser("~/.context-miner")
    log_file = os.path.join(log_dir, "daemon.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            *([] if not foreground else [logging.StreamHandler()]),
        ],
    )

    _write_pid()
    logger.info("ContextMiner daemon starting (PID %d)", os.getpid())

    try:
        asyncio.run(_run_loop(cfg))
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        _remove_pid()
        logger.info("ContextMiner daemon stopped")

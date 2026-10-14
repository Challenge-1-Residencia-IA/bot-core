# src/main.py
"""Entry point for the WhatsApp bot with ARQ integration and pause/resume support.

The script performs the following steps:
1. Loads configuration from ``config.yaml``.
2. Starts a Playwright ``async_browser`` instance.
3. Creates an ARQ pool pointing at the local Redis server.
4. Launches three background tasks:
   - ``scanner_loop`` – discovers unread chats and enqueues jobs.
   - ``dispatcher_loop`` – consumes processed results and sends replies.
   - ``monitor_pause_input`` – listens for ``p`` + Enter on the console to toggle pause/resume.
5. Handles graceful shutdown on SIGINT/SIGTERM, cancelling all tasks and closing resources.
"""
import asyncio
import logging

# ---------------------------------------------------------------------------
# Helper: wait for WhatsApp UI to be ready
# ---------------------------------------------------------------------------
async def wait_for_whatsapp_ui(page) -> None:
    """Wait until the main sidebar selector appears.
    The selector ``#pane-side`` is present after the user has logged in (QR code scanned).
    This function retries for up to 5 minutes, logging progress.
    """
    max_wait_seconds = 300  # 5 minutes
    interval = 5
    elapsed = 0
    while elapsed < max_wait_seconds:
        try:
            await page.wait_for_selector('#pane-side', timeout=interval * 1000)
            logging.info("✅ WhatsApp UI detected – sidebar is visible")
            return
        except Exception:
            elapsed += interval
            logging.info(f"⏳ Waiting for WhatsApp UI… ({elapsed}s elapsed)")
    raise TimeoutError("WhatsApp UI did not become ready within timeout")

import signal
import sys
from pathlib import Path

import yaml
from arq import create_pool
from arq.connections import RedisSettings
from playwright.async_api import async_playwright

from src.config import CONFIG
from src.control import monitor_pause_input
from src.scanner import scanner_loop
from src.dispatcher import dispatcher_loop
from src.utils.playwright_utils import abrir_navegador, fechar_navegador

# Global asyncio lock to serialize Playwright interactions
lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Helper: load configuration (already loaded in src.config, but expose for __main__)
# ---------------------------------------------------------------------------

def load_config():
    """Load configuration from ``config.yaml`` if not already loaded.
    ``src.config`` imports CONFIG at import time, but this function ensures the file
    exists and can be re‑loaded during development.
    """
    config_path = Path(__file__).parents[1] / "config.yaml"
    if not config_path.exists():
        logging.error("Configuration file %s not found.", config_path)
        sys.exit(1)
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------
async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Ensure config is loaded (the module ``src.config`` already does this).
    _ = CONFIG  # reference to avoid unused-import lint warnings

    # Start Playwright browser
    playwright = await async_playwright().start()
    browser = await abrir_navegador(playwright)
    page = await browser.new_page()
    await page.goto('https://web.whatsapp.com', wait_until='load')
    await wait_for_whatsapp_ui(page)

    # Create ARQ pool (uses the Redis settings from config.yaml)
    redis_cfg = CONFIG["redis"]
    arq_pool = await create_pool(
        RedisSettings(
            host=redis_cfg["host"],
            port=redis_cfg["port"],
            database=redis_cfg["db"],
        ),
        # Workers are launched separately via ``python -m arq worker src.worker``
    )

    # Create background tasks
    tasks = []
    tasks.append(asyncio.create_task(scanner_loop(page, arq_pool, lock, CONFIG), name="scanner"))
    tasks.append(asyncio.create_task(dispatcher_loop(page, lock), name="dispatcher"))
    tasks.append(asyncio.create_task(monitor_pause_input(), name="pause_listener"))

    # Signal handling – cancel all tasks on SIGINT/SIGTERM
    stop_event = asyncio.Event()

    def _signal_handler(*_):
        logging.info("🔧 Signal received – shutting down gracefully...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows may not support add_signal_handler for all signals
            signal.signal(sig, lambda s, f: _signal_handler())

    # Wait until a termination signal is received
    await stop_event.wait()

    # Cancel background tasks
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # Close ARQ pool and Playwright resources
    await arq_pool.close()
    await fechar_navegador(browser)
    await playwright.stop()
    logging.info("✅ Bot stopped.")

# ---------------------------------------------------------------------------
# Entry point guard
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # In case the signal handler missed the interrupt
        logging.info("KeyboardInterrupt received – exiting.")
        sys.exit(0)

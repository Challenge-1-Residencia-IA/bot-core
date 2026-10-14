# src/main.py
"""Entry point for the WhatsApp bot with ARQ integration and pause/resume support.

The script performs the following steps:
1. Loads configuration from ``config.yaml``.
2. Starts a Playwright ``async_browser`` instance.
3. Creates an ARQ pool pointing at the Redis server shared with the core
   (``bot-core`` worker: ``arq app.worker.WorkerSettings``).
4. Launches three background tasks:
   - ``scanner_loop`` – discovers unread chats, enqueues ``analisar_mensagem``
     jobs for the core and awaits their results (one asyncio task per message).
   - ``dispatcher_loop`` – consumes the replies and sends them.
   - ``monitor_pause_input`` – listens for ``p`` + Enter on the console to toggle pause/resume.
5. Handles graceful shutdown on SIGINT/SIGTERM, cancelling all tasks and closing resources.

Run from the ``bot-core`` root (so that ``app.contrato`` is importable)::

    python -m conectores.whatsapp.src.main
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

from app.contrato import FILA
from .config import CONFIG, PERFIL_DIR, REDIS_DB, REDIS_HOST, REDIS_PORT, REGISTRO_ENTREGAS_PATH
from .delivery_store import DeliveryStore
from .control import monitor_pause_input
from .scanner import scanner_loop
from .dispatcher import dispatcher_loop
from .utils.playwright_utils import abrir_navegador, fechar_navegador

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

    # Allowlist: fail-closed when empty
    if not CONFIG["contatos_permitidos"]:
        logging.warning(
            "🚫 contatos_permitidos está vazio: o bot NÃO vai atender nenhum contato (fail-closed). "
            "Configure config.yaml ou WHATSAPP_CONTATOS_PERMITIDOS."
        )
    else:
        logging.info("✅ Contatos permitidos: %d", len(CONFIG["contatos_permitidos"]))

    # Delivery journal (single instance lock; intermediate states -> interrupted)
    try:
        store = DeliveryStore(REGISTRO_ENTREGAS_PATH)
    except RuntimeError as erro:
        logging.error("⛔ %s (%s). Encerrando.", erro, REGISTRO_ENTREGAS_PATH)
        sys.exit(1)
    if store.interrompidas_no_inicio:
        logging.warning("⚠️ %d entregas pendentes da execução anterior marcadas como interrupted (não serão reenviadas).",
                        store.interrompidas_no_inicio)

    # Start Playwright browser (persistent profile: QR code only on the first run)
    playwright = await async_playwright().start()
    browser = await abrir_navegador(playwright)
    logging.info("🗂️ Perfil do navegador: %s", PERFIL_DIR)
    page = browser.pages[0] if browser.pages else await browser.new_page()
    await page.goto('https://web.whatsapp.com', wait_until='load')
    await wait_for_whatsapp_ui(page)

    # Create ARQ pool (uses the Redis settings from config.yaml / env)
    arq_pool = await create_pool(
        RedisSettings(
            host=REDIS_HOST,
            port=REDIS_PORT,
            database=REDIS_DB,
        ),
        # The core's worker is launched separately: ``arq app.worker.WorkerSettings``
        default_queue_name=FILA,
    )

    # In-memory queue (same process): scanner -> dispatcher
    fila_respostas: asyncio.Queue = asyncio.Queue()

    logging.info(
        "⚙️ Modo simulação (dry-run): %s | headless: %s",
        CONFIG["mode_dry_run"],
        CONFIG["headless"],
    )

    # Create background tasks
    tasks = []
    tasks.append(asyncio.create_task(scanner_loop(page, arq_pool, lock, CONFIG, fila_respostas, store), name="scanner"))
    tasks.append(asyncio.create_task(dispatcher_loop(page, lock, fila_respostas, store), name="dispatcher"))
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
    store.close()
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

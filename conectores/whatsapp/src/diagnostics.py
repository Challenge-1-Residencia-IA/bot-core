# src/diagnostics.py
"""Diagnostic utilities for the WhatsApp bot.

- Provides a `log_debug` function that logs to the console and, when
  `DEBUG_MODE` is true, also writes to a dedicated debug log file.
- Optionally runs a tiny HTTP health‑check server (enabled when
  `DEBUG_MODE` is true) exposing internal state such as pending ARQ jobs.
"""

import logging
from pathlib import Path
from typing import Any

# Import config flags – they are loaded when this module is imported.
from .config import DEBUG_MODE, DEBUG_LOG_PATH

# Set up a dedicated logger for diagnostics.
_diag_logger = logging.getLogger("diagnostics")
_diag_logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)

if DEBUG_MODE:
    # Ensure the log file exists and add a file handler.
    log_path = Path(DEBUG_LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    _diag_logger.addHandler(file_handler)

# Always keep a console handler (the root logger already does this, but we add for clarity).
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%Y-%m-%d %H:%M:%S")
)
_diag_logger.addHandler(_console_handler)


def log_debug(msg: str, *args: Any, **kwargs: Any) -> None:
    """Log a debug message.

    When ``DEBUG_MODE`` is ``True`` the message is written both to the console
    and to the file specified by ``DEBUG_LOG_PATH``. When it is ``False`` the
    call is a no‑op (the regular logger will still output info‑level messages).
    """
    if DEBUG_MODE:
        _diag_logger.debug(msg, *args, **kwargs)

# ---------------------------------------------------------------------------
# Optional health‑check HTTP server (runs only in debug mode).
# ---------------------------------------------------------------------------
if DEBUG_MODE:
    from aiohttp import web

    async def _health_handler(request: web.Request) -> web.Response:
        # Minimal health payload – extend as needed.
        payload = {
            "status": "ok",
            "debug_mode": DEBUG_MODE,
            # Placeholder values; real implementation could query ARQ.
            "queues": {},
        }
        return web.json_response(payload)

    async def start_health_server(host: str = "127.0.0.1", port: int = 8000) -> web.AppRunner:
        app = web.Application()
        app.router.add_get("/health", _health_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logging.info("🩺 Health‑check server listening on http://%s:%s/health", host, port)
        return runner

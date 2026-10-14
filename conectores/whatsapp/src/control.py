# src/control.py
"""Runtime control utilities for pausing/resuming the bot.

Press the letter ``p`` followed by ``Enter`` in the console to toggle
pause/resume while the bot is running. The module exposes a shared
``pause_event`` (``asyncio.Event``) that the other loops check
periodically.
"""
import asyncio
import logging
import sys

# ``pause_event`` is **set** when the bot is *paused*.
pause_event = asyncio.Event()

def _toggle_pause():
    if pause_event.is_set():
        pause_event.clear()
        logging.info("🟢 Bot resumed (pause toggled off).")
    else:
        pause_event.set()
        logging.info("🔴 Bot paused (pause toggled on).")

async def monitor_pause_input():
    """Background coroutine that reads lines from ``stdin``.

    The user types ``p`` (and ``Enter``) to toggle the pause state.
    The coroutine runs forever until the program exits.
    """
    logging.info("⏸️  Press 'p' + Enter to pause/resume the bot.")
    while True:
        # ``asyncio.to_thread`` runs the blocking ``readline`` in a thread.
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            continue
        cmd = line.strip().lower()
        if cmd == "p":
            _toggle_pause()
        elif cmd == "d":
            # Toggle dry‑run mode (default true) – useful for testing without sending messages.
            from .config import toggle_dry_run
            new_state = toggle_dry_run()
            logging.info("⚙️ Dry‑run mode toggled to %s via command", new_state)

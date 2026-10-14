"""src/db/database.py

Utility module for persisting assistant messages to a local SQLite database.

The original project only referenced a ``save_message`` function but the file was empty,
resulting in an ``ImportError`` when the ARQ worker tried to import it.
This implementation provides a minimal, thread‑safe SQLite helper that creates the
required table on first use and offers a ``save_message`` function used by the worker.

It is deliberately lightweight – no ORM, just the Python ``sqlite3`` module –
so it works in the constrained execution environment of the bot.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

# Determine the database location – a file named ``bot_messages.db`` in the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = _PROJECT_ROOT / "bot_messages.db"

# A lock ensures that concurrent writes from multiple threads/processes do not corrupt the file.
_LOCK = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    """Open a SQLite connection with ``detect_types`` enabled.

    The connection is created with ``check_same_thread=False`` because we may use a
    shared lock to coordinate access across threads.
    """
    return sqlite3.connect(str(_DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)


def _initialize_database() -> None:
    """Create the ``messages`` table if it does not already exist.

    The schema stores the chat name, a unique message identifier, the role (user/assistant),
    the message text, optional metadata and a timestamp.
    """
    with _LOCK:
        conn = _get_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_name TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

# Ensure the database file and table exist on import.
_initialize_database()


def save_message(
    *,
    chat_nome: str,
    mensagem_id: str,
    role: str,
    texto: str,
    metadata: Optional[str] = None,
) -> None:
    """Persist a single message record.

    Parameters
    ----------
    chat_nome: Name of the WhatsApp chat.
    mensagem_id: Unique identifier for the message.
    role: ``"user"`` or ``"assistant"`` – the role of the stored message.
    texto: The message content.
    metadata: Optional JSON or raw string with additional context.
    """
    with _LOCK:
        conn = _get_connection()
        try:
            conn.execute(
                "INSERT INTO messages (chat_name, message_id, role, text, metadata) VALUES (?, ?, ?, ?, ?)",
                (chat_nome, mensagem_id, role, texto, metadata),
            )
            conn.commit()
        finally:
            conn.close()


def get_messages(chat_nome: Optional[str] = None, limit: int = 100):
    """Retrieve recent messages, optionally filtered by ``chat_nome``.

    Returns a list of ``dict`` objects with the column names as keys.
    """
    with _LOCK:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            if chat_nome:
                cur.execute(
                    "SELECT chat_name, message_id, role, text, metadata, created_at FROM messages WHERE chat_name = ? ORDER BY created_at DESC LIMIT ?",
                    (chat_nome, limit),
                )
            else:
                cur.execute(
                    "SELECT chat_name, message_id, role, text, metadata, created_at FROM messages ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in rows]
        finally:
            conn.close()

__all__ = ["save_message", "get_messages"]


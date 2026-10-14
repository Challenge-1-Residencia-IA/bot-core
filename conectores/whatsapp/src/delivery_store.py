# src/delivery_store.py
"""Delivery journal (deduplication + delivery state) for the WhatsApp connector.

Adapted from ``bot-test/delivery_store.py`` (branch ``feat/integracao-rag-whatsapp``
of the ``experimentos`` repo). It stores **no message text and no contact name**:
each message is identified only by ``sha256(chat + "\\0" + message_id)``.

States
------
``baseline``     history already visible when the chat was first opened (never analysed)
``queued``       claimed by the scanner (``claim``)
``analyzing``    job enqueued for the core, waiting for the result
``ready``        reply ready for the dispatcher
``sending``      reserved for sending (``reserve_send``)
``sent``         sent and done
``dry_run``      reply only logged (dry-run mode)
``blocked``      not sent: contact not allowed, reply too long or chat not found
``uncertain``    the send failed midway; never re-sent automatically
``interrupted``  the process stopped while the message was in an intermediate state

On start-up every intermediate state (``queued``, ``analyzing``, ``ready``,
``sending``) becomes ``interrupted`` and is not re-sent. A file lock
(``<db>.lock``) guarantees a single connector instance per database.
"""
import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Optional

ESTADOS = (
    "baseline", "queued", "analyzing", "ready", "sending",
    "sent", "dry_run", "blocked", "uncertain", "interrupted",
)
ESTADOS_INTERMEDIARIOS = ("queued", "analyzing", "ready", "sending")


def message_key(chat: str, message_id: str) -> str:
    """Opaque key for a message: no name or text is ever stored."""
    return hashlib.sha256((chat + "\0" + message_id).encode("utf-8")).hexdigest()


class DeliveryStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.instance_lock = open(str(path) + ".lock", "a+b")
        self.instance_lock.seek(0, 2)
        if self.instance_lock.tell() == 0:
            self.instance_lock.write(b"0")
            self.instance_lock.flush()
        self.instance_lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.instance_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.instance_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.instance_lock.close()
            raise RuntimeError("Outra instância do conector está usando este registro de entregas") from None
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
            key TEXT PRIMARY KEY, state TEXT NOT NULL,
            updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        marcadores = ",".join("?" for _ in ESTADOS_INTERMEDIARIOS)
        cursor = self.db.execute(
            f"UPDATE deliveries SET state='interrupted', updated=CURRENT_TIMESTAMP WHERE state IN ({marcadores})",
            ESTADOS_INTERMEDIARIOS,
        )
        self.interrompidas_no_inicio = cursor.rowcount
        self.db.commit()

    def known(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM deliveries WHERE key=?", (key,)).fetchone() is not None

    def get(self, key: str) -> Optional[str]:
        linha = self.db.execute("SELECT state FROM deliveries WHERE key=?", (key,)).fetchone()
        return linha[0] if linha else None

    def claim(self, key: str, state: str = "queued") -> bool:
        """Insert the key if it is new. Returns ``True`` only for the first claim."""
        self._validar(state)
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO deliveries(key,state) VALUES (?,?)", (key, state))
        return cursor.rowcount == 1

    def state(self, key: str, state: str) -> None:
        self._validar(state)
        with self.db:
            self.db.execute("UPDATE deliveries SET state=?, updated=CURRENT_TIMESTAMP WHERE key=?", (state, key))

    def reserve_send(self, key: str) -> bool:
        """Atomically move ``ready`` -> ``sending``. ``False`` means: do not send."""
        with self.db:
            cursor = self.db.execute(
                "UPDATE deliveries SET state='sending', updated=CURRENT_TIMESTAMP WHERE key=? AND state='ready'",
                (key,),
            )
        return cursor.rowcount == 1

    def close(self) -> None:
        self.db.close()
        self.instance_lock.close()

    @staticmethod
    def _validar(state: str) -> None:
        if state not in ESTADOS:
            raise ValueError(f"Estado de entrega inválido: {state}")

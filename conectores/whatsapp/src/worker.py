# src/worker.py
"""ARQ worker definitions.

- ``RESULT_QUEUE``: a global ``asyncio.Queue`` used by the dispatcher to
  retrieve processed responses.
- ``process_task``: the ARQ job that receives a ``MensagemTarefa`` dict,
  generates a response via the Groq API (delegated to ``src.main``), stores the
  assistant message in SQLite, and puts a ``RespostaTarefa`` onto ``RESULT_QUEUE``.

The worker is intended to be launched with::

    python -m arq src.worker

which will automatically discover the ``process_task`` coroutine.
"""

import asyncio
import logging
from typing import Any, Dict

from src.models import MensagemTarefa, RespostaTarefa
from src.db.database import save_message
from arq.connections import RedisSettings
# Import the async response generator lazily inside process_task to avoid circular imports

# Global queue that the dispatcher consumes from. It is safe to share within the same
# process; workers run in separate processes and cannot directly push into this queue.
# For a multi‑process setup, you would normally use a second Redis queue, but for
# this simple local development scenario we keep it in‑memory.
RESULT_QUEUE: asyncio.Queue = asyncio.Queue()

async def process_task(task_data: Dict[str, Any]):
    """ARQ job that processes a message task.

    ``task_data`` is the ``__dict__`` of a ``MensagemTarefa`` instance.
    The function reconstructs the dataclass, calls the Groq model to obtain a
    response, persists the assistant message, and enqueues a ``RespostaTarefa``
    for the dispatcher.
    """
    try:
        # Reconstruct the dataclass from the dict
        mensagem = MensagemTarefa(**task_data)
        logging.info("⚙️ [Worker] Processando tarefa para chat '%s'", mensagem.chat_nome)

        # Generate response using Groq (async function defined in src.main)
        from src.main import gerar_resposta_groq_async
        resposta_texto = await gerar_resposta_groq_async(mensagem.texto, mensagem.contexto)

        # Save assistant message to SQLite for safety
        asst_msg_id = f"assistant_{mensagem.mensagem_id}_{int(asyncio.get_event_loop().time())}"
        save_message(
            chat_nome=mensagem.chat_nome,
            mensagem_id=asst_msg_id,
            role="assistant",
            texto=resposta_texto,
            metadata="",
        )

        # Create the response task for the dispatcher
        resposta = RespostaTarefa(
            chat_nome=mensagem.chat_nome,
            mensagem_id=mensagem.mensagem_id,
            resposta_texto=resposta_texto,
        )
        await RESULT_QUEUE.put(resposta)
        logging.info("✅ [Worker] Resposta enfileirada para '%s'", mensagem.chat_nome)
    except Exception as e:
        logging.error("❌ [Worker] Erro ao processar tarefa: %s", e)
        raise

# ---------------------------------------------------------------------------
# ARQ worker settings
# ---------------------------------------------------------------------------
from arq import Worker as BaseWorker
from src.config import CONFIG

class WorkerSettings(BaseWorker):
    """Minimal ARQ worker settings.

    The ARQ CLI expects a ``WorkerSettings`` class that defines which functions
    are exposed as jobs and the Redis connection details. This class points to
    the ``process_task`` coroutine defined in this module and uses the same Redis
    configuration loaded from ``config.yaml``.
    """

    # Expose the coroutine as a job named ``process_task``
    functions = [process_task]

    # Redis connection settings – derived from ``src.config.CONFIG``
    redis_settings = RedisSettings(
        host=CONFIG.get("redis_host", "localhost"),
        port=CONFIG.get("redis_port", 6379),
        database=CONFIG.get("redis_db", 0),
    )

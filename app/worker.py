"""Worker ARQ do núcleo: consome o job `analisar_mensagem` publicado pelo conector de WhatsApp.

Para rodar (da raiz do bot-core, com o Redis no ar): `arq app.worker.WorkerSettings`.
"""

from __future__ import annotations

import asyncio
import logging

from arq.connections import RedisSettings

from app import config
from app.contrato import FILA, JOB_ANALISAR, TIMEOUT_JOB_S, PedidoAnalise
from app.graph.grafo import analisar

logger = logging.getLogger(__name__)


async def analisar_mensagem(ctx: dict, pedido: dict) -> dict:
    """Roda o grafo numa thread: os nós fazem chamadas bloqueantes (LLM, busca na web)."""
    resultado = await asyncio.to_thread(analisar, PedidoAnalise(**pedido))
    logger.info("mensagem %s analisada (recusada=%s, llm_indisponivel=%s)",
                pedido.get("mensagem_id"), resultado.recusada, resultado.llm_indisponivel)
    return resultado.para_dict()


async def startup(ctx: dict) -> None:
    from app import llm

    if not llm.disponivel():
        logger.warning("Ollama não respondeu em %s com o modelo %s; as análises vão falhar até ele voltar",
                       config.OLLAMA_URL, config.OLLAMA_MODEL)


class WorkerSettings:
    functions = [analisar_mensagem]
    queue_name = FILA
    redis_settings = RedisSettings.from_dsn(config.REDIS_URL)
    job_timeout = TIMEOUT_JOB_S
    # O LLM roda num único PC: processar uma mensagem por vez evita disputa pela GPU.
    max_jobs = 1
    on_startup = startup


assert analisar_mensagem.__name__ == JOB_ANALISAR

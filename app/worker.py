"""Worker ARQ do núcleo: consome o job `analisar_mensagem` publicado pelo conector de WhatsApp.

Para rodar (da raiz do bot-core, com o Redis no ar): `arq app.worker.WorkerSettings`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

from arq.connections import RedisSettings

from app import config
from app.contrato import FILA, JOB_ANALISAR, PAINEL_ANALISES, PAINEL_HISTORICO, TIMEOUT_JOB_S, PedidoAnalise
from app.graph.grafo import analisar

logger = logging.getLogger(__name__)


async def analisar_mensagem(ctx: dict, pedido: dict) -> dict:
    """Roda o grafo numa thread: os nós fazem chamadas bloqueantes (LLM, busca na web)."""
    inicio = time.time()
    resultado = await asyncio.to_thread(analisar, PedidoAnalise(**pedido))
    logger.info("mensagem %s analisada (recusada=%s, llm_indisponivel=%s)",
                pedido.get("mensagem_id"), resultado.recusada, resultado.llm_indisponivel)
    await registrar_no_painel(ctx.get("redis"), pedido, resultado, time.time() - inicio)
    return resultado.para_dict()


async def registrar_no_painel(redis, pedido: dict, resultado, duracao_s: float) -> None:
    """Guarda um resumo da análise para o painel (sem o texto da mensagem; o contato só pelo hash)."""
    if redis is None:
        return
    from app import llm

    detalhes = resultado.detalhes or {}
    resumo = {
        "quando": time.time(),
        "chat": hashlib.sha256(str(pedido.get("chat_id", "")).encode("utf-8")).hexdigest()[:8],
        "duracao_s": round(duracao_s, 1),
        "recusada": resultado.recusada,
        "motivo": resultado.motivo,
        "llm_indisponivel": resultado.llm_indisponivel,
        "llm": llm.ultimo_uso().get("url"),
        "fontes": len(detalhes.get("fontes", [])),
        "fontes_da_base": detalhes.get("fontes_da_base", 0),
        "web": (detalhes.get("busca") or {}).get("web"),
    }
    try:
        await redis.lpush(PAINEL_ANALISES, json.dumps(resumo, ensure_ascii=False))
        await redis.ltrim(PAINEL_ANALISES, 0, PAINEL_HISTORICO - 1)
    except Exception as erro:  # o painel nunca atrapalha a análise
        logger.warning("Falha ao registrar a análise no painel (%s)", type(erro).__name__)


async def startup(ctx: dict) -> None:
    from app import llm

    for servidor in llm.situacao(timeout=15):
        if servidor["ok"]:
            logger.info("Ollama disponível em %s (%s)", servidor["url"], servidor["modelo"])
        else:
            logger.warning("Ollama indisponível em %s (%s): %s", servidor["url"], servidor["modelo"], servidor["erro"])
    if config.DATABASE_URL:
        # Carrega o modelo de embedding (~1 GB, alguns segundos) antes da primeira mensagem.
        from app.rag.embeddings import modelo_padrao

        try:
            await asyncio.to_thread(modelo_padrao)
        except Exception as erro:  # sem o modelo, a busca segue pela web
            logger.warning("Modelo de embedding não carregou (%s); a busca vai usar só a web", erro)


class WorkerSettings:
    functions = [analisar_mensagem]
    queue_name = FILA
    redis_settings = RedisSettings.from_dsn(config.REDIS_URL)
    job_timeout = TIMEOUT_JOB_S
    # O LLM roda num único PC: processar uma mensagem por vez evita disputa pela GPU.
    max_jobs = 1
    on_startup = startup


assert analisar_mensagem.__name__ == JOB_ANALISAR

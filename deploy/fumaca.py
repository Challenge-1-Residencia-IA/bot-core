"""Teste de fumaça do deploy: um job real pela fila ARQ, respondido pelo worker no ar.

A mensagem é uma saudação, que o guardrail de entrada recusa sem chamar o LLM nem a busca. Assim o
teste confere Redis, worker, contrato da fila e grafo em poucos segundos, sem depender do Ollama. Se
o worker estiver ocupado com uma análise real, o job espera na fila até `--timeout`.

Uso (com as variáveis de `worker.env` e a release no PYTHONPATH):
    PYTHONPATH=~/bot-core-current python ~/bot-core-runtime/bin/fumaca.py [--timeout 300]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from arq import create_pool
from arq.connections import RedisSettings

from app import config
from app.contrato import FILA, JOB_ANALISAR, PedidoAnalise


async def testar(timeout: float) -> dict:
    pool = await create_pool(RedisSettings.from_dsn(config.REDIS_URL), default_queue_name=FILA)
    try:
        identificador = uuid.uuid4().hex[:12]
        pedido = PedidoAnalise(chat_id=f"deploy-fumaca-{identificador}", mensagem_id=identificador, texto="oi")
        job = await pool.enqueue_job(JOB_ANALISAR, pedido.para_dict(), _queue_name=FILA,
                                     _job_id=f"deploy-fumaca:{identificador}")
        return await job.result(timeout=timeout)
    finally:
        await pool.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    try:
        resultado = asyncio.run(testar(args.timeout))
    except Exception as erro:  # qualquer falha (Redis, timeout, erro no job) reprova o deploy
        print(f"FUMAÇA FALHOU: {type(erro).__name__}: {erro}")
        return 1
    if resultado.get("recusada") and resultado.get("motivo") == "apresentacao" and resultado.get("resposta_texto"):
        print("FUMAÇA OK: o worker respondeu pela fila")
        return 0
    print(f"FUMAÇA FALHOU: resposta inesperada {resultado}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

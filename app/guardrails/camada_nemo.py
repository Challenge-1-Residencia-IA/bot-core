"""Rails de entrada executados pelo NeMo Guardrails (NVIDIA).

A configuração fica em `app/guardrails/nemo/` (config.yml e rails.co). O NeMo roda só os rails de entrada,
por `LLMRails.check`, em duas etapas:

1. `verificacao deterministica`: as verificações de `app/guardrails/entrada.py`, registradas como action.
   Recusam sem chamar o LLM.
2. `self check input`: rail da biblioteca do NeMo, com um prompt em português, que pede ao LLM para
   classificar escopo e tentativa de instrução nos casos que os padrões não pegam.

A resposta é gerada pelo grafo; os guardrails de saída ficam em `app/guardrails/saida.py`.
"""

from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from app import config as app_config
from app.guardrails.entrada import DecisaoEntrada, verificar_entrada

CONFIG = Path(__file__).resolve().parent / "nemo"
# O classificador do LLM só é usado em mensagens curtas e sem link. Pedidos fora do escopo costumam
# ser curtos; boatos e notícias, longos. Numa amostra de 35 mensagens legítimas, o Qwen 7B bloqueou
# por engano 1 a 2, sempre textos longos; com este limite, 0 de 70 legítimas foram bloqueadas.
LIMITE_PALAVRAS_LLM = 30
LINK = re.compile(r"https?://")
# O `check` síncrono do NeMo falha se já houver um event loop rodando na thread (Jupyter, FastAPI,
# LangGraph assíncrono). Nesses casos, a verificação roda nesta thread dedicada, que mantém o próprio loop.
_THREAD_NEMO = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nemo")


def _verificar_entrada_deterministica(context: dict | None = None) -> dict:
    """Action do NeMo: aplica `verificar_entrada` à última mensagem do usuário."""
    from app.guardrails import RESPOSTAS_RECUSA

    decisao = verificar_entrada((context or {}).get("user_message", ""))
    texto = RESPOSTAS_RECUSA[decisao.motivo][0] if not decisao.permitida else ""
    return {"permitida": decisao.permitida, "motivo": decisao.motivo, "texto": texto}


@lru_cache(maxsize=1)
def carregar_rails():
    """Carrega a configuração uma vez por processo (o carregamento leva alguns segundos)."""
    from nemoguardrails import LLMRails, RailsConfig

    config = RailsConfig.from_path(str(CONFIG))
    # Mesmo modelo e endereço do resto do núcleo (app/config.py), em vez dos valores do config.yml.
    config.models[0].model = app_config.OLLAMA_MODEL
    config.models[0].parameters["base_url"] = f"{app_config.OLLAMA_URL}/v1"
    rails = LLMRails(config)
    rails.register_action(_verificar_entrada_deterministica, "verificar_entrada_deterministica")
    return rails


def verificar_entrada_nemo(mensagem: str) -> DecisaoEntrada:
    """Mesma interface de `verificar_entrada`, com a classificação pelo LLM como segunda etapa."""
    from nemoguardrails.rails.llm.options import RailStatus, RailType

    decisao = verificar_entrada(mensagem)
    if not decisao.permitida:
        return decisao  # já recusada pelos padrões: o NeMo chegaria ao mesmo resultado sem o LLM
    if len(decisao.mensagem.split()) > LIMITE_PALAVRAS_LLM or LINK.search(decisao.mensagem):
        return decisao
    mensagens = [{"role": "user", "content": decisao.mensagem}]
    try:
        asyncio.get_running_loop()
        dentro_de_loop = True
    except RuntimeError:
        dentro_de_loop = False
    if dentro_de_loop:
        resultado = _THREAD_NEMO.submit(
            lambda: carregar_rails().check(mensagens, rail_types=[RailType.INPUT])).result()
    else:
        resultado = carregar_rails().check(mensagens, rail_types=[RailType.INPUT])
    if resultado.status == RailStatus.BLOCKED:
        return DecisaoEntrada(False, decisao.mensagem, "fora_do_escopo",
                              [*decisao.sinais, f"nemo:{resultado.rail}"])
    return decisao

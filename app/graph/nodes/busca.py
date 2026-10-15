"""Nó de busca: base de checagens primeiro, web só quando a base não traz nenhuma checagem.

Ordem: checagem de um boato parecido com a mensagem → checagens e notícias da base parecidas com a
afirmação (`app/rag/base.py`) → busca na web (`app/rag/busca.py`). A base responde em menos de 1 s e
sempre com fontes escolhidas; a web cobre o que é novo demais para a base. Se o banco não estiver
configurado (`DATABASE_URL` vazia) ou falhar, a busca segue só pela web.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from app import config as app_config
from app.graph.state import EstadoAnalise
from app.rag.busca import buscar_evidencias as buscar_na_web
from app.rag.trechos import relevancia, termos

logger = logging.getLogger(__name__)

# Naturezas que admitem checagem por fontes; opinião, previsão e sátira seguem sem busca.
VERIFICAVEIS = {"fato_verificavel", "fora_de_contexto"}
MAX_EVIDENCIAS = 6


def afirmacao_principal(state: EstadoAnalise) -> dict | None:
    return next((a for a in state.get("afirmacoes", []) if a["natureza"] in VERIFICAVEIS), None)


def tem_relacao(afirmacao: str, evidencia) -> bool:
    """Filtro mínimo de relevância para a web: a fonte precisa ter ao menos um termo da afirmação no
    título ou no trecho. Páginas sem nenhum termo em comum chegam quando o buscador não acha nada
    sobre o assunto e devolve resultados genéricos. Resultados da base já passaram pelo limiar de
    similaridade e não passam por este filtro."""
    if not termos(afirmacao):
        return True
    _, nota = relevancia(afirmacao, evidencia.titulo, evidencia.trecho or evidencia.texto)
    return nota > 0


def buscar_na_base(afirmacao: str, mensagem: str, excluir_urls) -> list:
    from app.rag.base import buscar_na_base as buscar

    return buscar(afirmacao, mensagem, excluir_urls=excluir_urls)


def buscar_evidencias(state: EstadoAnalise, config: RunnableConfig = None) -> dict:
    principal = afirmacao_principal(state)
    if principal is None:
        return {"evidencias": []}
    afirmacao = principal["texto"]
    excluir = list(((config or {}).get("configurable") or {}).get("excluir_urls") or [])

    da_base = []
    if app_config.DATABASE_URL:
        try:
            da_base = buscar_na_base(afirmacao, state.get("mensagem", ""), excluir)
        except Exception as erro:  # banco fora do ar não pode derrubar a análise: a web cobre
            logger.warning("Base de checagens indisponível (%s: %s); buscando só na web",
                           type(erro).__name__, str(erro)[:200])

    web = []
    if not any(e.tipo == "checagem" for e in da_base):
        urls = {e.url for e in da_base} | set(excluir)
        encontradas = buscar_na_web(afirmacao)
        web = [e for e in encontradas if e.url not in urls and tem_relacao(afirmacao, e)]
        if len(web) < len(encontradas):
            logger.info("Busca: %d de %d fontes da web descartadas (sem relação ou repetidas)",
                        len(encontradas) - len(web), len(encontradas))

    evidencias = (da_base + web)[:MAX_EVIDENCIAS]
    logger.info("Busca: %d da base (%d checagens), %d da web",
                len(da_base), sum(e.tipo == "checagem" for e in da_base), len(web))
    # Dicionários, para o estado poder ser serializado pelo checkpointer.
    return {"evidencias": [e.para_dict(com_texto=True) for e in evidencias]}

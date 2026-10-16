"""Nó de busca: base de checagens primeiro, web quando a base não basta.

Ordem: checagem de um boato parecido com a mensagem → checagens e notícias da base parecidas com a
afirmação (`app/rag/base.py`) → busca na web (`app/rag/busca.py`). A base responde em menos de 1 s e
sempre com fontes escolhidas; a web cobre o que é novo demais para a base.

A web também é consultada quando a checagem mais recente da base tem mais de `CHECAGEM_RECENTE_MESES`
meses ou não tem data (*freshness*): um boato antigo pode voltar quando o assunto já mudou, e uma
checagem antiga sozinha levaria o usuário à conclusão errada. Sempre que a base traz checagem com data,
a resposta informa a data da mais recente; se ela for antiga, o aviso diz que o assunto pode ter mudado.
Se o banco não estiver configurado (`DATABASE_URL` vazia) ou falhar, a busca segue só pela web.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from langchain_core.runnables import RunnableConfig

from app import config as app_config
from app.graph.state import EstadoAnalise
from app.rag.busca import buscar_evidencias as buscar_na_web
from app.rag.trechos import relevancia, termos

logger = logging.getLogger(__name__)

# Naturezas que admitem checagem por fontes; opinião, previsão e sátira seguem sem busca.
VERIFICAVEIS = {"fato_verificavel", "fora_de_contexto"}
MAX_EVIDENCIAS = 6
MAX_DA_BASE_COM_WEB = 3
"""Quando a web entra por causa da data, a base fica com no máximo estas vagas, para sobrar espaço ao que é recente."""
CHECAGEM_RECENTE_MESES = int(os.getenv("BASE_CHECAGEM_RECENTE_MESES", "6"))
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
AVISO_CHECAGEM_RECENTE = "A checagem mais recente que encontrei na base é de {data}. Confira a data de cada fonte."
AVISO_CHECAGEM_ANTIGA = (
    "A checagem mais recente que encontrei na base é de {data}. O assunto pode ter mudado desde então: "
    "confira a data de cada fonte."
)


def hoje() -> date:
    return date.today()


def checagem_mais_recente(evidencias) -> date | None:
    """Data da checagem mais recente entre as evidências; `None` se nenhuma checagem tiver data."""
    datas = []
    for e in evidencias:
        if e.tipo == "checagem" and e.data:
            try:
                datas.append(date.fromisoformat(e.data[:10]))
            except ValueError:
                continue
    return max(datas, default=None)


def meses_entre(inicio: date, fim: date) -> int:
    return (fim.year - inicio.year) * 12 + fim.month - inicio.month - (fim.day < inicio.day)


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

    tem_checagem = any(e.tipo == "checagem" for e in da_base)
    recente = checagem_mais_recente(da_base)
    if not tem_checagem:
        motivo_web = "sem_checagem_na_base"
    elif recente is None:
        motivo_web = "checagem_sem_data"
    elif meses_entre(recente, hoje()) > CHECAGEM_RECENTE_MESES:
        motivo_web = "checagem_antiga"
    else:
        motivo_web = None

    web = []
    if motivo_web:
        urls = {e.url for e in da_base} | set(excluir)
        encontradas = buscar_na_web(afirmacao)
        web = [e for e in encontradas if e.url not in urls and tem_relacao(afirmacao, e)]
        if len(web) < len(encontradas):
            logger.info("Busca: %d de %d fontes da web descartadas (sem relação ou repetidas)",
                        len(encontradas) - len(web), len(encontradas))

    if motivo_web in ("checagem_antiga", "checagem_sem_data"):
        da_base = da_base[:MAX_DA_BASE_COM_WEB]
    evidencias = (da_base + web)[:MAX_EVIDENCIAS]
    logger.info("Busca: %d da base (%d checagens), %d da web (motivo: %s)",
                len(da_base), sum(e.tipo == "checagem" for e in da_base), len(web), motivo_web)
    avisos = []
    if recente is not None:
        modelo_aviso = AVISO_CHECAGEM_ANTIGA if motivo_web == "checagem_antiga" else AVISO_CHECAGEM_RECENTE
        avisos.append(modelo_aviso.format(data=f"{MESES[recente.month - 1]}/{recente.year}"))
    # Dicionários, para o estado poder ser serializado pelo checkpointer.
    return {"evidencias": [e.para_dict(com_texto=True) for e in evidencias], "avisos": avisos,
            "busca": {"checagem_mais_recente": recente.isoformat() if recente else None,
                      "web": bool(motivo_web), "motivo_web": motivo_web}}

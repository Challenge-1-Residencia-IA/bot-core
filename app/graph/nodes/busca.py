"""Nó de busca: procura checagens publicadas e páginas da web para a afirmação principal (`app/rag/busca.py`)."""

from __future__ import annotations

import logging

from app.graph.state import EstadoAnalise
from app.rag.busca import buscar_evidencias as buscar
from app.rag.trechos import relevancia, termos

# Naturezas que admitem checagem por fontes; opinião, previsão e sátira seguem sem busca.
VERIFICAVEIS = {"fato_verificavel", "fora_de_contexto"}


def afirmacao_principal(state: EstadoAnalise) -> dict | None:
    return next((a for a in state.get("afirmacoes", []) if a["natureza"] in VERIFICAVEIS), None)


def tem_relacao(afirmacao: str, evidencia) -> bool:
    """Filtro mínimo de relevância: a fonte precisa ter ao menos um termo da afirmação no título ou no
    trecho. Vale também para as checagens; páginas sem nenhum termo em comum chegam quando o buscador
    não acha nada sobre o assunto e devolve resultados genéricos."""
    if not termos(afirmacao):
        return True
    _, nota = relevancia(afirmacao, evidencia.titulo, evidencia.trecho or evidencia.texto)
    return nota > 0


def buscar_evidencias(state: EstadoAnalise) -> dict:
    principal = afirmacao_principal(state)
    if principal is None:
        return {"evidencias": []}
    encontradas = buscar(principal["texto"])
    evidencias = [e for e in encontradas if tem_relacao(principal["texto"], e)]
    if len(evidencias) < len(encontradas):
        logging.info("Busca: %d de %d fontes descartadas por não terem relação com a afirmação",
                     len(encontradas) - len(evidencias), len(encontradas))
    # Dicionários, para o estado poder ser serializado pelo checkpointer.
    return {"evidencias": [e.para_dict(com_texto=True) for e in evidencias]}

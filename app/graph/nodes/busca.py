"""Nó de busca: procura checagens publicadas e páginas da web para a afirmação principal (`app/rag/busca.py`)."""

from __future__ import annotations

from app.graph.state import EstadoAnalise
from app.rag.busca import buscar_evidencias as buscar

# Naturezas que admitem checagem por fontes; opinião, previsão e sátira seguem sem busca.
VERIFICAVEIS = {"fato_verificavel", "fora_de_contexto"}


def afirmacao_principal(state: EstadoAnalise) -> dict | None:
    return next((a for a in state.get("afirmacoes", []) if a["natureza"] in VERIFICAVEIS), None)


def buscar_evidencias(state: EstadoAnalise) -> dict:
    principal = afirmacao_principal(state)
    if principal is None:
        return {"evidencias": []}
    diagnostico: dict = {}
    evidencias = buscar(principal["texto"], diagnostico=diagnostico)
    # Dicionários, para o estado poder ser serializado pelo checkpointer.
    return {"evidencias": [e.para_dict(com_texto=True) for e in evidencias]}

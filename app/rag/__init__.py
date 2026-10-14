"""Busca de evidências, seleção de trechos literais e síntese da tutora (nós do LangGraph)."""

from app.rag.busca import Evidencia, ProvedorBusca, ProvedorDDGS, ResultadoBusca, buscar_evidencias
from app.rag.sintese import RespostaTutora, safe_fallback, sintetizar, validar_resposta
from app.rag.trechos import selecionar_trechos

__all__ = [
    "Evidencia", "ProvedorBusca", "ProvedorDDGS", "ResultadoBusca", "RespostaTutora",
    "buscar_evidencias", "safe_fallback", "selecionar_trechos", "sintetizar", "validar_resposta",
]

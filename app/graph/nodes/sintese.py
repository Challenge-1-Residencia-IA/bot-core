"""Nó de síntese: monta a resposta da tutora com técnicas, citações literais, leitura lateral e fontes
(`app/rag/sintese.py`). Nunca dá veredito; o guardrail de saída confere o texto depois."""

from __future__ import annotations

from app.graph.nodes.busca import afirmacao_principal
from app.graph.state import EstadoAnalise
from app.guardrails import filtrar_texto
from app.rag.busca import Evidencia
from app.rag.sintese import formatar_resposta, sintetizar

NATUREZA_SEM_BUSCA = {
    "opiniao": "A mensagem expressa uma opinião, que não pode ser checada como fato.",
    "previsao": "A mensagem faz uma previsão sobre o futuro, que ainda não pode ser checada como fato.",
    "satira": "A mensagem parece ser sátira ou humor, não uma informação apresentada como fato.",
}


def gerar_sintese(state: EstadoAnalise) -> dict:
    principal = afirmacao_principal(state)
    if principal is None:
        natureza = (state.get("afirmacoes") or [{}])[0].get("natureza", "opiniao")
        texto = (NATUREZA_SEM_BUSCA.get(natureza, NATUREZA_SEM_BUSCA["opiniao"])
                 + " Se ela citar um fato específico (número, data, declaração), envie esse trecho para eu buscar fontes.")
        return {"sintese": {"sem_busca": natureza}, "resposta_texto": texto}

    evidencias = [Evidencia(**e) for e in state.get("evidencias", [])]
    resposta = sintetizar(principal["texto"], evidencias, mensagem=state["mensagem"])
    violacoes = filtrar_texto_gerado(resposta)
    return {"sintese": resposta.para_dict(), "resposta_texto": formatar_resposta(resposta),
            "saida": {"violacoes_removidas": violacoes}}


def filtrar_texto_gerado(resposta) -> list[str]:
    """Guardrail de saída (GR-01, GR-04) só no texto escrito pelo modelo: explicações das técnicas e
    passos de verificação. Citações, títulos e endereços das fontes são cópias e não passam pelo filtro,
    porque a conclusão de uma checagem, atribuída à agência, pode ser apresentada."""
    violacoes: list[str] = []
    for tecnica in resposta.tecnicas:
        tecnica.explicacao, encontradas = filtrar_texto(tecnica.explicacao)
        violacoes += [f"tecnicas: {v}" for v in encontradas]
    passos = []
    for passo in resposta.como_verificar:
        filtrado, encontradas = filtrar_texto(passo)
        violacoes += [f"como_verificar: {v}" for v in encontradas]
        if filtrado:
            passos.append(filtrado)
    resposta.como_verificar = passos
    return violacoes

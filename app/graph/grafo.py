"""Grafo de análise (LangGraph).

guardrail_entrada ─┬─ recusada ──────────────────────────────────────────────► END
                   └─ analisar ─► extracao ─► busca ─► sintese ─► guardrail_saida ─► END

O estado de cada conversa fica no checkpointer com `thread_id = chat_id` (decisão em `README.md`).
Por enquanto o checkpointer é em memória e cada mensagem roda o pipeline completo; o nó de
classificação de intenção e o checkpointer no Postgres entram depois.
"""

from __future__ import annotations

import time
from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.contrato import PedidoAnalise, ResultadoAnalise
from app.graph.nodes.busca import buscar_evidencias
from app.graph.nodes.extracao import extrair_afirmacoes
from app.graph.nodes.guardrails import guardrail_entrada, guardrail_saida, rota_apos_entrada
from app.graph.nodes.sintese import gerar_sintese
from app.graph.state import EstadoAnalise
from app.llm import LLMIndisponivel

MENSAGEM_LLM_INDISPONIVEL = (
    "No momento não consigo analisar mensagens: o modelo de análise está fora do ar. "
    "Tente de novo em alguns minutos."
)


def construir_grafo(checkpointer=None):
    grafo = StateGraph(EstadoAnalise)
    grafo.add_node("guardrail_entrada", guardrail_entrada)
    grafo.add_node("extracao", extrair_afirmacoes)
    grafo.add_node("busca", buscar_evidencias)
    grafo.add_node("sintese", gerar_sintese)
    grafo.add_node("guardrail_saida", guardrail_saida)

    grafo.add_edge(START, "guardrail_entrada")
    grafo.add_conditional_edges("guardrail_entrada", rota_apos_entrada, {"recusada": END, "analisar": "extracao"})
    grafo.add_edge("extracao", "busca")
    grafo.add_edge("busca", "sintese")
    grafo.add_edge("sintese", "guardrail_saida")
    grafo.add_edge("guardrail_saida", END)
    return grafo.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def grafo_padrao():
    return construir_grafo(checkpointer=InMemorySaver())


def analisar(pedido: PedidoAnalise, grafo=None) -> ResultadoAnalise:
    """Executa o grafo para uma mensagem e devolve o resultado no formato do contrato."""
    grafo = grafo or grafo_padrao()
    inicio = time.perf_counter()
    entrada = {
        "chat_id": pedido.chat_id, "mensagem_id": pedido.mensagem_id, "mensagem": pedido.texto,
        "contexto": pedido.contexto,
        # Zera o que veio da mensagem anterior da mesma conversa (o checkpointer guarda o estado).
        "afirmacoes": [], "elementos_manipulacao": [], "evidencias": [], "sintese": {},
        "resposta_texto": "", "saida": {},
    }
    try:
        estado = grafo.invoke(entrada, {"configurable": {"thread_id": pedido.chat_id}})
    except LLMIndisponivel as erro:
        return ResultadoAnalise(resposta_texto=MENSAGEM_LLM_INDISPONIVEL, llm_indisponivel=True,
                                motivo="llm_indisponivel", detalhes={"erro": str(erro)})

    detalhes = {
        "entrada": estado.get("entrada", {}),
        "afirmacoes": estado.get("afirmacoes", []),
        "elementos_manipulacao": estado.get("elementos_manipulacao", []),
        "fontes": [e.get("url") for e in estado.get("evidencias", []) if isinstance(e, dict)],
        "saida": estado.get("saida", {}),
        "tempo_s": round(time.perf_counter() - inicio, 2),
    }
    recusada = estado["entrada"]["recusada"]
    return ResultadoAnalise(resposta_texto=estado["resposta_texto"], recusada=recusada,
                            motivo=estado["entrada"]["motivo"] if recusada else None, detalhes=detalhes)

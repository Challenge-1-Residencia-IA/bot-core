"""Contrato entre o conector de WhatsApp e o núcleo, pela fila ARQ (Redis).

O conector enfileira o job `analisar_mensagem` com um `PedidoAnalise` (como dicionário) e espera o
resultado com `await job.result(timeout=...)`; o núcleo é o worker do ARQ e devolve um
`ResultadoAnalise` (como dicionário). Os dois lados importam este módulo para não divergir.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

JOB_ANALISAR = "analisar_mensagem"
FILA = "bot-core:analises"
TIMEOUT_JOB_S = 300
VERSAO_CONTRATO = 1


@dataclass
class PedidoAnalise:
    chat_id: str
    """Identificador da conversa; vira o `thread_id` do checkpointer do LangGraph."""
    mensagem_id: str
    texto: str
    contexto: list[dict] = field(default_factory=list)
    """Mensagens recentes no formato {"role": "user"|"assistant", "content": "..."}."""

    def para_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResultadoAnalise:
    resposta_texto: str
    """Texto pronto para enviar no WhatsApp."""
    recusada: bool = False
    """True quando o guardrail de entrada recusou a mensagem (sem busca nem LLM)."""
    motivo: str | None = None
    llm_indisponivel: bool = False
    versao_contrato: int = VERSAO_CONTRATO
    detalhes: dict = field(default_factory=dict)
    """Dados para auditoria (guardrails, fontes, tempos); o conector não precisa ler."""

    def para_dict(self) -> dict:
        return asdict(self)

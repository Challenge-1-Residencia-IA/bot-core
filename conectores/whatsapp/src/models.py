# src/models.py
"""Common data structures shared across the bot.

`MensagemTarefa` – the message read by the scanner (converted to
``app.contrato.PedidoAnalise`` before being enqueued for the core).
`RespostaTarefa` – the reply placed on the in‑memory response queue by the
scanner (after the core's ARQ job finishes) and consumed by the dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class MensagemTarefa:
    """Message read by the scanner.

    Attributes
    ----------
    chat_nome: str
        Name of the WhatsApp chat.
    mensagem_id: str
        Unique identifier for the message (from WhatsApp).
    texto: str
        The raw message text.
    metadata: str
        Optional raw metadata string from WhatsApp (kept for debugging).
    contexto: List[Dict[str, str]]
        Recent conversation context used for the LLM prompt.
    """

    chat_nome: str
    mensagem_id: str
    texto: str
    metadata: str = ""
    contexto: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class RespostaTarefa:
    """Reply consumed by the dispatcher.

    Attributes
    ----------
    chat_nome: str
        Name of the chat to which the response belongs.
    mensagem_id: str
        The original message id (so the dispatcher can match the reply).
    resposta_texto: str
        The reply text to send (from ``ResultadoAnalise.resposta_texto``).
    falha: bool
        True when the core did not answer (timeout/error) and ``resposta_texto``
        is the short unavailability message.
    chave: str
        Delivery journal key (``delivery_store.message_key``); empty when the
        reply is not tracked.
    aviso: bool
        True for the short notices of the open access mode (received, waiting
        list, "your turn"). Notices are not tracked in the delivery journal.
    """

    chat_nome: str
    mensagem_id: str
    resposta_texto: str
    falha: bool = False
    chave: str = ""
    aviso: bool = False

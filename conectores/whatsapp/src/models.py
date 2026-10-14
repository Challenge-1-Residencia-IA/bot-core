# src/models.py
"""Common data structures shared across the bot.

`MensagemTarefa` – the task placed on the ARQ queue by the scanner.
`RespostaTarefa` – the result placed on the in‑memory RESULT_QUEUE by the worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class MensagemTarefa:
    """Data sent from the scanner to the ARQ worker.

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
    """Result returned by the ARQ worker and consumed by the dispatcher.

    Attributes
    ----------
    chat_nome: str
        Name of the chat to which the response belongs.
    mensagem_id: str
        The original message id (so the dispatcher can match the reply).
    resposta_texto: str
        The generated reply text from the LLM.
    """

    chat_nome: str
    mensagem_id: str
    resposta_texto: str

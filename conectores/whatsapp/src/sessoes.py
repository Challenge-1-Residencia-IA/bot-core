# src/sessoes.py
"""Admission control for the open access mode (``modo_acesso: aberto``).

The LLM runs on a single GPU, so the number of people served at the same time
is limited (``sessoes_max_ativas``). A session starts with the first message of
a chat and ends after ``sessao_inatividade_s`` without messages, unless the
chat still has an analysis pending. When every slot is taken, new chats go to
a FIFO waiting list and are promoted, in order, as sessions end. Chats with an
active session always come first: their messages are analysed before anyone
on the waiting list is admitted.

Pure logic (no Playwright, no Redis), with an injectable clock for tests.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, List, Set


@dataclass
class Admissao:
    ativa: bool
    """True when the chat has an active session and its message can be analysed."""
    nova: bool
    """True on the first message of a session (or on entering the waiting list)."""
    posicao_espera: int = 0
    """Position on the waiting list (1 = next), when ``ativa`` is False."""


class GerenciadorSessoes:
    def __init__(self, max_ativas: int = 10, inatividade_s: float = 600,
                 relogio: Callable[[], float] = time.monotonic):
        if max_ativas < 1:
            raise ValueError("sessoes_max_ativas deve ser pelo menos 1")
        self.max_ativas = max_ativas
        self.inatividade_s = inatividade_s
        self.relogio = relogio
        self.ativas: Dict[str, float] = {}
        """chat -> time of the last activity."""
        self.espera: "OrderedDict[str, None]" = OrderedDict()
        self.ocupados: Set[str] = set()
        """Chats with an analysis queued or running; their sessions never expire."""

    def admitir(self, chat: str) -> Admissao:
        """Register a new message from ``chat`` and say whether it can be analysed now."""
        agora = self.relogio()
        if chat in self.ativas:
            self.ativas[chat] = agora
            return Admissao(ativa=True, nova=False)
        if chat in self.espera:
            return Admissao(ativa=False, nova=False, posicao_espera=self.posicao_espera(chat))
        # Someone already waiting goes first, even if a slot has just been freed.
        if len(self.ativas) < self.max_ativas and not self.espera:
            self.ativas[chat] = agora
            return Admissao(ativa=True, nova=True)
        self.espera[chat] = None
        return Admissao(ativa=False, nova=True, posicao_espera=len(self.espera))

    def tocar(self, chat: str) -> None:
        """Renew the session of ``chat`` (e.g. when its reply is delivered)."""
        if chat in self.ativas:
            self.ativas[chat] = self.relogio()

    def marcar_ocupado(self, chat: str, ocupado: bool) -> None:
        if ocupado:
            self.ocupados.add(chat)
        else:
            self.ocupados.discard(chat)

    def expirar(self) -> List[str]:
        """End idle sessions and promote chats from the waiting list.

        Returns the promoted chats, in order, so the caller can notify them.
        """
        agora = self.relogio()
        for chat, ultimo in list(self.ativas.items()):
            if chat not in self.ocupados and agora - ultimo >= self.inatividade_s:
                del self.ativas[chat]
        promovidos = []
        while self.espera and len(self.ativas) < self.max_ativas:
            chat, _ = self.espera.popitem(last=False)
            self.ativas[chat] = agora
            promovidos.append(chat)
        return promovidos

    def posicao_espera(self, chat: str) -> int:
        for posicao, nome in enumerate(self.espera, 1):
            if nome == chat:
                return posicao
        return 0

    def esta_ativa(self, chat: str) -> bool:
        return chat in self.ativas

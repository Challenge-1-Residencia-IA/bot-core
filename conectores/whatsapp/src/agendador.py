# src/agendador.py
"""Analysis queue for the open access mode.

The scanner hands every text message to ``Agendador.adicionar``; the
scheduler loop sends them to the core (``analisar_mensagem``) under three rules:

- only chats with an active session (``GerenciadorSessoes``) are served, so
  messages from the waiting list stay here until the chat is promoted;
- one analysis per chat at a time, in order of arrival across chats;
- at most ``analises_simultaneas`` jobs in the core (the LLM runs on a single GPU).

It also sends the short notices of the open mode (received, waiting list,
"your turn") as untracked replies (``RespostaTarefa.aviso``).
"""
from __future__ import annotations

import asyncio
import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Set

from .models import MensagemTarefa, RespostaTarefa
from .scanner import enfileirar_analise
from .sessoes import GerenciadorSessoes


@dataclass
class _Item:
    tarefa: MensagemTarefa
    chave: str


class Agendador:
    def __init__(self, sessoes: GerenciadorSessoes, arq_pool, fila_respostas: asyncio.Queue, store, config):
        self.sessoes = sessoes
        self.arq_pool = arq_pool
        self.fila_respostas = fila_respostas
        self.store = store
        self.config = config
        self.pendentes: Deque[_Item] = deque()
        self.em_andamento: Set[str] = set()
        self.bloqueados: Set[str] = set()
        """Chats bloqueados pelo painel: suas mensagens são ignoradas, sem resposta."""

    # -- notices -----------------------------------------------------------------------------------
    async def avisar(self, chat: str, texto: str) -> None:
        await self.fila_respostas.put(RespostaTarefa(chat_nome=chat, mensagem_id="", resposta_texto=texto, aviso=True))

    def analises_a_frente(self, chat: str) -> int:
        """Analyses that will run before the next one of ``chat`` (running + queued for active sessions)."""
        a_frente = len(self.em_andamento - {chat})
        for item in self.pendentes:
            if item.tarefa.chat_nome == chat:
                break
            if self.sessoes.esta_ativa(item.tarefa.chat_nome):
                a_frente += 1
        return a_frente

    def _texto_previsao(self, chat: str) -> str:
        a_frente = self.analises_a_frente(chat)
        if a_frente == 0:
            return self.config["mensagem_previsao_vez"]
        minutos = max(1, math.ceil((a_frente + 1) * float(self.config["tempo_medio_analise_s"]) / 60))
        return self.config["mensagem_previsao_fila"].format(a_frente=a_frente, minutos=minutos)

    # -- queue -------------------------------------------------------------------------------------
    def pendentes_de(self, chat: str) -> int:
        return sum(1 for item in self.pendentes if item.tarefa.chat_nome == chat)

    async def adicionar(self, tarefa: MensagemTarefa, chave: str) -> bool:
        """Queue a message for analysis and notify the sender.

        Returns False (and notifies) when the chat already has
        ``pendentes_max_por_chat`` messages waiting; the message is then dropped.
        """
        chat = tarefa.chat_nome
        if self.pendentes_de(chat) >= int(self.config["pendentes_max_por_chat"]):
            await self.avisar(chat, self.config["mensagem_excesso"])
            return False
        admissao = self.sessoes.admitir(chat)
        self.pendentes.append(_Item(tarefa, chave))
        self.sessoes.marcar_ocupado(chat, True)
        if admissao.ativa:
            modelo = self.config["mensagem_boas_vindas"] if admissao.nova else self.config["mensagem_recebida"]
            await self.avisar(chat, modelo.format(previsao=self._texto_previsao(chat)))
        elif admissao.nova:
            await self.avisar(chat, self.config["mensagem_espera"].format(posicao=admissao.posicao_espera))
        logging.info("🗂️ [Agendador] Mensagem na fila (sessão %s, %d pendente(s) no total)",
                     "ativa" if admissao.ativa else f"em espera #{admissao.posicao_espera}", len(self.pendentes))
        return True

    def encerrar(self, chat: str) -> int:
        """Encerra a sessão (ou a espera) de ``chat`` e descarta as mensagens dele que ainda estão na fila.

        Uma análise já em andamento termina e é entregue normalmente. Devolve quantas mensagens foram
        descartadas; elas ficam como ``blocked`` no registro de entregas.
        """
        descartadas = [item for item in self.pendentes if item.tarefa.chat_nome == chat]
        for item in descartadas:
            self.pendentes.remove(item)
            self.store.state(item.chave, "blocked")
        self.sessoes.ativas.pop(chat, None)
        self.sessoes.espera.pop(chat, None)
        self.sessoes.marcar_ocupado(chat, chat in self.em_andamento)
        return len(descartadas)

    def _proximo(self):
        for item in self.pendentes:
            chat = item.tarefa.chat_nome
            if self.sessoes.esta_ativa(chat) and chat not in self.em_andamento:
                return item
        return None

    def _concluir(self, chat: str) -> None:
        self.em_andamento.discard(chat)
        self.sessoes.marcar_ocupado(chat, self.pendentes_de(chat) > 0)
        self.sessoes.tocar(chat)

    async def rodar_uma_vez(self) -> None:
        for chat in self.sessoes.expirar():
            logging.info("🎟️ [Agendador] Chat promovido da fila de espera")
            await self.avisar(chat, self.config["mensagem_sua_vez"])
        limite = int(self.config["analises_simultaneas"])
        while len(self.em_andamento) < limite:
            item = self._proximo()
            if item is None:
                break
            self.pendentes.remove(item)
            chat = item.tarefa.chat_nome
            self.em_andamento.add(chat)
            task = await enfileirar_analise(self.arq_pool, item.tarefa, self.fila_respostas,
                                            self.config["timeout_job_s"], self.store, item.chave)
            if task is None:
                self._concluir(chat)
            else:
                task.add_done_callback(lambda _t, c=chat: self._concluir(c))

    async def loop(self, intervalo: float = 0.5) -> None:
        logging.info("🗂️ [Agendador] Ativo: até %d sessões, %s análise(s) por vez",
                     self.sessoes.max_ativas, self.config["analises_simultaneas"])
        while True:
            try:
                await self.rodar_uma_vez()
                await asyncio.sleep(intervalo)
            except asyncio.CancelledError:
                break
            except Exception as erro:
                logging.error("⚠️ [Agendador] Erro: %s", erro)
                await asyncio.sleep(1.0)

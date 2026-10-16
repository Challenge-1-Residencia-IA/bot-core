# src/painel.py
"""Painel de controle do conector, pelo Redis (lido e usado por ``deploy/painel.py``).

A cada segundo o conector publica um instantâneo do estado (sessões, espera, pendências, pausa, modo de
envio) e aplica os comandos que chegaram na fila de comandos, respondendo na lista de eventos. Os
contatos aparecem só pelo identificador ``id_chat`` (hash do nome), nunca pelo nome ou número.

Comandos (JSON): {"acao": "pausar"} | {"acao": "retomar"} | {"acao": "simulacao", "valor": true|false}
| {"acao": "limite", "valor": <n>} | {"acao": "encerrar", "chat": <id>} | {"acao": "bloquear", "chat": <id>}
| {"acao": "desbloquear", "chat": <id>}. O estado alterado vale até o conector reiniciar.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

from app.contrato import PAINEL_COMANDOS, PAINEL_ESTADO_CONECTOR, PAINEL_EVENTOS, PAINEL_HISTORICO

from .config import CONFIG
from .control import pause_event


def id_chat(nome: str) -> str:
    return hashlib.sha256(nome.encode("utf-8")).hexdigest()[:8]


def instantaneo(agendador) -> dict:
    estado = {"quando": time.time(), "modo_acesso": CONFIG["modo_acesso"], "pausado": pause_event.is_set(),
              "simulacao": bool(CONFIG["mode_dry_run"])}
    if agendador is None:
        return estado
    sessoes = agendador.sessoes
    agora = sessoes.relogio()
    pendentes = {}
    for item in agendador.pendentes:
        pendentes[item.tarefa.chat_nome] = pendentes.get(item.tarefa.chat_nome, 0) + 1
    estado.update({
        "limite": sessoes.max_ativas,
        "inatividade_s": sessoes.inatividade_s,
        "ativas": [{"id": id_chat(chat), "inativo_s": round(agora - ultimo), "analisando": chat in agendador.em_andamento,
                    "pendentes": pendentes.get(chat, 0)} for chat, ultimo in sessoes.ativas.items()],
        "espera": [{"id": id_chat(chat), "posicao": posicao, "pendentes": pendentes.get(chat, 0)}
                   for posicao, chat in enumerate(sessoes.espera, 1)],
        "pendentes_total": len(agendador.pendentes),
        "em_andamento": len(agendador.em_andamento),
        "bloqueados": sorted(id_chat(chat) for chat in agendador.bloqueados),
    })
    return estado


def _achar(agendador, chat_id: str) -> str | None:
    candidatos = set(agendador.sessoes.ativas) | set(agendador.sessoes.espera) | agendador.bloqueados
    candidatos |= {item.tarefa.chat_nome for item in agendador.pendentes}
    return next((c for c in candidatos if id_chat(c).startswith(chat_id)), None)


def aplicar(comando: dict, agendador) -> str:
    """Aplica um comando e devolve a descrição do resultado (começa com "ok" ou "erro")."""
    acao = comando.get("acao")
    if acao == "pausar":
        pause_event.set()
        return "ok: bot pausado (não lê nem envia; análises em andamento terminam e saem ao retomar)"
    if acao == "retomar":
        pause_event.clear()
        return "ok: bot retomado"
    if acao == "simulacao":
        CONFIG["mode_dry_run"] = bool(comando.get("valor"))
        return f"ok: simulação {'ligada (nada é enviado)' if CONFIG['mode_dry_run'] else 'desligada (envio real)'}"
    if agendador is None:
        return f"erro: '{acao}' só existe no modo aberto"
    if acao == "limite":
        valor = int(comando.get("valor", 0))
        if valor < 1:
            return "erro: o limite precisa ser pelo menos 1"
        agendador.sessoes.max_ativas = valor
        return f"ok: até {valor} sessões ativas (quem estiver na espera entra conforme abrem vagas)"
    if acao in ("encerrar", "bloquear", "desbloquear"):
        chat = _achar(agendador, str(comando.get("chat", "")))
        if chat is None:
            return f"erro: contato {comando.get('chat')} não encontrado"
        if acao == "desbloquear":
            agendador.bloqueados.discard(chat)
            return f"ok: {id_chat(chat)} desbloqueado"
        descartadas = agendador.encerrar(chat)
        if acao == "bloquear":
            agendador.bloqueados.add(chat)
            return f"ok: {id_chat(chat)} bloqueado ({descartadas} mensagem(ns) na fila descartada(s))"
        return f"ok: sessão de {id_chat(chat)} encerrada ({descartadas} mensagem(ns) na fila descartada(s))"
    return f"erro: comando desconhecido {acao!r}"


async def painel_loop(redis, agendador, intervalo: float = 1.0) -> None:
    """Publica o estado e aplica os comandos; falhas de Redis só geram aviso e não param o conector."""
    logging.info("🎛️ [Painel] Publicando o estado e aceitando comandos pelo Redis")
    while True:
        try:
            while True:
                bruto = await redis.lpop(PAINEL_COMANDOS)
                if bruto is None:
                    break
                try:
                    comando = json.loads(bruto)
                    resultado = aplicar(comando, agendador)
                except (ValueError, TypeError) as erro:
                    comando, resultado = {"bruto": str(bruto)[:100]}, f"erro: comando inválido ({erro})"
                logging.info("🎛️ [Painel] %s → %s", comando.get("acao"), resultado)
                evento = json.dumps({"quando": time.time(), "comando": comando, "resultado": resultado},
                                    ensure_ascii=False)
                await redis.lpush(PAINEL_EVENTOS, evento)
                await redis.ltrim(PAINEL_EVENTOS, 0, PAINEL_HISTORICO - 1)
            await redis.set(PAINEL_ESTADO_CONECTOR, json.dumps(instantaneo(agendador), ensure_ascii=False), ex=10)
        except asyncio.CancelledError:
            raise
        except Exception as erro:  # o painel nunca derruba o conector
            logging.warning("🎛️ [Painel] Falha ao publicar o estado (%s)", type(erro).__name__)
        await asyncio.sleep(intervalo)

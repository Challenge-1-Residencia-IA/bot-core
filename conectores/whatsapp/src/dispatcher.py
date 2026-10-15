# src/dispatcher.py
"""Dispatcher loop – consumes replies from the in‑memory response queue
(filled by the scanner when the core's job finishes) and sends them via Playwright.
It also respects the pause/resume mechanism via ``src.control.pause_event``.

Send rules (adapted from ``bot-test/botfinal.py``):

- dry-run: the reply is only logged (state ``dry_run``);
- notices of the open mode (``aviso``) follow the same rules but are not
  tracked in the delivery journal;
- in ``allowlist`` mode, contact outside ``contatos_permitidos``; in any mode, reply longer than
  ``limite_resposta_chars``: discarded (state ``blocked``);
- chat cannot be opened, or any error before sending: ``blocked``
  (no re-queue, no retry loop);
- ``reserve_send`` must succeed before sending (``ready`` -> ``sending``);
- a failed send becomes ``uncertain`` and is never re-sent automatically.
"""
import asyncio
import logging
from .utils.playwright_utils import (
    abrir_chat_por_nome,
    enviar_mensagem,
    obter_nome_chat_ativo,
)
from .control import pause_event
from .config import CONFIG
from .delivery_store import message_key


def rotulo_chat(nome: str) -> str:
    """Chat name for the logs: in the open mode it is usually a stranger's phone
    number, so only a short hash is logged (data minimisation, LGPD)."""
    if CONFIG["modo_acesso"] == "aberto":
        return "chat#" + message_key(nome, "")[:8]
    return nome


async def _await_if_paused():
    """Block the dispatcher while the bot is paused.
    ``pause_event`` is *set* when paused.
    """
    while pause_event.is_set():
        logging.info("⏸️ Bot is paused – dispatcher sleeping…")
        await asyncio.sleep(0.5)


async def dispatcher_loop(page, lock, fila_respostas: asyncio.Queue, store):
    """Continuously reads from ``fila_respostas`` and delivers the AI answer.
    The loop checks ``pause_event`` on each iteration so the user can
    pause/resume the bot by pressing ``p`` + ``Enter`` in the console.
    """
    logging.info("📤 [Despachante] Inicializado e aguardando respostas prontas…")
    while True:
        try:
            await _await_if_paused()
            resposta_tarefa = await fila_respostas.get()
        except asyncio.CancelledError:
            break
        chave = resposta_tarefa.chave
        rastreada = bool(chave) and not resposta_tarefa.aviso

        def marcar(estado):
            if rastreada:
                store.state(chave, estado)

        def estado_atual():
            return store.get(chave) if rastreada else None

        try:
            tipo = "AVISO" if resposta_tarefa.aviso else "RESPOSTA PRONTA"
            logging.info("🚀 [Despachante] %s (%s)", tipo, chave[:12] or "-")
            logging.info("👤 Destinatário: %s", rotulo_chat(resposta_tarefa.chat_nome))
            logging.info("💬 Resposta:\n%s", resposta_tarefa.resposta_texto)

            if CONFIG["mode_dry_run"]:
                logging.info("🔒 [MODO SIMULAÇÃO] Resposta NÃO enviada ao WhatsApp.")
                marcar("dry_run")
                continue

            if CONFIG["modo_acesso"] == "allowlist" and resposta_tarefa.chat_nome not in CONFIG["contatos_permitidos"]:
                marcar("blocked")
                logging.warning("🚫 [Despachante] Envio %s bloqueado: contato não autorizado.", chave[:12])
                continue
            if len(resposta_tarefa.resposta_texto) > int(CONFIG["limite_resposta_chars"]):
                marcar("blocked")
                logging.warning("🚫 [Despachante] Envio %s bloqueado: resposta com %d caracteres (limite %s).",
                                chave[:12], len(resposta_tarefa.resposta_texto), CONFIG["limite_resposta_chars"])
                continue

            async with lock:
                nome_atual = await obter_nome_chat_ativo(page)
                if nome_atual != resposta_tarefa.chat_nome:
                    logging.info("🔄 [Despachante] Navegando para o chat de '%s'…", rotulo_chat(resposta_tarefa.chat_nome))
                    sucesso = await abrir_chat_por_nome(page, resposta_tarefa.chat_nome)
                    if not sucesso or await obter_nome_chat_ativo(page) != resposta_tarefa.chat_nome:
                        marcar("blocked")
                        logging.warning("⚠️ Não foi possível abrir o chat de '%s'. Envio %s bloqueado.",
                                        rotulo_chat(resposta_tarefa.chat_nome), chave[:12])
                        continue
                if rastreada and not store.reserve_send(chave):
                    logging.warning("⚠️ [Despachante] Envio %s não reservado (estado %s); ignorado.",
                                    chave[:12], store.get(chave))
                    continue
                await enviar_mensagem(page, resposta_tarefa.resposta_texto, resposta_tarefa.chat_nome)
                marcar("sent")
                logging.info("✅ [Despachante] Envio confirmado na interface para '%s'", rotulo_chat(resposta_tarefa.chat_nome))
        except asyncio.CancelledError:
            if estado_atual() == "sending":
                marcar("uncertain")
            raise
        except Exception as erro:
            if estado_atual() == "sending":
                marcar("uncertain")
                logging.error("⚠️ [Despachante] Envio %s não confirmado (%s); sem reenvio automático.",
                              chave[:12], type(erro).__name__)
            else:
                if estado_atual() == "ready":
                    marcar("blocked")
                detalhe = type(erro).__name__ if CONFIG["modo_acesso"] == "aberto" else erro
                logging.error("⚠️ [Despachante] Erro inesperado antes do envio (%s): %s", chave[:12], detalhe)
            await asyncio.sleep(1.0)
        finally:
            fila_respostas.task_done()

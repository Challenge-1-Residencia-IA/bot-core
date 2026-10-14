# src/dispatcher.py
"""Dispatcher loop – consumes replies from the in‑memory response queue
(filled by the scanner when the core's job finishes) and sends them via Playwright.
It also respects the pause/resume mechanism via ``src.control.pause_event``.

Send rules (adapted from ``bot-test/botfinal.py``):

- dry-run: the reply is only logged (state ``dry_run``);
- contact outside ``contatos_permitidos`` or reply longer than
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
    fechar_chat_ativo,
    enviar_mensagem,
    obter_nome_chat_ativo,
)
from .control import pause_event
from .config import CONFIG


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
        try:
            logging.info("🚀 [Despachante] RESPOSTA PRONTA (%s)", chave[:12])
            logging.info("👤 Destinatário: %s", resposta_tarefa.chat_nome)
            logging.info("💬 Resposta:\n%s", resposta_tarefa.resposta_texto)

            if CONFIG["mode_dry_run"]:
                logging.info("🔒 [MODO SIMULAÇÃO] Resposta NÃO enviada ao WhatsApp.")
                store.state(chave, "dry_run")
                async with lock:
                    await fechar_chat_ativo(page)
                continue

            if resposta_tarefa.chat_nome not in CONFIG["contatos_permitidos"]:
                store.state(chave, "blocked")
                logging.warning("🚫 [Despachante] Envio %s bloqueado: contato não autorizado.", chave[:12])
                continue
            if len(resposta_tarefa.resposta_texto) > int(CONFIG["limite_resposta_chars"]):
                store.state(chave, "blocked")
                logging.warning("🚫 [Despachante] Envio %s bloqueado: resposta com %d caracteres (limite %s).",
                                chave[:12], len(resposta_tarefa.resposta_texto), CONFIG["limite_resposta_chars"])
                continue

            async with lock:
                nome_atual = await obter_nome_chat_ativo(page)
                if nome_atual != resposta_tarefa.chat_nome:
                    logging.info("🔄 [Despachante] Navegando para o chat de '%s'…", resposta_tarefa.chat_nome)
                    sucesso = await abrir_chat_por_nome(page, resposta_tarefa.chat_nome)
                    if not sucesso or await obter_nome_chat_ativo(page) != resposta_tarefa.chat_nome:
                        store.state(chave, "blocked")
                        logging.warning("⚠️ Não foi possível abrir o chat de '%s'. Envio %s bloqueado.",
                                        resposta_tarefa.chat_nome, chave[:12])
                        continue
                if not store.reserve_send(chave):
                    logging.warning("⚠️ [Despachante] Envio %s não reservado (estado %s); ignorado.",
                                    chave[:12], store.get(chave))
                    continue
                await enviar_mensagem(page, resposta_tarefa.resposta_texto)
                store.state(chave, "sent")
                logging.info("✅ [Despachante] Mensagem enviada com sucesso para '%s'!", resposta_tarefa.chat_nome)
                await fechar_chat_ativo(page)
        except asyncio.CancelledError:
            if store.get(chave) == "sending":
                store.state(chave, "uncertain")
            raise
        except Exception as erro:
            if store.get(chave) == "sending":
                store.state(chave, "uncertain")
                logging.error("⚠️ [Despachante] Envio %s não confirmado (%s); sem reenvio automático.",
                              chave[:12], type(erro).__name__)
            else:
                if store.get(chave) == "ready":
                    store.state(chave, "blocked")
                logging.error("⚠️ [Despachante] Erro inesperado antes do envio (%s): %s", chave[:12], erro)
            await asyncio.sleep(1.0)
        finally:
            fila_respostas.task_done()

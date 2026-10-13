# src/dispatcher.py
"""Dispatcher loop – consumes results from RESULT_QUEUE and sends them via Playwright.
It also respects the pause/resume mechanism via ``src.control.pause_event``.
"""
import asyncio
import logging
from src.utils.playwright_utils import (
    abrir_chat_por_nome,
    fechar_chat_ativo,
    enviar_mensagem,
    obter_nome_chat_ativo,
)
from src.control import pause_event
from src.worker import RESULT_QUEUE
from src.config import CONFIG
from src.db.database import save_message
import time


async def _await_if_paused():
    """Block the dispatcher while the bot is paused.
    ``pause_event`` is *set* when paused.
    """
    while pause_event.is_set():
        logging.info("⏸️ Bot is paused – dispatcher sleeping…")
        await asyncio.sleep(0.5)


async def dispatcher_loop(page, lock):
    """Continuously reads from RESULT_QUEUE and delivers the AI answer.
    The loop checks ``pause_event`` on each iteration so the user can
    pause/resume the bot by pressing ``p`` + ``Enter`` in the console.
    """
    logging.info("📤 [Despachante] Inicializado e aguardando respostas prontas…")
    while True:
        try:
            await _await_if_paused()
            resposta_tarefa = await RESULT_QUEUE.get()

            logging.info("🚀 [Despachante] ENVIANDO RESPOSTA PRONTA")
            logging.info("👤 Destinatário: %s", resposta_tarefa.chat_nome)
            logging.info("💬 Resposta:\n%s", resposta_tarefa.resposta_texto)

            if CONFIG["mode_dry_run"]:
                logging.info("🔒 [MODO SIMULAÇÃO] Resposta NÃO enviada ao WhatsApp.")
                async with lock:
                    await fechar_chat_ativo(page)
            else:
                async with lock:
                    nome_atual = await obter_nome_chat_ativo(page)
                    if nome_atual != resposta_tarefa.chat_nome:
                        logging.info("🔄 [Despachante] Navegando para o chat de '%s'…", resposta_tarefa.chat_nome)
                        sucesso = await abrir_chat_por_nome(page, resposta_tarefa.chat_nome)
                        if not sucesso:
                            logging.warning("⚠️ Não foi possível abrir o chat de '%s'. Reenfileirando…", resposta_tarefa.chat_nome)
                            await RESULT_QUEUE.put(resposta_tarefa)
                            RESULT_QUEUE.task_done()
                            await asyncio.sleep(1.0)
                            continue
                    await enviar_mensagem(page, resposta_tarefa.resposta_texto)
                    logging.info("✅ [Despachante] Mensagem enviada com sucesso para '%s'!", resposta_tarefa.chat_nome)
                    await fechar_chat_ativo(page)

            # Persist assistant message (extra safety)
            asst_msg_id = f"assistant_{resposta_tarefa.mensagem_id}_{int(time.time())}"
            save_message(
                chat_nome=resposta_tarefa.chat_nome,
                mensagem_id=asst_msg_id,
                role="assistant",
                texto=resposta_tarefa.resposta_texto,
                metadata="",
            )
            RESULT_QUEUE.task_done()
        except asyncio.CancelledError:
            break
        except Exception as erro:
            logging.error("⚠️ [Despachante] Erro inesperado: %s", erro)
            await asyncio.sleep(1.0)

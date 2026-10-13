# src/scanner.py
"""Scanner loop – finds unread chats and enqueues a job for each
message using the ARQ pool. Supports pause/resume via the shared
``pause_event`` from ``src.control``.
"""
import asyncio
import time
import logging
from src.utils.playwright_utils import (
    abrir_chat_por_nome,
    obter_contexto_conversa,
    obter_mensagem_id,
    obter_texto_mensagem,
    mensagem_eh_recebida,
    obter_nome_chat_ativo,
    fechar_chat_ativo,
    chat_eh_grupo,
    encontrar_chats_nao_lidos,
)
from src.models import MensagemTarefa
from src.control import pause_event


async def _await_if_paused():
    """If the bot is paused, wait here until it is resumed.
    ``pause_event`` is *set* when paused.
    """
    while pause_event.is_set():
        logging.info("⏸️ Bot is paused – scanner sleeping…")
        await asyncio.sleep(0.5)


async def scanner_loop(page, arq_pool, lock, config):
    """Continuously scans for unread chats and enqueues them as ARQ jobs.
    The loop respects the ``pause_event`` so the user can pause/resume
    the bot by pressing ``p`` + ``Enter`` in the console.
    """
    logging.info("👀 [Scanner] Monitor de mensagens ativado (varrendo lista lateral + chat ativo)…")
    ultimo_pulso = time.time()
    chats_processados = set()
    chats_ignorados = set()

    while True:
        try:
            # --------------------------------------------------------------
            # Pause handling – block until user resumes the bot
            # --------------------------------------------------------------
            await _await_if_paused()

            # Periodic heartbeat for visibility
            if time.time() - ultimo_pulso > 15:
                logging.info("💓 [Scanner] Em espera... Nenhuma mensagem nova no momento.")
                ultimo_pulso = time.time()

            # 1️⃣ Find unread chats in the sidebar
            async with lock:
                chats_nao_lidos = await encontrar_chats_nao_lidos(page)  # helper from utils

            # 2️⃣ Process each unread chat
            if chats_nao_lidos:
                for chat_info in chats_nao_lidos:
                    nome = chat_info["nome"]
                    if nome in chats_processados or nome in chats_ignorados:
                        continue

                    async with lock:
                        try:
                            await chat_info["elemento"].click()
                            await asyncio.sleep(0.4)
                        except Exception:
                            continue

                        # Ignore groups if configured
                        if config["ignorar_grupos"] and await chat_eh_grupo(page):
                            logging.info(f"👥 [Scanner] Grupo ignorado: '{nome}'")
                            chats_ignorados.add(nome)
                            # Return to the main chat list to avoid staying inside the group
                            async with lock:
                                await fechar_chat_ativo(page)
                            continue

                        mensagens = page.locator('[data-pre-plain-text]')
                        total = await mensagens.count()
                        if total == 0:
                            continue

                        ultima_msg = mensagens.last
                        msg_id = await obter_mensagem_id(ultima_msg)
                        if not msg_id or msg_id in chats_processados:
                            continue

                        recebida = await mensagem_eh_recebida(ultima_msg)
                        if recebida is not True:
                            chats_processados.add(msg_id)
                            continue

                        texto = await obter_texto_mensagem(ultima_msg)
                        if not texto:
                            chats_processados.add(msg_id)
                            continue

                        metadata = (await ultima_msg.get_attribute("data-pre-plain-text")) or ""
                        contexto = await obter_contexto_conversa(page)

                        chats_processados.add(msg_id)

                        tarefa = MensagemTarefa(
                            chat_nome=nome,
                            mensagem_id=msg_id,
                            texto=texto,
                            metadata=metadata,
                            contexto=contexto,
                        )
                        # Enqueue ARQ job
                        await arq_pool.enqueue_job("process_task", tarefa.__dict__)

                        logging.info("⚡ [Scanner] Mensagem enfileirada da lista lateral – %s", nome)
            else:
                # 3️⃣ No unread badges – monitor the currently opened chat
                async with lock:
                    nome_ativo = await obter_nome_chat_ativo(page)
                    if nome_ativo:
                        if nome_ativo in chats_ignorados or nome_ativo in chats_processados:
                            pass
                        elif config["ignorar_grupos"] and await chat_eh_grupo(page):
                            chats_ignorados.add(nome_ativo)
                            logging.info(f"👥 [Scanner] Chat ativo é grupo ignorado: '{nome_ativo}'")
                            await fechar_chat_ativo(page)
                            continue
                        else:
                            mensagens = page.locator('[data-pre-plain-text]')
                            total = await mensagens.count()
                            if total > 0:
                                ultima_msg = mensagens.last
                                msg_id = await obter_mensagem_id(ultima_msg)
                                if msg_id and msg_id not in chats_processados:
                                    recebida = await mensagem_eh_recebida(ultima_msg)
                                    if recebida is True:
                                        texto = await obter_texto_mensagem(ultima_msg)
                                        if texto:
                                            metadata = (await ultima_msg.get_attribute("data-pre-plain-text")) or ""
                                            contexto = await obter_contexto_conversa(page)
                                            chats_processados.add(msg_id)
                                            tarefa = MensagemTarefa(
                                                chat_nome=nome_ativo,
                                                mensagem_id=msg_id,
                                                texto=texto,
                                                metadata=metadata,
                                                contexto=contexto,
                                            )
                                            await arq_pool.enqueue_job("process_task", tarefa.__dict__)
                                            logging.info("⚡ [Scanner] Mensagem enfileirada do chat ativo – %s", nome_ativo)
                                    # Duplicate processing removed

                                        if texto:
                                            metadata = (await ultima_msg.get_attribute("data-pre-plain-text")) or ""
                                            contexto = await obter_contexto_conversa(page)

                                            chats_processados.add(msg_id)

                                            tarefa = MensagemTarefa(
                                                chat_nome=nome_ativo,
                                                mensagem_id=msg_id,
                                                texto=texto,
                                                metadata=metadata,
                                                contexto=contexto,
                                            )
                                            await arq_pool.enqueue_job("process_task", tarefa.__dict__)

                                            logging.info("⚡ [Scanner] Mensagem enfileirada do chat ativo – %s", nome_ativo)
            await asyncio.sleep(config["intervalo_scanner"])
        except asyncio.CancelledError:
            break
        except Exception as erro:
            logging.error("⚠️ [Scanner] Erro na varredura: %s", erro)
            await asyncio.sleep(1.5)

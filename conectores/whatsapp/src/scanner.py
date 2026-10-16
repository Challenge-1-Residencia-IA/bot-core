# src/scanner.py
"""Scanner loop – finds unread chats and enqueues a job for each
message using the ARQ pool. Supports pause/resume via the shared
``pause_event`` from ``src.control``.

Each message becomes an ``analisar_mensagem`` job (``app.contrato``) on the
core's queue. The result is awaited in a separate asyncio task (one per
message), so the scanning loop is never blocked, and the reply is handed to
the dispatcher through an in‑memory ``asyncio.Queue`` in the same process.

Two access modes (``modo_acesso``):

- ``allowlist`` (``scanner_loop``): only chats in ``contatos_permitidos`` are
  opened/analysed (empty = none), polled in turn;
- ``aberto`` (``scanner_aberto_loop``): any individual chat with an unread
  badge is read; its messages go to the ``Agendador`` (limited sessions,
  waiting list), not straight to the core.

Protections (adapted from ``bot-test/botfinal.py``):

- the first time a chat is opened after start-up, the messages already
  visible are marked ``baseline`` and never analysed;
- every message is claimed in the delivery journal (``DeliveryStore``); only a
  successful ``claim`` is enqueued, so nothing is processed twice;
- messages without text (audio, image, sticker) get a short reply from the
  connector itself, without calling the core.
"""
import asyncio
import time
import logging

from app.contrato import FILA, JOB_ANALISAR, PedidoAnalise, ResultadoAnalise
from app.guardrails import RESPOSTAS_RECUSA, verificar_entrada
from .utils.playwright_utils import (
    abrir_chat_por_nome,
    obter_contexto_conversa,
    obter_mensagens_recebidas,
    obter_nome_chat_ativo,
    fechar_chat_ativo,
    chat_eh_grupo,
    listar_chats_nao_lidos,
)
from .config import CONFIG, MENSAGEM_INDISPONIVEL, MENSAGEM_MIDIA, TIMEOUT_JOB_S
from .delivery_store import message_key
from .models import MensagemTarefa, RespostaTarefa
from .control import pause_event

# Strong references to the pending result tasks (asyncio keeps only weak ones).
_TAREFAS_PENDENTES: set = set()


def _rotulo_chat(nome: str) -> str:
    if CONFIG["modo_acesso"] == "aberto":
        return "chat#" + message_key(nome, "")[:8]
    return nome


def _marcar(store, chave, estado):
    if store is not None and chave:
        store.state(chave, estado)


async def _await_if_paused():
    """If the bot is paused, wait here until it is resumed.
    ``pause_event`` is *set* when paused.
    """
    while pause_event.is_set():
        logging.info("⏸️ Bot is paused – scanner sleeping…")
        await asyncio.sleep(0.5)


async def aguardar_resultado(job, tarefa: MensagemTarefa, fila_respostas: asyncio.Queue,
                             timeout: float = TIMEOUT_JOB_S, store=None, chave: str = "") -> RespostaTarefa:
    """Wait for the core's result and put a ``RespostaTarefa`` on ``fila_respostas``.

    On timeout or any error (core down, job failed, malformed result) a short
    unavailability message is queued instead, flagged with ``falha=True``.
    The delivery state goes to ``ready`` in both cases (``interrupted`` if cancelled).
    """
    try:
        bruto = await job.result(timeout=timeout)
        resultado = ResultadoAnalise(**bruto) if isinstance(bruto, dict) else bruto
        texto = (resultado.resposta_texto or "").strip()
        if not texto:
            raise ValueError("resultado sem resposta_texto")
        if resultado.recusada:
            logging.info("🛡️ [Scanner] Núcleo recusou a mensagem de '%s' (%s)", _rotulo_chat(tarefa.chat_nome), resultado.motivo)
        if resultado.llm_indisponivel:
            logging.warning("⚠️ [Scanner] Núcleo respondeu sem LLM para '%s'", _rotulo_chat(tarefa.chat_nome))
        resposta = RespostaTarefa(
            chat_nome=tarefa.chat_nome,
            mensagem_id=tarefa.mensagem_id,
            resposta_texto=texto,
            chave=chave,
        )
    except asyncio.CancelledError:
        _marcar(store, chave, "interrupted")
        raise
    except Exception as erro:
        tipo = "timeout" if isinstance(erro, (asyncio.TimeoutError, TimeoutError)) else type(erro).__name__
        if CONFIG["modo_acesso"] == "aberto":
            logging.error("❌ [Scanner] Núcleo não respondeu para '%s' (%s)", _rotulo_chat(tarefa.chat_nome), tipo)
        else:
            logging.error("❌ [Scanner] Núcleo não respondeu para '%s' (%s: %s)", tarefa.chat_nome, tipo, erro)
        resposta = RespostaTarefa(
            chat_nome=tarefa.chat_nome,
            mensagem_id=tarefa.mensagem_id,
            resposta_texto=MENSAGEM_INDISPONIVEL,
            falha=True,
            chave=chave,
        )
    _marcar(store, chave, "ready")
    await fila_respostas.put(resposta)
    return resposta


async def enfileirar_analise(arq_pool, tarefa: MensagemTarefa, fila_respostas: asyncio.Queue,
                             timeout: float = TIMEOUT_JOB_S, store=None, chave: str = ""):
    """Enqueue ``analisar_mensagem`` for the core and start waiting for its result.

    The WhatsApp message id is used as the ARQ job id, so the same message is
    never enqueued twice (ARQ returns ``None`` for a duplicated job id).
    Returns the asyncio task that awaits the result, or ``None``.
    """
    pedido = PedidoAnalise(
        chat_id=tarefa.chat_nome,
        mensagem_id=tarefa.mensagem_id,
        texto=tarefa.texto,
        contexto=tarefa.contexto,
    )
    try:
        job = await arq_pool.enqueue_job(
            JOB_ANALISAR,
            pedido.para_dict(),
            _job_id=f"whatsapp:{tarefa.mensagem_id}",
            _queue_name=FILA,
        )
    except Exception as erro:
        if CONFIG["modo_acesso"] == "aberto":
            logging.error("❌ [Scanner] Falha ao enfileirar para o núcleo (%s)", type(erro).__name__)
        else:
            logging.error("❌ [Scanner] Falha ao enfileirar para o núcleo (%s: %s)", type(erro).__name__, erro)
        _marcar(store, chave, "ready")
        await fila_respostas.put(RespostaTarefa(
            chat_nome=tarefa.chat_nome,
            mensagem_id=tarefa.mensagem_id,
            resposta_texto=MENSAGEM_INDISPONIVEL,
            falha=True,
            chave=chave,
        ))
        return None
    if job is None:
        logging.info("🔁 [Scanner] Mensagem %s já estava na fila – ignorada", chave[:12] if CONFIG["modo_acesso"] == "aberto" else tarefa.mensagem_id)
        _marcar(store, chave, "interrupted")
        return None
    _marcar(store, chave, "analyzing")
    identificador = chave[:12] if CONFIG["modo_acesso"] == "aberto" else tarefa.mensagem_id
    task = asyncio.create_task(
        aguardar_resultado(job, tarefa, fila_respostas, timeout, store, chave),
        name=f"resultado:{identificador}",
    )
    _TAREFAS_PENDENTES.add(task)
    task.add_done_callback(_TAREFAS_PENDENTES.discard)
    return task


def marcar_baseline(store, nome: str, mensagens) -> int:
    """Mark the messages already visible in a chat as ``baseline`` (never analysed)."""
    marcadas = 0
    for item in mensagens:
        if store.claim(message_key(nome, item["id"]), "baseline"):
            marcadas += 1
    return marcadas


async def processar_chat_aberto(page, nome: str, arq_pool, fila_respostas: asyncio.Queue,
                                store, config, chats_inicializados: set) -> int:
    """Handle the incoming messages of the chat ``nome`` (already open; lock held).

    Returns how many new messages were claimed.
    """
    mensagens = await obter_mensagens_recebidas(page)
    if nome not in chats_inicializados:
        marcadas = marcar_baseline(store, nome, mensagens)
        chats_inicializados.add(nome)
        logging.info("📌 [Scanner] Histórico inicial de '%s' ignorado (%d mensagens)", nome, marcadas)
        return 0

    novas = 0
    contexto = None
    for item in mensagens:
        chave = message_key(nome, item["id"])
        if not store.claim(chave):
            continue
        novas += 1
        texto = (item.get("texto") or "").strip()
        if not texto:
            # Audio, image, sticker, document without caption: answered here, core not called
            logging.info("📎 [Scanner] Mensagem sem texto de '%s' – pedindo texto ou link", nome)
            store.state(chave, "ready")
            await fila_respostas.put(RespostaTarefa(
                chat_nome=nome,
                mensagem_id=item["id"],
                resposta_texto=MENSAGEM_MIDIA,
                chave=chave,
            ))
            continue
        if contexto is None:
            contexto = await obter_contexto_conversa(page, config.get("limite_contexto", 15))
        tarefa = MensagemTarefa(
            chat_nome=nome,
            mensagem_id=item["id"],
            texto=texto,
            contexto=contexto,
        )
        await enfileirar_analise(arq_pool, tarefa, fila_respostas, config["timeout_job_s"], store, chave)
        logging.info("⚡ [Scanner] Mensagem enfileirada – %s", nome)
    return novas


async def inicializar_baseline(page, lock, store, contatos, chats_inicializados: set) -> None:
    """Open each allowed contact once at start-up and mark its history as baseline.

    Without this, the first message a contact sends after start-up would be the
    one that opens the chat and would be swallowed by the baseline. Contacts
    that cannot be opened here get their baseline on the first open instead.
    """
    for nome in contatos:
        async with lock:
            try:
                if not await abrir_chat_por_nome(page, nome):
                    logging.warning("⚠️ [Scanner] Não abri '%s' no início; baseline fica para a primeira abertura", nome)
                    continue
                marcadas = marcar_baseline(store, nome, await obter_mensagens_recebidas(page))
                chats_inicializados.add(nome)
                logging.info("📌 [Scanner] Histórico inicial de '%s' ignorado (%d mensagens)", nome, marcadas)
            except Exception as erro:
                logging.warning("⚠️ [Scanner] Baseline de '%s' adiada (%s: %s)", nome, type(erro).__name__, erro)


async def scanner_loop(page, arq_pool, lock, config, fila_respostas: asyncio.Queue, store):
    """Poll the allowed contacts in turn and enqueue their new messages as ARQ jobs.

    Only chats in ``contatos_permitidos`` are opened (fail-closed allowlist); the
    unread badges are not used, because their markup and text change between
    WhatsApp Web versions and languages. The loop respects ``pause_event``
    (``p`` + ``Enter`` in the console).
    """
    contatos = list(dict.fromkeys(config["contatos_permitidos"]))
    logging.info("👀 [Scanner] Monitor ativado para %d contato(s) permitido(s)", len(contatos))
    ultimo_pulso = time.time()
    chats_ignorados = set()
    chats_inicializados = set()
    avisados = set()

    await inicializar_baseline(page, lock, store, contatos, chats_inicializados)

    while True:
        try:
            await _await_if_paused()

            if time.time() - ultimo_pulso > 15:
                logging.info("💓 [Scanner] Em espera... Nenhuma mensagem nova no momento.")
                ultimo_pulso = time.time()

            for nome in contatos:
                if nome in chats_ignorados:
                    continue
                async with lock:
                    if not await abrir_chat_por_nome(page, nome):
                        if nome not in avisados:
                            logging.warning("⚠️ [Scanner] '%s' não está visível na lista de conversas; "
                                            "abra a conversa uma vez ou envie uma mensagem para ela aparecer", nome)
                            avisados.add(nome)
                        continue
                    avisados.discard(nome)
                    if config["ignorar_grupos"] and await chat_eh_grupo(page):
                        logging.info(f"👥 [Scanner] Grupo ignorado: '{nome}'")
                        chats_ignorados.add(nome)
                        await fechar_chat_ativo(page)
                        continue
                    await processar_chat_aberto(
                        page, nome, arq_pool, fila_respostas, store, config, chats_inicializados
                    )
            await asyncio.sleep(config["intervalo_scanner"])
        except asyncio.CancelledError:
            break
        except Exception as erro:
            logging.error("⚠️ [Scanner] Erro na varredura: %s", type(erro).__name__ if CONFIG["modo_acesso"] == "aberto" else erro)
            await asyncio.sleep(1.5)


# ---------------------------------------------------------------------------
# Open access mode
# ---------------------------------------------------------------------------
async def coletar_novas_aberto(page, nome: str, nao_lidas: int, agendador, fila_respostas: asyncio.Queue,
                               store, config, chats_inicializados: set) -> int:
    """Hand the new incoming messages of the open chat ``nome`` to the scheduler (lock held).

    The first time a chat is seen after start-up, only its last ``nao_lidas``
    incoming messages are new (taken from the unread badge); the older visible
    ones become ``baseline``. Afterwards, every unclaimed incoming message is new.
    Returns how many messages were claimed.
    """
    mensagens = await obter_mensagens_recebidas(page)
    if nome not in chats_inicializados:
        antigas = mensagens[:-nao_lidas] if nao_lidas > 0 else mensagens
        marcar_baseline(store, nome, antigas)
        chats_inicializados.add(nome)

    novas = 0
    contexto = None
    for item in mensagens:
        chave = message_key(nome, item["id"])
        if not store.claim(chave):
            continue
        novas += 1
        if nome in agendador.bloqueados:  # bloqueado pelo painel: ignorado, sem resposta
            store.state(chave, "blocked")
            continue
        texto = (item.get("texto") or "").strip()
        if not texto:
            logging.info("📎 [Scanner] Mensagem sem texto – pedindo texto ou link")
            store.state(chave, "ready")
            await fila_respostas.put(RespostaTarefa(
                chat_nome=nome, mensagem_id=item["id"], resposta_texto=MENSAGEM_MIDIA, chave=chave,
            ))
            continue
        # Deterministic input rails (no LLM): greetings, out-of-scope requests and injection attempts are
        # answered here at once, without the "received" notice, a session slot or a place in the queue.
        # The core still applies the full rails (NeMo + Qwen) to everything that gets through.
        decisao = verificar_entrada(texto)
        if not decisao.permitida:
            logging.info("🛡️ [Scanner] Mensagem recusada no conector (%s)", decisao.motivo)
            store.state(chave, "ready")
            await fila_respostas.put(RespostaTarefa(
                chat_nome=nome, mensagem_id=item["id"], resposta_texto=RESPOSTAS_RECUSA[decisao.motivo][0],
                chave=chave,
            ))
            continue
        if contexto is None:
            contexto = await obter_contexto_conversa(page, config.get("limite_contexto", 15))
        tarefa = MensagemTarefa(chat_nome=nome, mensagem_id=item["id"], texto=texto, contexto=contexto)
        if not await agendador.adicionar(tarefa, chave):
            store.state(chave, "blocked")
    return novas


async def scanner_aberto_loop(page, lock, config, fila_respostas: asyncio.Queue, store, agendador):
    """Read every individual chat with an unread badge and queue its new messages.

    At the start of each cycle the open chat (left open by the dispatcher) is
    checked for messages that arrived while it was open, then closed: with no
    chat open, every new message shows an unread badge in the list.
    """
    logging.info("👀 [Scanner] Modo aberto: atendendo qualquer conversa individual com mensagem não lida")
    ultimo_pulso = time.time()
    chats_ignorados = set()
    chats_inicializados = set()

    while True:
        try:
            await _await_if_paused()
            if time.time() - ultimo_pulso > 15:
                logging.info("💓 [Scanner] Em espera... Nenhuma mensagem nova no momento.")
                ultimo_pulso = time.time()

            async with lock:
                aberto = await obter_nome_chat_ativo(page)
                if aberto is not None:
                    if aberto in chats_inicializados:
                        await coletar_novas_aberto(page, aberto, 0, agendador, fila_respostas, store, config,
                                                   chats_inicializados)
                    await fechar_chat_ativo(page)
                nao_lidos = await listar_chats_nao_lidos(page)

            for chat in nao_lidos:
                nome = chat["nome"]
                if nome in chats_ignorados:
                    continue
                async with lock:
                    if not await abrir_chat_por_nome(page, nome):
                        logging.warning("⚠️ [Scanner] Conversa com mensagem não lida não abriu (%s)", _rotulo_chat(nome))
                        continue
                    if config["ignorar_grupos"] and await chat_eh_grupo(page):
                        logging.info("👥 [Scanner] Grupo ignorado")
                        chats_ignorados.add(nome)
                        await fechar_chat_ativo(page)
                        continue
                    await coletar_novas_aberto(page, nome, int(chat.get("nao_lidas") or 0), agendador,
                                               fila_respostas, store, config, chats_inicializados)
                    await fechar_chat_ativo(page)
            await asyncio.sleep(config["intervalo_scanner"])
        except asyncio.CancelledError:
            break
        except Exception as erro:
            logging.error("⚠️ [Scanner] Erro na varredura: %s", type(erro).__name__)
            await asyncio.sleep(1.5)

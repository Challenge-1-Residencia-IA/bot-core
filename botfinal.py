import asyncio
from dataclasses import dataclass
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

# Garante suporte a UTF-8 no terminal Windows para exibição de emojis e caracteres especiais
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv
from groq import AsyncGroq
from playwright.async_api import async_playwright
from database import init_database, save_message


# ============================================================
# CONFIGURAÇÃO E AMBIENTE
# ============================================================

load_dotenv()

# Modo de simulação (Dry-Run):
# True  = Apenas monitora, lê chats não lidos, gera resposta com Groq e anota no console (NÃO envia mensagens)
# False = Envia as respostas geradas diretamente no WhatsApp
MODO_DRY_RUN = False

API_KEY_GROQ = os.getenv("GROQ_API_KEY")
if not API_KEY_GROQ:
    print("⚠️ AVISO: GROQ_API_KEY não encontrada no arquivo .env!")
    groq_client = None
else:
    groq_client = AsyncGroq(api_key=API_KEY_GROQ)

MODELO_PRINCIPAL = "qwen/qwen3.8-27b"
MODELO_FALLBACK = "openai/gpt-oss-120b"

PERFIL_WHATSAPP = "whatsapp_profile"
INTERVALO_SCANNER = 1.0       # Frequência de varredura de novas mensagens (segundos)
NUM_WORKERS_IA = 5           # Quantidade de agentes de IA processando em paralelo
LIMITE_CONTEXTO = 15          # Mensagens recentes no histórico para dar contexto à IA
IGNORAR_GRUPOS = True         # Se True, ignora conversas e mensagens de grupos



# ============================================================
# ESTRUTURAS DE DADOS (FILAS E MENSAGENS)
# ============================================================

@dataclass
class MensagemTarefa:
    chat_nome: str
    mensagem_id: str
    texto: str
    metadata: str
    contexto: List[Dict[str, str]]


@dataclass
class RespostaTarefa:
    chat_nome: str
    mensagem_id: str
    resposta_texto: str


# ============================================================
# INSTRUÇÕES DO SISTEMA (PROMPT DO ASSISTENTE)
# ============================================================

INSTRUCOES = """
Você é um assistente conversacional inteligente que responde pelo WhatsApp focado em responder fake news.

Seu objetivo é responder de forma natural, direta, amigável e concisa. Sendo um tipo de tutor para ajudar pessoas a desmascarar mensagens tendenciosas ou falsas. Seu objetivo não é necessariamente falar se é falso ou verdadeiro, mas sim fornecer informações confiáveis, links de referência e contexto para que o usuário possa tomar sua própria decisão.

REGRAS:
- Seja direto e responda em tom de mensagem de WhatsApp.
- Não faça introduções desnecessárias.
- Para conversas normais, responda em 1 a 3 parágrafos curtos.
- Mantenha um tom amigável e prestativo.
- Responda em português (a menos que a mensagem recebida esteja em outro idioma).
- Responda apenas à mensagem atual usando o contexto anterior para entender a conversa.
- MENSAGENS DE ÁUDIO: Se o contato enviou uma mensagem de áudio (marcada como [Mensagem de Áudio]), informe com simpatia e clareza que no momento você não consegue ouvir mensagens de áudio e peça gentilmente para a pessoa enviar por texto.
"""


# ============================================================
# INTEGRAÇÃO COM A GROQ (IA ASSÍNCRONA)
# ============================================================

def eh_erro_transitorio(erro: Exception) -> bool:
    """Verifica se o erro da API é transitório (rate limit, timeout, etc)."""
    msg = str(erro).upper()
    return any(termo in msg for termo in [
        "503", "UNAVAILABLE", "429", "RATE LIMIT", "RESOURCE_EXHAUSTED",
        "500", "INTERNAL", "TIMEOUT", "CONNECTION"
    ])


async def gerar_resposta_groq_async(contexto: List[Dict[str, str]], mensagem_atual: str) -> str:
    """
    Gera uma resposta usando Groq assíncrono.
    Tenta o MODELO_PRINCIPAL e, se falhar por instabilidade transitória, tenta o MODELO_FALLBACK.
    """
    if not groq_client:
        return "[Simulação] Chave GROQ_API_KEY não configurada no .env."

    mensagens = [{"role": "system", "content": INSTRUCOES}]

    if contexto:
        for c in contexto:
            if c.get("content") == mensagem_atual and c.get("role") == "user":
                continue
            mensagens.append(c)

    mensagens.append({"role": "user", "content": mensagem_atual})

    modelos = [
        ("Principal", MODELO_PRINCIPAL),
        ("Fallback", MODELO_FALLBACK)
    ]

    ultimo_erro = None

    for nome_mod, mod in modelos:
        try:
            resposta = await groq_client.chat.completions.create(
                model=mod,
                messages=mensagens,
                temperature=0.7,
                max_tokens=600
            )
            conteudo = resposta.choices[0].message.content
            if conteudo and conteudo.strip():
                return conteudo.strip()
        except Exception as erro:
            ultimo_erro = erro
            print(f"⚠️ Erro ao consultar Groq no modelo {nome_mod} ({mod}): {erro}")
            if not eh_erro_transitorio(erro):
                break

    raise RuntimeError(f"Falha na geração com Groq em todos os modelos: {ultimo_erro}")


# ============================================================
# PARSER E EXTRAÇÃO DO DOM DO WHATSAPP (ASSÍNCRONO)
# ============================================================

async def eh_mensagem_de_audio(mensagem) -> bool:
    """Verifica se o elemento da mensagem é uma mensagem de voz ou áudio."""
    try:
        seletores_audio = [
            'button[aria-label*="Reproduzir"]',
            'button[aria-label*="Play"]',
            'button[aria-label*="áudio"]',
            'button[aria-label*="voz"]',
            '[data-testid="audio-play"]',
            '[data-testid="voice-note-play"]',
            '[data-icon="audio-play"]',
            '[data-icon="ptt-play"]',
            '[data-testid="audio-waveform"]',
            'audio'
        ]
        if await mensagem.locator(", ".join(seletores_audio)).count() > 0:
            return True

        texto_bruto = (await mensagem.inner_text()).strip()
        linhas = [l.strip() for l in texto_bruto.split("\n") if l.strip()]
        for l in linhas:
            if re.match(r"^\d{1,2}:\d{2}$", l) and len(linhas) <= 3:
                return True
    except Exception:
        pass
    return False


async def mensagem_eh_recebida(mensagem) -> Optional[bool]:
    """Identifica se a mensagem veio do contato (True) ou se foi enviada por nós (False)."""
    try:
        atual = mensagem
        for _ in range(4):
            atual = atual.locator("xpath=..")
        classe = (await atual.get_attribute("class")) or ""

        if "x1ew7x2d" in classe or "message-in" in classe:
            return True
        if "x1g5lz36" in classe or "message-out" in classe:
            return False

        parent_box = mensagem.locator(
            "xpath=ancestor::div[contains(@class, 'message-in') or contains(@class, 'message-out')]"
        ).first
        if await parent_box.count() > 0:
            box_class = (await parent_box.get_attribute("class")) or ""
            if "message-in" in box_class:
                return True
            if "message-out" in box_class:
                return False
    except Exception:
        pass
    return None


async def obter_texto_mensagem(mensagem) -> str:
    """Extrai o texto legível da mensagem ou identifica se é mensagem de voz/áudio."""
    try:
        if await eh_mensagem_de_audio(mensagem):
            texto_bruto = (await mensagem.inner_text()).strip()
            duracao_match = re.search(r"\b\d{1,2}:\d{2}\b", texto_bruto)
            duracao = f" ({duracao_match.group(0)})" if duracao_match else ""
            return f"[Mensagem de Áudio{duracao}]"

        elemento = mensagem.locator('[data-testid="selectable-text"]').first
        if await elemento.count() > 0:
            texto = (await elemento.inner_text()).strip()
            if texto:
                if re.match(r"^\d{1,2}:\d{2}$", texto):
                    return f"[Mensagem de Áudio ({texto})]"
                return texto
        return (await mensagem.inner_text()).strip()
    except Exception:
        return ""


async def obter_mensagem_id(mensagem) -> Optional[str]:
    """Gera um identificador único para a mensagem (metadados + texto)."""
    try:
        texto = await obter_texto_mensagem(mensagem)
        metadata = await mensagem.get_attribute("data-pre-plain-text")
        if not texto and not metadata:
            return None
        return f"{metadata or ''}|{texto}"
    except Exception:
        return None


async def obter_nome_chat_ativo(page) -> str:
    """Obtém o nome do contato ou grupo atualmente aberto no cabeçalho."""
    try:
        header = page.locator("#main header").first
        if await header.count() > 0:
            titulo_elem = header.locator('span[title], div[role="button"] span[dir="auto"]').first
            if await titulo_elem.count() > 0:
                nome = (await titulo_elem.get_attribute("title")) or (await titulo_elem.inner_text()).strip()
                if nome:
                    return nome
    except Exception:
        pass
    return "Conversa Atual"


async def obter_contexto_conversa(page) -> List[Dict[str, str]]:
    """Lê o histórico recente da conversa aberta para enriquecer o contexto da IA."""
    contexto = []
    try:
        mensagens = page.locator('#main [data-pre-plain-text]')
        total = await mensagens.count()
        inicio = max(0, total - LIMITE_CONTEXTO)

        for i in range(inicio, total):
            msg = mensagens.nth(i)
            texto = await obter_texto_mensagem(msg)
            if not texto:
                continue

            recebida = await mensagem_eh_recebida(msg)
            if recebida is True:
                contexto.append({"role": "user", "content": texto})
            elif recebida is False:
                contexto.append({"role": "assistant", "content": texto})
    except Exception:
        pass
    return contexto



async def enviar_mensagem(page, texto: str):
    """Digita e envia uma mensagem no WhatsApp."""
    texto = texto.strip()
    if not texto:
        return

    seletores = [
        'div[contenteditable="true"][data-tab="10"]',
        'footer div[contenteditable="true"]',
        'div[contenteditable="true"][role="textbox"]'
    ]

    campo = None
    for sel in seletores:
        candidato = page.locator(sel).last
        if (await candidato.count() > 0) and (await candidato.is_visible()):
            campo = candidato
            break

    if not campo:
        print("⚠️ Caixa de digitação do WhatsApp não encontrada.")
        return

    await campo.click()
    await campo.fill(texto)
    await campo.press("Enter")
    await asyncio.sleep(0.4)


async def abrir_chat_por_nome(page, nome_contato: str) -> bool:
    """
    Localiza e abre o chat pelo nome na lista lateral ou pela barra de busca.
    """
    if not nome_contato:
        return False

    try:
        # 1. Tenta clicar diretamente na linha do contato na barra lateral (#pane-side)
        chat_item = page.locator('#pane-side').locator(f'span[title="{nome_contato}"]').first
        if await chat_item.count() > 0 and await chat_item.is_visible():
            await chat_item.click()
            await asyncio.sleep(0.5)
            return True

        # Tenta localizar por texto na linha do chat
        chat_by_text = page.locator('#pane-side div[role="listitem"], #pane-side div[role="row"]').filter(has_text=nome_contato).first
        if await chat_by_text.count() > 0 and await chat_by_text.is_visible():
            await chat_by_text.click()
            await asyncio.sleep(0.5)
            return True

        # 2. Se não estiver visível diretamente, pesquisa na barra de busca do WhatsApp
        seletores_busca = [
            '[data-testid="chat-list-search"]',
            '#side div[contenteditable="true"]',
            'div[contenteditable="true"][data-tab="3"]',
            'div[contenteditable="true"][role="textbox"]'
        ]

        search_box = None
        for sel in seletores_busca:
            candidato = page.locator(sel).first
            if await candidato.count() > 0 and await candidato.is_visible():
                search_box = candidato
                break

        if search_box:
            await search_box.click()
            await search_box.fill(nome_contato)
            await asyncio.sleep(0.8)

            # Procura o resultado da busca
            resultado = page.locator('#pane-side').locator(f'span[title="{nome_contato}"]').first
            if await resultado.count() == 0:
                resultado = page.locator('#pane-side div[role="listitem"]').filter(has_text=nome_contato).first

            if await resultado.count() > 0:
                await resultado.click()
                await asyncio.sleep(0.6)
                # Limpa a busca
                await page.keyboard.press("Escape")
                return True
            else:
                await page.keyboard.press("Escape")
    except Exception as erro:
        print(f"❌ Erro ao tentar abrir chat '{nome_contato}': {erro}")
    return False



async def fechar_chat_ativo(page):
    """
    Deseleciona/fecha a conversa atualmente aberta no WhatsApp Web,
    retornando à tela inicial para permitir que novas mensagens gerem badges na barra lateral.
    """
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def chat_eh_grupo(page, chat_row=None) -> bool:
    """
    Verifica se o chat atual (ou linha do chat) é um grupo ou comunidade.
    Utiliza:
    1. Ícones e marcadores de grupo/comunidade na lista lateral e no cabeçalho do CHAT ATIVO (#main header).
    2. Presença de múltiplos remetentes distintos nas mensagens do chat aberto (#main).
    3. Descrição/Subtítulo no cabeçalho (#main header) (ex: participantes, dados do grupo).
    """
    try:
        # 1. Se fornecida a linha da lista lateral (#pane-side)
        if chat_row:
            seletores_lateral = [
                '[data-icon="default-group"]',
                '[data-icon="community"]',
                '[data-icon="community-outline"]',
                '[data-icon="newsletter"]',
                '[data-testid="default-group"]',
                '[data-testid="community-icon"]',
                'span[data-icon*="group"]',
                'span[data-icon*="community"]'
            ]
            for sel in seletores_lateral:
                if await chat_row.locator(sel).count() > 0:
                    return True

            avatar = chat_row.locator('div[role="img"]').first
            if await avatar.count() > 0:
                rotulo = (await avatar.get_attribute("aria-label")) or ""
                if any(termo in rotulo.lower() for termo in ["grupo", "group", "comunidade", "community"]):
                    return True

        # 2. Verificação no cabeçalho do CHAT ATIVO (#main header)
        # Importante: usar '#main header' para NÃO confundir com o cabeçalho da barra lateral (#side header)!
        main_header = page.locator("#main header").first
        if await main_header.count() > 0:
            seletores_header_icone = [
                '[data-icon="default-group"]',
                '[data-icon="community"]',
                '[data-testid="default-group"]',
                '[data-testid="community-icon"]'
            ]
            for sel in seletores_header_icone:
                if await main_header.locator(sel).count() > 0:
                    return True

            elementos_sub = main_header.locator('span[title], span[dir="auto"], div[role="button"] + span')
            for i in range(await elementos_sub.count()):
                elem = elementos_sub.nth(i)
                texto = ((await elem.get_attribute("title")) or (await elem.inner_text()) or "").strip().lower()
                if "," in texto or any(kw in texto for kw in [
                    "clique aqui para dados do grupo",
                    "click here for group info",
                    "admin",
                    "você, ",
                    "you, ",
                    "participantes",
                    "participants"
                ]):
                    return True

        # 3. Verificação por Múltiplos Remetentes distintos nas mensagens do chat aberto (#main)
        mensagens = page.locator('#main [data-pre-plain-text]')
        total_msgs = await mensagens.count()
        if total_msgs > 0:
            remetentes_recebidos = set()
            inicio = max(0, total_msgs - 10)
            for i in range(inicio, total_msgs):
                msg = mensagens.nth(i)
                if await mensagem_eh_recebida(msg) is True:
                    pre_text = (await msg.get_attribute("data-pre-plain-text")) or ""
                    match = re.search(r"\]\s*([^:]+):", pre_text)
                    if match:
                        remetente = match.group(1).strip()
                        remetentes_recebidos.add(remetente)

            # Mais de um remetente em mensagens recebidas -> Grupo!
            if len(remetentes_recebidos) > 1:
                return True

    except Exception:
        pass

    return False



# ============================================================
# DETECÇÃO DE CHATS NÃO LIDOS
# ============================================================

async def encontrar_chats_nao_lidos(page, chats_ignorados: set = None) -> List[Dict[str, Any]]:
    """Varre #pane-side procurando por conversas com badge de não lidas."""
    chats_nao_lidos = []
    nomes_filtros_ignorados = {
        "tudo", "todas", "não lidas", "nao lidas", "favoritas", "favoritos",
        "grupos", "all", "unread", "favorites", "groups", "conversas", "chats"
    }

    try:
        pane = page.locator("#pane-side")
        if await pane.count() == 0:
            return chats_nao_lidos

        chat_rows = pane.locator('div[role="listitem"], div[role="row"]')
        total_rows = await chat_rows.count()

        seletores_badge = [
            'span[aria-label*="não lida"]',
            'span[aria-label*="não lidas"]',
            'span[aria-label*="nao lida"]',
            'span[aria-label*="unread"]',
            '[data-testid="unread-count"]',
            '[data-testid="icon-unread-count"]',
            'span[aria-label*="mensagem não lida"]',
            'span[aria-label*="mensagens não lidas"]'
        ]
        badge_query = ", ".join(seletores_badge)

        for i in range(total_rows):
            row = chat_rows.nth(i)
            try:
                if not (await row.is_visible()):
                    continue

                role = (await row.get_attribute("role")) or ""
                if role == "tab" or (await row.locator('[role="tab"]').count() > 0):
                    continue

                badge = row.locator(badge_query).first
                if (await badge.count() == 0) or not (await badge.is_visible()):
                    continue

                span_nome = row.locator('span[title], span[dir="auto"]').first
                nome_contato = ""
                if await span_nome.count() > 0:
                    nome_contato = ((await span_nome.get_attribute("title")) or (await span_nome.inner_text())).strip()

                if not nome_contato or nome_contato.lower() in nomes_filtros_ignorados:
                    continue

                if chats_ignorados and nome_contato in chats_ignorados:
                    continue

                # Se a opção IGNORAR_GRUPOS estiver ativa e o chat for grupo, pula
                if IGNORAR_GRUPOS and await chat_eh_grupo(page, chat_row=row):
                    continue

                chats_nao_lidos.append({
                    "nome": nome_contato,
                    "elemento": row
                })
            except Exception:
                continue

    except Exception:
        pass

    return chats_nao_lidos


# ============================================================
# TRÊS CAMADAS ASSÍNCRONAS: SCANNER, POOL DE IA E DESPACHANTE
# ============================================================

async def scanner_loop(
    page,
    fila_entrada: asyncio.Queue,
    lock_pagina: asyncio.Lock,
    mensagens_processadas: set,
    chats_em_processamento: set,
    chats_ignorados: set
):
    """
    PRODUTOR: Varre o WhatsApp, abre chats não lidos rapidamente,
    e também monitora o chat atualmente aberto na tela caso receba mensagens novas ao vivo.
    """
    print("👀 [Scanner] Monitor de mensagens ativado (varrendo lista lateral + chat ativo)...")
    ultimo_pulso = time.time()

    while True:
        try:
            # Pulso periódico no console a cada 15 segundos para confirmar que o scanner está vivo
            if time.time() - ultimo_pulso > 15:
                print("💓 [Scanner] Em espera... Nenhuma mensagem nova no momento.")
                ultimo_pulso = time.time()

            # 1. Busca chats não lidos na lista lateral
            chats_nao_lidos = []
            async with lock_pagina:
                chats_nao_lidos = await encontrar_chats_nao_lidos(page, chats_ignorados)

            # 2. Se houver conversas não lidas na lista lateral, abre e processa
            if chats_nao_lidos:
                for chat_info in chats_nao_lidos:
                    nome = chat_info["nome"]

                    if nome in chats_em_processamento or nome in chats_ignorados:
                        continue

                    async with lock_pagina:
                        try:
                            await chat_info["elemento"].click()
                            await asyncio.sleep(0.4)
                        except Exception:
                            continue

                        # Se for grupo, ignora
                        if IGNORAR_GRUPOS and await chat_eh_grupo(page):
                            print(f"👥 [Scanner] Grupo detectado e ignorado: '{nome}'")
                            chats_ignorados.add(nome)
                            continue

                        mensagens = page.locator('[data-pre-plain-text]')
                        total = await mensagens.count()
                        if total == 0:
                            continue

                        ultima_msg = mensagens.last
                        msg_id = await obter_mensagem_id(ultima_msg)

                        if not msg_id or msg_id in mensagens_processadas:
                            continue

                        recebida = await mensagem_eh_recebida(ultima_msg)
                        if recebida is not True:
                            mensagens_processadas.add(msg_id)
                            continue

                        texto = await obter_texto_mensagem(ultima_msg)
                        if not texto:
                            mensagens_processadas.add(msg_id)
                            continue

                        metadata = (await ultima_msg.get_attribute("data-pre-plain-text")) or ""
                        contexto = await obter_contexto_conversa(page)

                        mensagens_processadas.add(msg_id)
                        chats_em_processamento.add(nome)

                        tarefa = MensagemTarefa(
                            chat_nome=nome,
                            mensagem_id=msg_id,
                            texto=texto,
                            metadata=metadata,
                            contexto=contexto
                        )

                        await fila_entrada.put(tarefa)

                        print("\n" + "📥" * 35)
                        print(f"⚡ [Scanner] MENSAGEM ENFILEIRADA DA LISTA LATERAL!")
                        print(f"👤 Contato   : {nome}")
                        print(f"💬 Mensagem  : \"{texto}\"")
                        print(f"📊 Fila de Espera: {fila_entrada.qsize()} tarefa(s)")
                        print("📥" * 35 + "\n")
            else:
                # 3. Se não houver chats com bolinha verde, monitora o chat que já está ABERTO na tela
                async with lock_pagina:
                    nome_ativo = await obter_nome_chat_ativo(page)
                    if nome_ativo and nome_ativo != "Conversa Atual":
                        if nome_ativo in chats_ignorados or nome_ativo in chats_em_processamento:
                            pass
                        elif IGNORAR_GRUPOS and await chat_eh_grupo(page):
                            chats_ignorados.add(nome_ativo)
                            print(f"👥 [Scanner] Chat aberto é grupo ignorado: '{nome_ativo}'")
                        else:
                            mensagens = page.locator('[data-pre-plain-text]')
                            total = await mensagens.count()
                            if total > 0:
                                ultima_msg = mensagens.last
                                msg_id = await obter_mensagem_id(ultima_msg)

                                if msg_id and msg_id not in mensagens_processadas:
                                    recebida = await mensagem_eh_recebida(ultima_msg)
                                    if recebida is True:
                                        texto = await obter_texto_mensagem(ultima_msg)
                                        if texto:
                                            metadata = (await ultima_msg.get_attribute("data-pre-plain-text")) or ""
                                            contexto = await obter_contexto_conversa(page)

                                            mensagens_processadas.add(msg_id)
                                            chats_em_processamento.add(nome_ativo)

                                            # Persistência local no banco de dados SQLite
                                            save_message(
                                                chat_nome=nome_ativo,
                                                mensagem_id=msg_id,
                                                role="user",
                                                texto=texto,
                                                metadata=metadata
                                            )

                                            tarefa = MensagemTarefa(
                                                chat_nome=nome_ativo,
                                                mensagem_id=msg_id,
                                                texto=texto,
                                                metadata=metadata,
                                                contexto=contexto
                                            )
                                            await fila_entrada.put(tarefa)

                                            print("\n" + "📥" * 35)
                                            print(f"⚡ [Scanner] MENSAGEM ENFILEIRADA DO CHAT ATIVO NA TELA!")
                                            print(f"👤 Contato   : {nome_ativo}")
                                            print(f"💬 Mensagem  : \"{texto}\"")
                                            print(f"📊 Fila de Espera: {fila_entrada.qsize()} tarefa(s)")
                                            print("📥" * 35 + "\n")
                                    else:
                                        mensagens_processadas.add(msg_id)



            await asyncio.sleep(INTERVALO_SCANNER)

        except asyncio.CancelledError:
            break
        except Exception as erro:
            print(f"⚠️ [Scanner] Erro na varredura: {erro}")
            await asyncio.sleep(1.5)


async def worker_ia_loop(
    worker_id: int,
    fila_entrada: asyncio.Queue,
    fila_saida: asyncio.Queue
):
    """
    WORKER / AGENTE DE IA: Puxa tarefas da fila_entrada e gera
    respostas via Groq de forma paralela e não bloqueante.
    """
    print(f"🤖 [Worker IA {worker_id}] Pronto e aguardando tarefas...")

    while True:
        try:
            tarefa: MensagemTarefa = await fila_entrada.get()

            print(f"🧠 [Worker IA {worker_id}] Processando resposta para '{tarefa.chat_nome}'...")
            t_inicio = time.time()

            try:
                resposta = await gerar_resposta_groq_async(tarefa.contexto, tarefa.texto)
                duracao = time.time() - t_inicio

                print(f"✨ [Worker IA {worker_id}] Resposta gerada para '{tarefa.chat_nome}' em {duracao:.2f}s!")

                resp_tarefa = RespostaTarefa(
                    chat_nome=tarefa.chat_nome,
                    mensagem_id=tarefa.mensagem_id,
                    resposta_texto=resposta
                )
                await fila_saida.put(resp_tarefa)

            except Exception as erro:
                print(f"❌ [Worker IA {worker_id}] Erro ao gerar resposta para '{tarefa.chat_nome}': {erro}")

            finally:
                fila_entrada.task_done()

        except asyncio.CancelledError:
            break
        except Exception as erro:
            print(f"⚠️ [Worker IA {worker_id}] Erro inesperado: {erro}")
            await asyncio.sleep(1.0)


async def dispatcher_loop(
    page,
    fila_saida: asyncio.Queue,
    lock_pagina: asyncio.Lock,
    chats_em_processamento: set
):
    """
    CONSUMIDOR / DESPACHANTE: Puxa respostas prontas da fila_saida,
    localiza o chat correspondente no WhatsApp e realiza o envio seguro.
    """
    print("📤 [Despachante] Inicializado e aguardando respostas prontas...")

    while True:
        try:
            resposta_tarefa: RespostaTarefa = await fila_saida.get()

            print("\n" + "═" * 70)
            print(f"🚀 [Despachante] ENVIANDO RESPOSTA PRONTA")
            print(f"👤 Destinatário: {resposta_tarefa.chat_nome}")
            print(f"💬 Resposta:\n{resposta_tarefa.resposta_texto}")
            print("═" * 70)

            if MODO_DRY_RUN:
                print("🔒 [MODO SIMULAÇÃO] Resposta NÃO enviada ao WhatsApp.")
                async with lock_pagina:
                    await fechar_chat_ativo(page)
            else:
                async with lock_pagina:
                    # Verifica se o chat já está aberto ou precisa ser aberto
                    nome_atual = await obter_nome_chat_ativo(page)
                    if nome_atual != resposta_tarefa.chat_nome:
                        print(f"🔄 [Despachante] Navegando para o chat de '{resposta_tarefa.chat_nome}'...")
                        sucesso = await abrir_chat_por_nome(page, resposta_tarefa.chat_nome)
                        if not sucesso:
                            print(f"⚠️ Não foi possível abrir o chat de '{resposta_tarefa.chat_nome}'. Tentando novamente mais tarde.")
                            # Reenfileira para tentar depois
                            await fila_saida.put(resposta_tarefa)
                            fila_saida.task_done()
                            await asyncio.sleep(1.0)
                            continue

                    await enviar_mensagem(page, resposta_tarefa.resposta_texto)
                    print(f"✅ [Despachante] Mensagem enviada com sucesso para '{resposta_tarefa.chat_nome}'!")

                    # Fecha o chat ativo para retornar à tela inicial e permitir que novas mensagens gerem badges
                    await fechar_chat_ativo(page)

            # Persistência local (SQLite) da resposta do assistente
            asst_msg_id = f"assistant_{resposta_tarefa.mensagem_id}_{int(time.time())}"
            save_message(
                chat_nome=resposta_tarefa.chat_nome,
                mensagem_id=asst_msg_id,
                role="assistant",
                texto=resposta_tarefa.resposta_texto
            )

            # Libera o contato para receber novas perguntas
            chats_em_processamento.discard(resposta_tarefa.chat_nome)
            fila_saida.task_done()


        except asyncio.CancelledError:
            break
        except Exception as erro:
            print(f"⚠️ [Despachante] Erro no envio: {erro}")
            await asyncio.sleep(1.0)


# ============================================================
# FLUXO PRINCIPAL DE EXECUÇÃO
# ============================================================

async def main_async():
    print("=" * 70)
    print("🤖 BOT WHATSAPP MULTI-AGENTES & SISTEMA DE FILAS ASSÍNCRONAS")
    print(f"⚙️  Modo Atual: {'🔒 SIMULAÇÃO / DRY-RUN (Apenas Console)' if MODO_DRY_RUN else '🚀 PRODUÇÃO (Envio Real Ativado)'}")
    print(f"🧠 Modelos Groq: Principal = {MODELO_PRINCIPAL} | Fallback = {MODELO_FALLBACK}")
    print(f"⚡ Workers de IA Concorrentes: {NUM_WORKERS_IA}")
    print("=" * 70)
    print()

    # Filas e estruturas de sincronização
    fila_entrada = asyncio.Queue()
    fila_saida = asyncio.Queue()
    lock_pagina = asyncio.Lock()
    mensagens_processadas = set()
    chats_em_processamento = set()
    chats_ignorados = set()

    async with async_playwright() as p:
        print("🌐 Inicializando Chromium...")
        context = await p.chromium.launch_persistent_context(
            PERFIL_WHATSAPP,
            headless=False,
            viewport={"width": 1280, "height": 850}
        )

        page = context.pages[0] if context.pages else await context.new_page()

        print("🔗 Acessando WhatsApp Web...")
        await page.goto("https://web.whatsapp.com")

        print("⏳ Aguardando carregamento da interface do WhatsApp...")
        try:
            await page.wait_for_selector("#pane-side", timeout=60000)
            print("✅ WhatsApp Web conectado e carregado com sucesso!")
        except Exception:
            print("\n⚠️ O painel lateral ainda não foi detectado.")
            print("👉 Se necessário, escaneie o QR Code no navegador.")
            await asyncio.to_thread(input, "👉 Pressione ENTER quando o WhatsApp estiver logado...")

        # Mapeia mensagens antigas do chat aberto, mas deixa a última para ser processada se for recebida
        try:
            mensagens_iniciais = page.locator('[data-pre-plain-text]')
            total_iniciais = await mensagens_iniciais.count()
            # Mapeia todas menos a última
            for i in range(max(0, total_iniciais - 1)):
                msg_id = await obter_mensagem_id(mensagens_iniciais.nth(i))
                if msg_id:
                    mensagens_processadas.add(msg_id)
            print(f"📚 {len(mensagens_processadas)} mensagens antigas catalogadas.")
        except Exception:
            pass

        print("\n🚀 Iniciando pipelines assíncronos (Scanner + Workers + Despachante)...\n")

        # Inicializa o pool de tarefas concorrentes
        tarefas = []

        # 1. Scanner (Produtor)
        tarefas.append(asyncio.create_task(
            scanner_loop(page, fila_entrada, lock_pagina, mensagens_processadas, chats_em_processamento, chats_ignorados)
        ))

        # 2. Pool de Workers IA
        for worker_id in range(1, NUM_WORKERS_IA + 1):
            tarefas.append(asyncio.create_task(
                worker_ia_loop(worker_id, fila_entrada, fila_saida)
            ))

        # 3. Despachante (Consumidor)
        tarefas.append(asyncio.create_task(
            dispatcher_loop(page, fila_saida, lock_pagina, chats_em_processamento)
        ))

        try:
            await asyncio.gather(*tarefas)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n🛑 Finalizando bot e cancelando tarefas assíncronas...")
            for t in tarefas:
                t.cancel()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n👋 Programa encerrado pelo usuário.")


if __name__ == "__main__":
    main()

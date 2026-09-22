import os
import re
import sys
import time

# Garante suporte a UTF-8 no terminal Windows para exibição de emojis e caracteres especiais
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv
from groq import Groq
from playwright.sync_api import sync_playwright


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
    groq_client = Groq(api_key=API_KEY_GROQ)

MODELO_PRINCIPAL = "qwen/qwen3.8-27b"
MODELO_FALLBACK = "openai/gpt-oss-120b"

PERFIL_WHATSAPP = "whatsapp_profile"
INTERVALO_LOOP = 1.5
COOLDOWN_ERRO = 15
LIMITE_CONTEXTO = 15


# ============================================================
# INSTRUÇÕES DO SISTEMA (PROMPT DO ASSISTENTE)
# ============================================================

INSTRUCOES = """
Você é um assistente conversacional inteligente que responde pelo WhatsApp.

Seu objetivo é responder de forma natural, direta, amigável e concisa.

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
# FUNÇÕES DE INTEGRAÇÃO COM A GROQ (IA)
# ============================================================

def eh_erro_transitorio(erro):
    """
    Verifica se o erro da API é transitório (rate limit, timeout, etc).
    """
    msg = str(erro).upper()
    return any(termo in msg for termo in [
        "503", "UNAVAILABLE", "429", "RATE LIMIT", "RESOURCE_EXHAUSTED",
        "500", "INTERNAL", "TIMEOUT", "CONNECTION"
    ])


def gerar_resposta_groq(contexto, mensagem_atual):
    """
    Gera uma resposta usando Groq com base no contexto da conversa.
    Tenta o MODELO_PRINCIPAL e, se falhar por instabilidade, tenta o MODELO_FALLBACK.
    """
    if not groq_client:
        return "[Simulação] Chave GROQ_API_KEY não configurada no .env."

    mensagens = [{"role": "system", "content": INSTRUCOES}]

    # Adiciona contexto recente (excluindo duplicações da mensagem atual se houver)
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
            resposta = groq_client.chat.completions.create(
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
# FUNÇÕES DO WHATSAPP (SELETORES & DOM)
# ============================================================

def eh_mensagem_de_audio(mensagem):
    """
    Verifica se o elemento da mensagem é uma mensagem de voz ou áudio.
    """
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
        if mensagem.locator(", ".join(seletores_audio)).count() > 0:
            return True

        texto_bruto = mensagem.inner_text().strip()
        linhas = [l.strip() for l in texto_bruto.split("\n") if l.strip()]
        for l in linhas:
            if re.match(r"^\d{1,2}:\d{2}$", l) and len(linhas) <= 3:
                return True
    except Exception:
        pass
    return False


def mensagem_eh_recebida(mensagem):
    """
    Identifica se a mensagem veio do contato (recebida)
    ou se foi enviada pelo próprio usuário do bot.
    """
    try:
        atual = mensagem
        for _ in range(4):
            atual = atual.locator("xpath=..")
        classe = atual.get_attribute("class") or ""

        if "x1ew7x2d" in classe or "message-in" in classe:
            return True
        if "x1g5lz36" in classe or "message-out" in classe:
            return False

        parent_box = mensagem.locator(
            "xpath=ancestor::div[contains(@class, 'message-in') or contains(@class, 'message-out')]"
        ).first
        if parent_box.count() > 0:
            box_class = parent_box.get_attribute("class") or ""
            if "message-in" in box_class:
                return True
            if "message-out" in box_class:
                return False
    except Exception:
        pass

    return None


def obter_mensagem_id(mensagem):
    """
    Gera um identificador único para a mensagem (data/hora + remetente + texto).
    """
    try:
        texto = obter_texto_mensagem(mensagem)
        metadata = mensagem.get_attribute("data-pre-plain-text")
        if not texto and not metadata:
            return None
        return f"{metadata or ''}|{texto}"
    except Exception:
        return None


def obter_texto_mensagem(mensagem):
    """
    Extrai o texto legível da mensagem ou identifica se é mensagem de voz/áudio.
    """
    try:
        if eh_mensagem_de_audio(mensagem):
            texto_bruto = mensagem.inner_text().strip()
            duracao_match = re.search(r"\b\d{1,2}:\d{2}\b", texto_bruto)
            duracao = f" ({duracao_match.group(0)})" if duracao_match else ""
            return f"[Mensagem de Áudio{duracao}]"

        elemento = mensagem.locator('[data-testid="selectable-text"]').first
        if elemento.count() > 0:
            texto = elemento.inner_text().strip()
            if texto:
                if re.match(r"^\d{1,2}:\d{2}$", texto):
                    return f"[Mensagem de Áudio ({texto})]"
                return texto

        texto_geral = mensagem.inner_text().strip()
        if re.match(r"^\d{1,2}:\d{2}$", texto_geral):
            return f"[Mensagem de Áudio ({texto_geral})]"

        return texto_geral
    except Exception:
        return ""


def obter_nome_chat_ativo(page):
    """
    Obtém o nome do contato ou grupo atualmente aberto no cabeçalho do WhatsApp.
    """
    try:
        header = page.locator("header").first
        if header.count() > 0:
            # Tenta encontrar título pelo atributo title ou texto
            titulo_elem = header.locator('span[title], div[role="button"] span[dir="auto"]').first
            if titulo_elem.count() > 0:
                nome = titulo_elem.get_attribute("title") or titulo_elem.inner_text().strip()
                if nome:
                    return nome
    except Exception:
        pass
    return "Conversa Atual"


def obter_contexto_conversa(page):
    """
    Lê o histórico recente da conversa aberta para enriquecer o contexto da IA.
    """
    contexto = []
    try:
        mensagens = page.locator('[data-pre-plain-text]')
        total = mensagens.count()
        inicio = max(0, total - LIMITE_CONTEXTO)

        for i in range(inicio, total):
            msg = mensagens.nth(i)
            texto = obter_texto_mensagem(msg)
            if not texto:
                continue

            recebida = mensagem_eh_recebida(msg)
            if recebida is True:
                contexto.append({"role": "user", "content": texto})
            elif recebida is False:
                contexto.append({"role": "assistant", "content": texto})
    except Exception:
        pass
    return contexto


def enviar_mensagem(page, texto):
    """
    Digita e envia uma mensagem no WhatsApp.
    (Só é chamada se MODO_DRY_RUN for False).
    """
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
        if candidato.count() > 0 and candidato.is_visible():
            campo = candidato
            break

    if not campo:
        print("⚠️ Caixa de digitação do WhatsApp não encontrada.")
        return

    campo.click()
    campo.fill(texto)
    campo.press("Enter")
    time.sleep(0.4)


# ============================================================
# DETECÇÃO DE CHATS NÃO LIDOS & TROCA DE CONVERSAS (CHAT SWITCHING)
# ============================================================

def encontrar_chats_nao_lidos(page):
    """
    Varre a lista lateral (#pane-side) procurando por conversas
    que possuam badge/indicador de mensagens não lidas.
    Ignora abas de filtro como 'Tudo', 'Não lidas', 'Grupos', etc.
    """
    chats_nao_lidos = []
    nomes_filtros_ignorados = {
        "tudo", "todas", "não lidas", "nao lidas", "favoritas", "favoritos",
        "grupos", "all", "unread", "favorites", "groups", "conversas", "chats"
    }

    try:
        pane = page.locator("#pane-side")
        if pane.count() == 0:
            return chats_nao_lidos

        # Localiza as linhas de conversas na lista lateral
        chat_rows = pane.locator('div[role="listitem"], div[role="row"]')
        total_rows = chat_rows.count()

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
                if not row.is_visible():
                    continue

                # Ignora botões ou abas de filtro superiores
                role = row.get_attribute("role") or ""
                if role == "tab" or row.locator('[role="tab"]').count() > 0:
                    continue

                # Procura por badge de não lidas dentro deste chat
                badge = row.locator(badge_query).first
                if badge.count() == 0 or not badge.is_visible():
                    continue

                # Extrai o nome do contato
                span_nome = row.locator('span[title], span[dir="auto"]').first
                nome_contato = ""
                if span_nome.count() > 0:
                    nome_contato = (span_nome.get_attribute("title") or span_nome.inner_text()).strip()

                if not nome_contato or nome_contato.lower() in nomes_filtros_ignorados:
                    continue

                chats_nao_lidos.append({
                    "nome": nome_contato,
                    "elemento": row,
                    "badge": badge
                })
            except Exception:
                continue

    except Exception:
        pass

    return chats_nao_lidos


def abrir_chat(page, info_chat):
    """
    Clica na conversa para abri-la e aguarda o carregamento das mensagens.
    """
    try:
        nome = info_chat.get("nome", "Contato")
        elemento = info_chat.get("elemento")
        if elemento and elemento.count() > 0:
            print(f"🔄 Abrindo conversa: 💬 [{nome}]...")
            elemento.click()
            time.sleep(1.2)
            return True
    except Exception as erro:
        print(f"❌ Não foi possível abrir o chat {info_chat.get('nome')}: {erro}")
    return False


# ============================================================
# PROCESSAMENTO DE MENSAGENS NO CHAT ATUAL
# ============================================================

def processar_chat_aberto(page, mensagens_processadas, mensagens_em_espera):
    """
    Analisa as mensagens no chat atualmente aberto na tela.
    Permite navegar pelas mensagens recebidas não respondidas:
      - [ENTER] : Envia a resposta gerada.
      - [X]     : Pula para a próxima mensagem da mesma conversa.
      - [Z]     : Pula e fecha a conversa atual (sai para a próxima conversa da lista).
    """
    nome_chat = obter_nome_chat_ativo(page)
    mensagens = page.locator('[data-pre-plain-text]')
    total = mensagens.count()
    if total == 0:
        return 0

    # Localiza o índice da última mensagem enviada por nós (para catalogar o histórico antigo)
    ultimo_indice_enviado = -1
    for i in range(total - 1, -1, -1):
        if mensagem_eh_recebida(mensagens.nth(i)) is False:
            ultimo_indice_enviado = i
            break

    # Marca todas as mensagens anteriores à nossa última resposta como já processadas
    if ultimo_indice_enviado >= 0:
        for j in range(0, ultimo_indice_enviado + 1):
            m_id = obter_mensagem_id(mensagens.nth(j))
            if m_id:
                mensagens_processadas.add(m_id)

    # O ponto de partida para mensagens recebidas pendentes
    indice_inicial = max(0, ultimo_indice_enviado + 1)
    agora = time.time()
    novas_mensagens_processadas = 0

    for i in range(indice_inicial, total):
        mensagem = mensagens.nth(i)
        mensagem_id = obter_mensagem_id(mensagem)

        if not mensagem_id or mensagem_id in mensagens_processadas:
            continue

        if mensagem_id in mensagens_em_espera:
            if agora < mensagens_em_espera[mensagem_id]:
                continue
            del mensagens_em_espera[mensagem_id]

        recebida = mensagem_eh_recebida(mensagem)
        if recebida is not True:
            mensagens_processadas.add(mensagem_id)
            continue

        texto = obter_texto_mensagem(mensagem)
        if not texto:
            mensagens_processadas.add(mensagem_id)
            continue

        metadata = mensagem.get_attribute("data-pre-plain-text") or ""

        # Log detalhado da mensagem detectada
        print("\n" + "═" * 70)
        print(f"📩 NOVA MENSAGEM RECEBIDA!")
        print(f"👤 Chat / Contato : {nome_chat}")
        if metadata:
            print(f"🕒 Metadados      : {metadata.strip()}")
        print(f"💬 Conteúdo       : \"{texto}\"")
        print("═" * 70)

        try:
            contexto = obter_contexto_conversa(page)
            print("🧠 Consultando Groq (Qwen / Fallback)...")
            resposta = gerar_resposta_groq(contexto, texto)

            print("\n" + "─" * 70)
            print("📋 RESPOSTA GERADA PELA IA:")
            print("─" * 70)
            print(resposta)
            print("─" * 70)

            if MODO_DRY_RUN:
                print("🔒 [MODO SIMULAÇÃO ATIVO] => A mensagem NÃO foi enviada ao contato.")
                print("   (Para ativar o envio real, mude MODO_DRY_RUN = False)")
                mensagens_processadas.add(mensagem_id)
                novas_mensagens_processadas += 1
            else:
                print("\n⌨️  OPÇÕES DE AÇÃO:")
                print("   👉 [ENTER] : Enviar esta resposta")
                print("   👉 [X]     : Pular esta mensagem (ir para a próxima nesta conversa)")
                print("   👉 [Z]     : Fechar conversa e ir para o próximo contato")
                escolha = input("Escolha (ENTER = Enviar / X = Próx Msg / Z = Sair do Chat): ").strip().lower()

                if escolha == "z":
                    print("🚪 Fechando conversa atual e pulando para o próximo contato...")
                    mensagens_processadas.add(mensagem_id)
                    try:
                        page.keyboard.press("Escape")
                        time.sleep(0.3)
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                    return novas_mensagens_processadas

                elif escolha == "x":
                    print("⏭️ Mensagem pulada. Avançando para a próxima mensagem desta conversa...")
                    mensagens_processadas.add(mensagem_id)
                    continue

                else:
                    print("📤 Enviando resposta para o WhatsApp...")
                    enviar_mensagem(page, resposta)
                    print("✅ Resposta enviada com sucesso ao contato!")
                    mensagens_processadas.add(mensagem_id)
                    novas_mensagens_processadas += 1

        except Exception as erro:
            print(f"\n❌ Erro ao processar resposta: {erro}")
            mensagens_em_espera[mensagem_id] = time.time() + COOLDOWN_ERRO
            print(f"⏳ Tentará novamente em {COOLDOWN_ERRO}s.\n")

    return novas_mensagens_processadas


# ============================================================
# FLUXO PRINCIPAL DE EXECUÇÃO
# ============================================================

def main():
    print("=" * 70)
    print("🤖 BOT WHATSAPP - MONITOR DE CHATS & LEITURA DE NÃO LIDAS")
    print(f"⚙️  Modo Atual: {'🔒 SIMULAÇÃO / DRY-RUN (Apenas Console)' if MODO_DRY_RUN else '🚀 PRODUÇÃO (Envio Real Ativado)'}")
    print(f"🧠 Modelos Groq: Principal = {MODELO_PRINCIPAL} | Fallback = {MODELO_FALLBACK}")
    print("=" * 70)
    print()

    with sync_playwright() as p:
        print("🌐 Inicializando Chromium...")
        context = p.chromium.launch_persistent_context(
            PERFIL_WHATSAPP,
            headless=False,
            viewport={"width": 1280, "height": 850}
        )

        page = context.pages[0] if context.pages else context.new_page()

        print("🔗 Acessando WhatsApp Web...")
        page.goto("https://web.whatsapp.com")

        print("⏳ Aguardando carregamento da interface do WhatsApp...")
        try:
            page.wait_for_selector("#pane-side", timeout=60000)
            print("✅ WhatsApp Web conectado e carregado com sucesso!")
        except Exception:
            print("\n⚠️ O painel lateral ainda não foi detectado.")
            print("👉 Se necessário, escaneie o QR Code no navegador.")
            input("👉 Pressione ENTER quando o WhatsApp estiver logado...")

        # Mapeia mensagens do chat atualmente aberto (se houver) para não reprocessar histórico antigo
        mensagens_processadas = set()
        mensagens_em_espera = {}

        try:
            mensagens_iniciais = page.locator('[data-pre-plain-text]')
            for i in range(mensagens_iniciais.count()):
                msg_id = obter_mensagem_id(mensagens_iniciais.nth(i))
                if msg_id:
                    mensagens_processadas.add(msg_id)
            print(f"📚 {len(mensagens_processadas)} mensagens antigas catalogadas.")
        except Exception:
            pass

        print("\n👀 Monitorando lista de conversas e mensagens não lidas...\n")

        while True:
            try:
                # 1. Busca conversas com indicador de mensagens não lidas na lista lateral
                chats_nao_lidos = encontrar_chats_nao_lidos(page)

                if chats_nao_lidos:
                    print(f"🔔 Detectado(s) {len(chats_nao_lidos)} chat(s) com mensagem(ns) não lida(s)!")
                    for chat_info in chats_nao_lidos:
                        sucesso = abrir_chat(page, chat_info)
                        if sucesso:
                            processar_chat_aberto(page, mensagens_processadas, mensagens_em_espera)
                            time.sleep(0.8)
                else:
                    # 2. Se não houver chats não lidos pendentes, monitora o chat ativo na tela
                    processar_chat_aberto(page, mensagens_processadas, mensagens_em_espera)

                time.sleep(INTERVALO_LOOP)

            except KeyboardInterrupt:
                print("\n🛑 Bot finalizado pelo usuário via terminal.")
                break
            except Exception as erro:
                print(f"\n⚠️ Erro durante o loop de monitoramento: {erro}")
                time.sleep(2)


if __name__ == "__main__":
    main()

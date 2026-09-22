import os
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
    Extrai o texto legível da mensagem.
    """
    try:
        elemento = mensagem.locator('[data-testid="selectable-text"]').first
        if elemento.count() > 0:
            texto = elemento.inner_text().strip()
            if texto:
                return texto
        return mensagem.inner_text().strip()
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
    Retorna uma lista de dicionários com informações do chat.
    """
    chats_nao_lidos = []
    try:
        pane = page.locator("#pane-side")
        if pane.count() == 0:
            return chats_nao_lidos

        # Seletores comuns de badges de mensagens não lidas no WhatsApp Web
        seletores_badge = [
            '#pane-side [aria-label*="não lida"]',
            '#pane-side [aria-label*="não lidas"]',
            '#pane-side [aria-label*="unread"]',
            '#pane-side [data-testid="unread-count"]',
            '#pane-side [data-testid="icon-unread-count"]',
            '#pane-side span[aria-label*="mensagem não lida"]'
        ]

        badges = page.locator(", ".join(seletores_badge))
        total_badges = badges.count()

        for i in range(total_badges):
            badge = badges.nth(i)
            try:
                if not badge.is_visible():
                    continue

                # Localiza a linha (row/listitem) da conversa no painel lateral
                chat_row = badge.locator('xpath=ancestor::div[@role="listitem" or @role="row" or @tabindex="-1"][1]')
                if chat_row.count() == 0:
                    chat_row = badge.locator('xpath=ancestor::div[contains(@class, "x10l6tqk") or contains(@class, "_ak72")][1]')

                # Tenta extrair o nome do contato do chat na lista lateral
                nome_contato = "Contato Desconhecido"
                if chat_row.count() > 0:
                    span_nome = chat_row.locator('span[title], span[dir="auto"]').first
                    if span_nome.count() > 0:
                        nome_contato = span_nome.get_attribute("title") or span_nome.inner_text().strip() or nome_contato
                else:
                    chat_row = badge

                chats_nao_lidos.append({
                    "nome": nome_contato,
                    "elemento": chat_row if chat_row.count() > 0 else badge,
                    "badge": badge
                })
            except Exception:
                continue

    except Exception as erro:
        # Falha transitória ao buscar elementos
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
            print(f"🔄 Trocando para o chat: 💬 [{nome}]...")
            elemento.click()
            time.sleep(1.0)
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
    Identifica novas mensagens recebidas e gera a resposta (logando no console no modo Dry-Run).
    """
    nome_chat = obter_nome_chat_ativo(page)
    mensagens = page.locator('[data-pre-plain-text]')
    quantidade = mensagens.count()
    agora = time.time()
    novas_mensagens_processadas = 0

    for i in range(quantidade):
        mensagem = mensagens.nth(i)
        mensagem_id = obter_mensagem_id(mensagem)

        if not mensagem_id or mensagem_id in mensagens_processadas:
            continue

        # Cooldown caso tenha falhado anteriormente
        if mensagem_id in mensagens_em_espera:
            if agora < mensagens_em_espera[mensagem_id]:
                continue
            del mensagens_em_espera[mensagem_id]

        texto = obter_texto_mensagem(mensagem)
        if not texto:
            continue

        recebida = mensagem_eh_recebida(mensagem)
        if recebida is None:
            # Mensagem de sistema ou não identificável
            mensagens_processadas.add(mensagem_id)
            continue
        if not recebida:
            # Mensagem enviada pelo próprio bot/usuário
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

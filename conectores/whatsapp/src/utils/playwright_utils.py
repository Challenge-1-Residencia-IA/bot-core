# src/utils/playwright_utils.py
"""Playwright helper utilities for WhatsApp Web automation.

All functions are deliberately small and async‑compatible with the bot code.
They are imported as:
    from conectores.whatsapp.src.utils.playwright_utils import (
        abrir_navegador, fechar_navegador,
        abrir_chat_por_nome, fechar_chat_ativo, obter_nome_chat_ativo,
        enviar_mensagem, chat_eh_grupo, obter_mensagens_recebidas,
        obter_contexto_conversa,
    )

Selectors follow the WhatsApp Web DOM as of October 2026: the chat list is
``#pane-side``, the open chat is ``#main`` (name in
``[data-testid="conversation-info-header-chat-title-name"]``) and each message
is a ``[data-id]`` row whose direction is inferred by ``_JS_LINHAS``. WhatsApp
Web changes this markup without notice; when the scanner stops seeing
messages, these selectors are the first thing to check.
"""

import asyncio
import time
from pathlib import Path
from typing import List, Dict, Optional, Union

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

USER_AGENT_CHROME = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/156.0.0.0 Safari/537.36")

# ---------------------------------------------------------------------------
# Browser handling
# ---------------------------------------------------------------------------
async def abrir_navegador(playwright, headless: Optional[bool] = None,
                          perfil_dir: Optional[Path] = None) -> BrowserContext:
    """Launch Chromium with a persistent profile for WhatsApp Web.

    The profile directory keeps the WhatsApp session, so the QR code is only
    needed on the first run; after that ``headless`` mode works too. Only one
    process may use a profile at a time (Chromium's ProcessSingleton lock).

    ``headless`` and ``perfil_dir`` default to ``config.yaml`` (``headless``,
    ``perfil_navegador``), overridable by ``WHATSAPP_HEADLESS`` and
    ``WHATSAPP_PERFIL_DIR``.
    """
    from ..config import HEADLESS, PERFIL_DIR
    if headless is None:
        headless = HEADLESS
    perfil = Path(perfil_dir or PERFIL_DIR)
    perfil.mkdir(parents=True, exist_ok=True)
    opcoes = {}
    if headless:
        # WhatsApp Web does not load the chat list for a "HeadlessChrome" user agent.
        opcoes["user_agent"] = USER_AGENT_CHROME
        opcoes["viewport"] = {"width": 1400, "height": 900}
    context = await playwright.chromium.launch_persistent_context(str(perfil), headless=headless, **opcoes)
    return context


async def fechar_navegador(browser: Union[Browser, BrowserContext]) -> None:
    """Close the Playwright browser/context."""
    await browser.close()

# Shared JS: classifies the message rows of ``#main``. In the current DOM each
# message is a ``[data-id]`` row (``data-testid="conv-msg-<id>"``) with no
# in/out class. A row is incoming when its ``data-pre-plain-text`` sender, its
# first aria label ("<name>:") or its tail icon (``tail-in``) points to the open
# chat; outgoing rows carry a delivery status label ("Enviada", "Entregue",
# "Lida"), "Você:" or ``tail-out``. Text inside quoted messages is ignored.
_JS_LINHAS = """
const STATUS = /^(enviada|entregue|lida|reproduzida|pendente|sent|delivered|read|played|pending)$/i;
const nomeChat = () => {
  const t = document.querySelector('#main [data-testid="conversation-info-header-chat-title-name"]')
    || document.querySelector('#main header span[title]');
  return t ? (t.getAttribute('title') || t.innerText || '').trim() : '';
};
const linhas = () => {
  const main = document.querySelector('#main');
  if (!main) return [];
  const nome = nomeChat();
  return [...main.querySelectorAll('[data-id]')].filter(row => !row.parentElement.closest('[data-id]')).map(row => {
    const pre = row.querySelector('[data-pre-plain-text]')?.getAttribute('data-pre-plain-text') || '';
    const remetente = (pre.match(/\\]\\s*(.*?):\\s*$/) || [, ''])[1].trim();
    const rotulos = [...row.querySelectorAll('[aria-label]')].map(e => (e.getAttribute('aria-label') || '').trim());
    const icone = n => !!row.querySelector(`[data-icon="${n}"]`);
    const saida = rotulos.some(r => STATUS.test(r) || r === 'Você:' || r === 'You:') || icone('tail-out');
    const entrada = !saida && (remetente === nome || rotulos.includes(nome + ':') || icone('tail-in'));
    const texto = [...row.querySelectorAll('.selectable-text')]
      .filter(el => !el.closest('[data-testid="quoted-message"]'))
      .map(el => el.innerText).join('\\n').trim();
    const midia = !!row.querySelector('audio, video, img[src^="blob:"], [data-icon="ptt-status"], [data-icon="ptt-play"], '
      + '[data-icon="audio-play"], [data-icon="media-play"], [data-icon="document"], [data-testid="ptt-status"]');
    const status = rotulos.filter(r => STATUS.test(r)).map(r => r.toLowerCase());
    return {id: row.getAttribute('data-id'), entrada, saida, texto, midia, status};
  });
};
"""

# ---------------------------------------------------------------------------
# Chat navigation helpers
# ---------------------------------------------------------------------------
_JS_NOME_CHAT_ATIVO = """() => {
  const main = document.querySelector('#main');
  if (!main) return null;
  const titulo = main.querySelector('[data-testid="conversation-info-header-chat-title-name"]')
    || main.querySelector('header span[title]');
  if (!titulo) return null;
  return (titulo.getAttribute('title') || titulo.innerText || '').trim() || null;
}"""


async def obter_nome_chat_ativo(page: Page) -> Optional[str]:
    """Return the name of the chat open in ``#main``, or ``None`` if none is open."""
    return await page.evaluate(_JS_NOME_CHAT_ATIVO)


async def abrir_chat_por_nome(page: Page, chat_nome: str) -> bool:
    """Open the chat whose title is exactly ``chat_nome``.

    The chat must be visible in the side list (``#pane-side``): the search box
    is not used, so a partial match can never open the wrong chat. Returns
    ``True`` only when ``#main`` shows that chat.
    """
    if await obter_nome_chat_ativo(page) == chat_nome:
        return True
    candidatos = page.locator("#pane-side").get_by_title(chat_nome, exact=True)
    if await candidatos.count() != 1:
        return False
    await candidatos.click(timeout=3000)
    for _ in range(20):
        if await obter_nome_chat_ativo(page) == chat_nome:
            # The header may switch before the message list finishes loading.
            await asyncio.sleep(0.5)
            return await obter_nome_chat_ativo(page) == chat_nome
        await asyncio.sleep(0.1)
    return False


async def fechar_chat_ativo(page: Page) -> None:
    """Close the open chat (Escape), returning to the chat list."""
    if await obter_nome_chat_ativo(page) is not None:
        await page.keyboard.press("Escape")


async def chat_eh_grupo(page: Page) -> bool:
    """Detect whether the open chat is a group or community."""
    seletor = ('#main header [data-icon="default-group"], #main header [data-icon="community"], '
               '#main header [title*="participantes"], #main header [title*="participants"]')
    return await page.locator(seletor).count() > 0

# ---------------------------------------------------------------------------
# Message handling helpers
# ---------------------------------------------------------------------------
_JS_ENVIO_CONFIRMADO = "({antes, texto}) => {" + _JS_LINHAS + """
  const norm = t => t.replace(/\\s+/g, ' ').trim();
  return linhas().some(l => l.saida && !antes.includes(l.id) && norm(l.texto) === norm(texto)
    && l.status.some(st => !['pendente', 'pending'].includes(st)));
}"""


async def enviar_mensagem(page: Page, texto: str, chat_nome: Optional[str] = None,
                          timeout_confirmacao: float = 15) -> None:
    """Send ``texto`` in the open chat and wait until it shows the sent check.

    Raises ``RuntimeError`` when the chat changes, the editor has a draft, or
    the message is not confirmed in the interface within the timeout. The
    caller must not resend after an error: the message may have gone out.
    """
    if chat_nome is not None and await obter_nome_chat_ativo(page) != chat_nome:
        raise RuntimeError("chat_diferente")
    main = page.locator("#main")
    editor = main.locator('footer div[contenteditable="true"][role="textbox"]')
    if await editor.count() != 1 or (await editor.inner_text()).strip():
        raise RuntimeError("editor_indisponivel_ou_com_rascunho")
    antes = await main.locator("[data-id]").evaluate_all("els => els.map(el => el.getAttribute('data-id'))")
    await editor.fill(texto)
    if chat_nome is not None and await obter_nome_chat_ativo(page) != chat_nome:
        raise RuntimeError("chat_mudou")
    await editor.press("Enter")
    limite = time.monotonic() + timeout_confirmacao
    while time.monotonic() < limite:
        if await page.evaluate(_JS_ENVIO_CONFIRMADO, {"antes": antes, "texto": texto}):
            return
        await asyncio.sleep(0.4)
    raise RuntimeError("envio_nao_confirmado")

_JS_MENSAGENS_RECEBIDAS = "() => {" + _JS_LINHAS + """
  return linhas().filter(l => l.entrada).map(l => ({id: l.id, texto: l.texto, midia: l.midia}));
}"""


async def obter_mensagens_recebidas(page: Page) -> List[Dict[str, object]]:
    """Return the incoming messages visible in the open chat, oldest first.

    Each item is ``{"id": <data-id>, "texto": <str>, "midia": <bool>}``.
    Media without caption (audio, image, sticker, document) has ``texto == ""``.
    Rows are classified by ``_JS_LINHAS``.
    """
    return await page.evaluate(_JS_MENSAGENS_RECEBIDAS) or []

# ---------------------------------------------------------------------------
# Conversation context helper
# ---------------------------------------------------------------------------
_JS_CONTEXTO = "(limite) => {" + _JS_LINHAS + """
  return linhas().filter(l => l.texto && (l.entrada || l.saida))
    .map(l => ({role: l.entrada ? 'user' : 'assistant', content: l.texto})).slice(-limite);
}"""


async def obter_contexto_conversa(page: Page, limite: int = 15) -> List[Dict[str, str]]:
    """Return the last ``limite`` text messages of the open chat, oldest first,
    as ``{"role": "user" | "assistant", "content": <text>}``."""
    return await page.evaluate(_JS_CONTEXTO, limite) or []

# src/utils/playwright_utils.py
"""Playwright helper utilities for WhatsApp Web automation.

All functions are deliberately small and async‑compatible with the bot code.
They are imported as:
    from src.utils.playwright_utils import (
        abrir_navegador, fechar_navegador,
        abrir_chat_por_nome, fechar_chat_ativo, obter_nome_chat_ativo,
        enviar_mensagem, encontrar_chats_nao_lidos, chat_eh_grupo,
        obter_mensagem_id, obter_texto_mensagem, mensagem_eh_recebida,
        obter_contexto_conversa,
    )
"""

import asyncio
import re
from pathlib import Path
from typing import List, Dict, Optional

from playwright.async_api import Browser, Page, async_playwright

# ---------------------------------------------------------------------------
# Browser handling
# ---------------------------------------------------------------------------
async def abrir_navegador(playwright) -> Browser:
    """Launch a fresh Chromium browser (non‑persistent) for WhatsApp Web.

    Using a persistent profile can cause a lock error when a previous instance hasn't been cleaned up. This non‑persistent launch avoids the ProcessSingleton conflict. If you need to keep login state, use a unique profile directory.
    """
    browser = await playwright.chromium.launch(headless=False)
    return browser


async def fechar_navegador(browser: Browser) -> None:
    """Close the Playwright browser/context."""
    await browser.close()

# ---------------------------------------------------------------------------
# Chat navigation helpers
# ---------------------------------------------------------------------------
async def abrir_chat_por_nome(page: Page, chat_nome: str) -> bool:
    """Search for a chat by its visible name and click it.

    Returns ``True`` if the chat was opened successfully.
    """
    await page.wait_for_selector("#pane-side", timeout=30000)
    await page.fill("input[title='Search input']", chat_nome)
    await page.keyboard.press("Enter")
    try:
        await page.wait_for_selector(f"text={chat_nome}", timeout=5000)
        return True
    except Exception:
        return False


async def fechar_chat_ativo(page: Page) -> None:
    """Return to the main chat list (WhatsApp’s "home" screen)."""
    try:
        await page.click("button[data-testid='chat-back']")
    except Exception:
        await page.click("button[aria-label='Back']")
    await page.wait_for_selector("#pane-side", timeout=10000)


async def obter_nome_chat_ativo(page: Page) -> Optional[str]:
    """Return the currently opened chat’s name, or ``None`` if none is open.
    Tries multiple selectors to be robust against UI changes.
    """
    selectors = [
        "header span[data-testid='chat-name']",
        "header span[title]",
        "div[data-testid='conversation-info-header'] span[title]",
    ]
    for sel in selectors:
        try:
            name = await page.inner_text(sel)
            if name:
                return name
        except Exception:
            continue
    return None

# ---------------------------------------------------------------------------
# Message handling helpers
# ---------------------------------------------------------------------------
async def enviar_mensagem(page: Page, texto: str) -> None:
    """Type and send a message to the currently opened chat."""
    await page.fill("div[data-testid='conversation-compose-box-input']", texto)
    await page.keyboard.press("Enter")
    await asyncio.sleep(0.5)

# ---------------------------------------------------------------------------
# Misc utilities used by scanner/dispatcher
# ---------------------------------------------------------------------------
async def encontrar_chats_nao_lidos(page: Page) -> List[Dict[str, object]]:
    """Return a list of chats that have an unread badge.

    Each entry is a dict ``{"nome": <str>, "elemento": <PlaywrightElement>}`` where
    ``elemento`` can be clicked to open the chat.
    """
    unread_spans = await page.query_selector_all(
        "div[data-testid='cell-frame-container'] span[data-testid='conversation-unread-count']"
    )
    resultados: List[Dict[str, object]] = []
    for span in unread_spans:
        container = await span.evaluate_handle(
            "node => node.closest('div[data-testid=\\\"cell-frame-container\\\']')"
        )
        title_elem = await container.query_selector("span[title]")
        nome = await title_elem.get_attribute("title") if title_elem else None
        if nome:
            resultados.append({"nome": nome, "elemento": container})
    return resultados


async def chat_eh_grupo(page: Page) -> bool:
    """Detect whether the currently opened chat is a group chat."""
    try:
        await page.wait_for_selector("button[data-testid='group-info']", timeout=2000)
        return True
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Message element helpers (used by scanner)
# ---------------------------------------------------------------------------
async def obter_mensagem_id(elem) -> Optional[str]:
    """Return the unique message id from a message element, if present."""
    return await elem.get_attribute("data-id")


async def obter_texto_mensagem(elem) -> str:
    """Return the visible text of a message element."""
    return await elem.inner_text()


async def mensagem_eh_recebida(elem) -> bool:
    """Return ``True`` if the message was received (incoming), ``False`` otherwise."""
    direction = await elem.get_attribute("data-direction")
    return direction == "incoming"

# ---------------------------------------------------------------------------
# Conversation context helper
# ---------------------------------------------------------------------------
async def obter_contexto_conversa(page: Page, limite: int = 15) -> List[Dict[str, str]]:
    """Collect the most recent *limite* messages from the currently opened chat.

    Returns a list of dicts compatible with the Groq prompt format, e.g.
    ``{"role": "user", "content": "message text"}``. The messages are ordered
    chronologically (oldest first).
    """
    elements = await page.query_selector_all("[data-pre-plain-text]")
    if not elements:
        return []
    selected = elements[-limite:]
    contexto: List[Dict[str, str]] = []
    for elem in selected:
        role = "user" if await mensagem_eh_recebida(elem) else "assistant"
        raw = (await elem.inner_text()).strip()
        cleaned = re.sub(r"^\d{1,2}:\d{2}\s+", "", raw)
        contexto.append({"role": role, "content": cleaned})
    return contexto


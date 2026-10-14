import asyncio
from playwright.async_api import async_playwright
from src.utils.playwright_utils import abrir_navegador, obter_nome_chat_ativo, fechar_chat_ativo

async def main():
    async with async_playwright() as p:
        browser = await abrir_navegador(p)
        page = await browser.new_page()
        await page.goto('https://web.whatsapp.com', wait_until='load')
        print('Log in to WhatsApp Web (scan QR code) and press Enter in this console...')
        input()
        # Wait for UI
        await page.wait_for_selector('#pane-side', timeout=300000)
        # Click the first chat in the sidebar
        first_chat = page.locator('div[data-testid="cell-frame-container"]').first
        await first_chat.click()
        await asyncio.sleep(1)
        chat_name = await obter_nome_chat_ativo(page)
        print(f'Opened chat: {chat_name}')
        # Click the last message in the opened chat (if any)
        msgs = page.locator('[data-pre-plain-text]')
        count = await msgs.count()
        if count:
            last_msg = msgs.last
            await last_msg.click()
            print('Clicked the last message.')
        else:
            print('No messages found in the chat.')
        await fechar_chat_ativo(page)
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())

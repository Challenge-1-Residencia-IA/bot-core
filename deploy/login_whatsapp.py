"""Login isolado, sem scanner nem envio; captura somente o QR."""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright
from conectores.whatsapp.src.utils.playwright_utils import abrir_navegador


async def main():
    os.umask(0o077)
    qr = Path(os.environ['WHATSAPP_QR_PATH'])
    async with async_playwright() as pw:
        browser = await abrir_navegador(pw, headless=True)
        try:
            page = browser.pages[0] if browser.pages else await browser.new_page()
            await page.goto('https://web.whatsapp.com', wait_until='domcontentloaded')
            for _ in range(180):
                if await page.locator('#pane-side').count():
                    print('LOGIN_CONFIRMADO', flush=True)
                    return
                canvas = page.locator('canvas').first
                if await canvas.count() and await canvas.is_visible():
                    await canvas.screenshot(path=str(qr))
                    print('QR_ATUALIZADO', flush=True)
                await asyncio.sleep(5)
            raise TimeoutError('Login não concluído em 15 minutos')
        finally:
            qr.unlink(missing_ok=True)
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())

import asyncio
import json
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.async_api import async_playwright

PERFIL_WHATSAPP = "whatsapp_profile"

async def inspecionar_chat():
    print("=" * 70)
    print("🔍 INSPETOR DE DOM DO WHATSAPP (DIAGNÓSTICO DE GRUPO VS CONTATO)")
    print("=" * 70)
    print("Abra o navegador, faça login se necessário, e CLIQUE em uma conversa.")
    print("=" * 70)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            PERFIL_WHATSAPP,
            headless=False,
            viewport={"width": 1280, "height": 850}
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://web.whatsapp.com")

        print("\n⏳ Aguardando carregamento do WhatsApp...")
        await page.wait_for_selector("#pane-side", timeout=60000)
        print("✅ Conectado!\n")

        while True:
            await asyncio.to_thread(input, "👉 Clique no chat que você quer analisar e aperte [ENTER] no terminal...")

            diagnostico = await page.evaluate("""
            () => {
                const header = document.querySelector('header');
                const headerHTML = header ? header.outerHTML : null;
                const headerText = header ? header.innerText : null;

                // Coleta elementos do cabeçalho
                let headerSpans = [];
                if (header) {
                    header.querySelectorAll('span, div[role="button"]').forEach(el => {
                        headerSpans.push({
                            tag: el.tagName,
                            title: el.getAttribute('title'),
                            ariaLabel: el.getAttribute('aria-label'),
                            text: el.innerText.trim(),
                            className: el.className
                        });
                    });
                }

                // Coleta dados das 3 últimas mensagens
                const msgs = document.querySelectorAll('[data-pre-plain-text]');
                let amostraMensagens = [];
                const ultimasMsgs = Array.from(msgs).slice(-3);
                ultimasMsgs.forEach(m => {
                    amostraMensagens.push({
                        prePlainText: m.getAttribute('data-pre-plain-text'),
                        innerText: m.innerText.trim(),
                        innerHTML: m.innerHTML.substring(0, 300)
                    });
                });

                return {
                    headerText: headerText,
                    headerSpans: headerSpans,
                    amostraMensagens: amostraMensagens
                };
            }
            """)

            print("\n" + "═" * 70)
            print("📊 DIAGNÓSTICO EXTRAÍDO:")
            print("═" * 70)
            print(json.dumps(diagnostico, indent=2, ensure_ascii=False))
            print("═" * 70)
            print("Copie e cole esse resultado aqui para analisarmos!\n")

if __name__ == "__main__":
    asyncio.run(inspecionar_chat())

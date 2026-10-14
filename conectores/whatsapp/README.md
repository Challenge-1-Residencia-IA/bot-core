# Conector de WhatsApp (Playwright)

Conector do bot-core para o WhatsApp Web. Ele lê as mensagens com o **Playwright**, envia cada uma ao **núcleo** pela fila **ARQ** (Redis) e responde no chat com o texto que o núcleo devolve. O conector não chama LLM: a análise (guardrails, busca, LangGraph) fica toda no núcleo (`app/`).

Origem: repositório `bot-test` (branch `teste1`), importado com `git subtree`.

## Fluxo

```
WhatsApp Web ──► scanner ──enqueue_job("analisar_mensagem", fila "bot-core:analises")──► Redis
                    │                                                                    │
                    │ await job.result(timeout)  (uma asyncio.Task por mensagem)          ▼
                    │◄──────────────────────── ResultadoAnalise ◄──────── worker do núcleo (app.worker)
                    ▼
        fila em memória (asyncio.Queue, mesmo processo)
                    ▼
               dispatcher ──► envia no chat (ou só registra no log, em dry-run)
```

- O contrato (nome do job, fila, `PedidoAnalise`, `ResultadoAnalise`, timeout padrão) está em `app/contrato.py`, importado pelos dois lados.
- O `_job_id` do ARQ é `whatsapp:<id da mensagem>`: a mesma mensagem não é enfileirada duas vezes (chave de idempotência).
- Se o núcleo não responde dentro de `timeout_job_s`, ou o job falha, o dispatcher recebe a mensagem curta `mensagem_indisponivel`.
- `chat_id` enviado ao núcleo é o **nome do chat** exibido no WhatsApp Web (o DOM não expõe um id estável da conversa).

## Como o WhatsApp Web é lido

- O scanner **consulta em ciclo os contatos permitidos**: abre cada conversa pelo **nome exato** na lista lateral (`#pane-side`) e lê as mensagens novas. Não usa os selos de "não lida" nem a caixa de busca, cujo formato muda entre versões e idiomas. A conversa precisa estar visível na lista lateral; se não estiver, o log avisa.
- O nome do chat aberto vem de `[data-testid="conversation-info-header-chat-title-name"]`. Cada mensagem é uma linha `[data-id]`; a direção é inferida pelo remetente em `data-pre-plain-text`, pelos rótulos ("Nome:", "Você:"), pelo status de entrega ("Enviada", "Entregue", "Lida") e pelos ícones `tail-in`/`tail-out`. Texto de mensagens citadas é ignorado.
- O envio só conta como feito quando a mensagem aparece no chat com status de entrega; caso contrário, vira `uncertain` e não é reenviada.
- Em modo headless, o navegador se identifica como um Chrome comum: com o user agent "HeadlessChrome", o WhatsApp Web não carrega a lista de conversas.
- Os seletores ficam em `src/utils/playwright_utils.py` e refletem o WhatsApp Web de outubro de 2026. Quando o scanner deixar de ver mensagens, comece por eles.

## Proteções

Adaptadas da integração RAG–WhatsApp do `experimentos` (`bot-test/botfinal.py` e `delivery_store.py`):

- **Allowlist fail-closed:** só os chats em `contatos_permitidos`, com o nome exato, são abertos e analisados. Se a lista estiver vazia, nenhum contato é atendido, e um aviso aparece no log ao iniciar. Fora do dry-run, o dispatcher confere a lista de novo antes de enviar.
- **Histórico inicial (baseline):** ao iniciar, o conector abre cada contato permitido e marca as mensagens já visíveis como `baseline`, sem analisá-las. O contato que não abre nesse momento recebe o baseline na primeira abertura.
- **Registro de entregas** (`src/delivery_store.py`, SQLite em `data/deliveries.db`):
  - A chave é `sha256(chat + "\0" + mensagem_id)`. O registro não guarda texto nem nome.
  - O scanner só enfileira se o `claim` der certo. O dispatcher só envia depois de `reserve_send` (`ready` → `sending`).
  - Se o envio falha, a mensagem vira `uncertain` e não é reenviada automaticamente.
  - Chat que não abre, contato fora da lista e resposta acima de `limite_resposta_chars` viram `blocked`.
  - Ao reiniciar, os estados intermediários (`queued`, `analyzing`, `ready`, `sending`) viram `interrupted` e não são reenviados.
  - Um lock de arquivo (`deliveries.db.lock`) impede duas instâncias no mesmo registro.
- **Mídia sem texto** (áudio, imagem, figurinha, documento): o próprio conector responde com `mensagem_midia`, pedindo o texto ou o link, sem chamar o núcleo. Uma imagem com legenda vai para o núcleo com a legenda.
- **Perfil persistente do navegador** (`launch_persistent_context` em `whatsapp_profile/`): o QR code só é pedido no primeiro login. Depois disso, o `headless` funciona. O perfil guarda a sessão do WhatsApp e não pode ir para o git.

Estados do registro: `baseline`, `queued`, `analyzing`, `ready`, `sending`, `sent`, `dry_run`, `blocked`, `uncertain`, `interrupted`.

## Configuração

`config.yaml` (algumas chaves aceitam variável de ambiente, útil na VM/Docker):

| Chave | Variável | Padrão | Efeito |
|---|---|---|---|
| `mode_dry_run` | `WHATSAPP_DRY_RUN` | `true` | Só registra a resposta no log, não envia |
| `headless` | `WHATSAPP_HEADLESS` | `false` | Chromium sem janela (depois do primeiro login) |
| `contatos_permitidos` | `WHATSAPP_CONTATOS_PERMITIDOS` (lista JSON) | `[]` | Chats atendidos; vazio = nenhum |
| `perfil_navegador` | `WHATSAPP_PERFIL_DIR` | `whatsapp_profile` | Perfil persistente do Chromium |
| `registro_entregas` | `WHATSAPP_REGISTRO_ENTREGAS` | `data/deliveries.db` | Registro de entregas |
| `limite_resposta_chars` | — | `12000` | Respostas maiores são descartadas (`blocked`) |
| `timeout_job_s` | — | `300` | Espera máxima pelo resultado do núcleo |
| `mensagem_indisponivel` | — | texto curto | Resposta em caso de timeout/erro |
| `mensagem_midia` | — | texto curto | Resposta a mensagem sem texto |
| `redis.host/port/db` | `REDIS_HOST/PORT/DB` | `127.0.0.1:6379/0` | Redis compartilhado com o núcleo |

Caminhos relativos são resolvidos a partir de `conectores/whatsapp/`. As variáveis de ambiente precisam estar exportadas no shell, porque o conector não lê o `.env` sozinho (veja `.env.example`).

## Como rodar (a partir da raiz do `bot-core`)

Todos os comandos rodam na raiz do `bot-core`, porque o conector importa `app.contrato` e é executado como módulo (`python -m conectores.whatsapp.src.main`, sem mexer em `sys.path`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r conectores/whatsapp/requirements.txt
playwright install chromium

# 1. Redis
docker compose -f conectores/whatsapp/docker-compose.yml up -d redis

# 2. Núcleo (worker do ARQ), em outro terminal
arq app.worker.WorkerSettings

# 3. Conector, em outro terminal (no primeiro uso, escaneie o QR code na janela do Chromium)
export WHATSAPP_CONTATOS_PERMITIDOS='["Nome exato do contato"]'
python -m conectores.whatsapp.src.main
```

No console do conector: `p` + Enter pausa/retoma; `d` + Enter liga/desliga o dry-run.

Para enviar respostas de verdade: `WHATSAPP_DRY_RUN=false python -m conectores.whatsapp.src.main` (use o número dedicado do bot).

## Testes

```bash
python -m pytest conectores/whatsapp/tests
```

Os testes usam fakes (pool ARQ, job e página) e um registro de entregas em diretório temporário. Eles não abrem navegador nem precisam de Redis.

## Estrutura

```
conectores/whatsapp/
├─ config.yaml          # configuração (dry-run, headless, timeout, Redis)
├─ docker-compose.yml   # Redis local
├─ requirements.txt     # dependências só do conector
├─ src/
│  ├─ main.py           # entrypoint: navegador, pool ARQ, scanner, dispatcher
│  ├─ scanner.py        # acha mensagens, enfileira no núcleo, espera o resultado
│  ├─ dispatcher.py     # envia as respostas (ou registra, em dry-run)
│  ├─ control.py        # pausa e dry-run pelo console
│  ├─ delivery_store.py # registro de entregas (hashes e estados, sem texto)
│  ├─ config.py, models.py, diagnostics.py
│  └─ utils/playwright_utils.py
├─ tests/               # testes com pytest
└─ botfinal.py, database.py, inspecionar.py, run_bot.ps1, test_click.py,
   test_database.py, ver_banco.py   # legado do bot-test (versão monolítica com Groq)
```

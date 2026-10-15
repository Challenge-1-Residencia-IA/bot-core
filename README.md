# bot-core

Aplicação principal do Challenge 1 (Residência IA): um bot de WhatsApp que ajuda a avaliar a confiabilidade de informações recebidas. Ele aponta técnicas de manipulação da mensagem, mostra o que checagens publicadas e outras fontes dizem (com citações literais) e sugere como conferir por conta própria. **Nunca dá veredito de verdadeiro ou falso**: a conclusão é do usuário. A documentação do produto está no repositório [`Documentacao`](https://github.com/Challenge-1-Residencia-IA/Documentacao).

## Arquitetura

O repositório é um monorepo com duas partes que conversam por uma fila (padrão produtor/consumidor com [ARQ](https://arq-docs.helpmanual.io/) sobre Redis):

```
WhatsApp Web ◄──► conector (Playwright) ──job "analisar_mensagem"──► Redis ──► núcleo (worker ARQ)
               conectores/whatsapp/      ◄──────── resultado ────────────         app/
                                                                                    │
                                                         LangGraph ◄────────────────┘
                                                           guardrail de entrada  (padrões + NeMo Guardrails)
                                                           extração              (LLM: afirmações, natureza, técnicas)
                                                           busca                 (checagens publicadas, depois a web)
                                                           síntese               (LLM: técnicas, citações, leitura lateral)
                                                           guardrail de saída    (sem veredito, avisos de tema de risco)
```

- **Conector** (`conectores/whatsapp/`): lê o WhatsApp Web com Playwright, só nos contatos permitidos, envia cada mensagem ao núcleo e entrega a resposta. Não chama o LLM. Detalhes em [`conectores/whatsapp/README.md`](conectores/whatsapp/README.md).
- **Núcleo** (`app/`): worker ARQ que executa o grafo LangGraph. O contrato entre as partes (nome do job, fila, formato do pedido e do resultado) fica em `app/contrato.py`, importado pelos dois lados.
- **LLM**: Qwen 2.5 7B servido pelo Ollama, acessado por HTTP (`OLLAMA_URL`). O núcleo não precisa rodar na mesma máquina do modelo; em produção, a máquina com o Ollama é acessada pela rede privada (Tailscale).
- **Docker**: Redis (fila) e PostgreSQL + pgvector local (base de checagens; em produção, Neon). O Ollama roda nativo, com acesso direto à GPU.

### O que a resposta traz

1. **O que observar na mensagem**: técnicas de manipulação (urgência, pedido de compartilhamento, autoridade vaga, apelo emocional), com o trecho da mensagem.
2. **O que as fontes dizem**: trechos copiados literalmente das páginas, numerados pela fonte. A conclusão de uma agência de checagem pode aparecer, sempre atribuída a ela.
3. **Como conferir por conta própria**: passos de leitura lateral.
4. **Fontes consultadas**: título, origem, data e link.
5. Avisos quando o tema é de risco (saúde, eleições, golpes, segurança) ou quando dados pessoais foram ocultados.

### Guardrails

| Etapa | O que faz |
|---|---|
| Entrada | Remove caracteres invisíveis; mascara CPF, CNPJ, e-mail e telefone antes da busca e do LLM; recusa tentativas de instrução ao assistente (OWASP LLM01), pedidos fora do escopo, saudações e mensagens em inglês ou espanhol; encurta mensagens longas com aviso. Mensagens curtas ambíguas passam por uma classificação do LLM via NeMo Guardrails (`GUARDRAILS_NEMO=0` desliga). |
| Fontes | Trechos externos são blindados antes de entrar no prompt (marcações de papel e frases com instruções são removidas). |
| Saída | Frases com veredito ou julgamento do usuário escritas pelo modelo são removidas; citações das fontes são preservadas. Avisos de tema de risco são acrescentados. |

## Estrutura

```
app/
├── config.py         # variáveis de ambiente (Ollama, Redis, banco)
├── contrato.py       # contrato da fila entre conector e núcleo
├── llm.py            # cliente do Ollama (saída em JSON)
├── worker.py         # worker ARQ: job analisar_mensagem
├── graph/            # LangGraph: estado, nós e grafo (grafo.py)
├── guardrails/       # entrada, privacidade, blindagem, saída e configuração do NeMo
├── rag/              # busca de evidências, seleção de trechos e síntese da tutora
├── prompts/          # prompts versionados
├── main.py           # FastAPI (endpoints de teste; não usado pelo fluxo do WhatsApp)
├── db/              # acesso ao PostgreSQL + pgvector (base de checagens)
├── ingestao/        # scripts que preenchem a base de checagens
├── search/          # esqueleto: provedores de busca
└── telegram/         # esqueleto antigo, do canal descartado

conectores/whatsapp/  # conector de WhatsApp Web (Playwright)
migrations/           # migrações do banco (Alembic)

tests/
├── guardrails/, rag/, graph/   # testes unitários (sem rede e sem LLM)
├── db/                         # migrações num Postgres de teste (TEST_DATABASE_URL)
├── dataset/, eval/             # esqueletos de dataset e avaliação
```

## Ambiente local

Pré-requisitos: Python 3.12 (NeMo Guardrails não suporta 3.14), Docker e [Ollama](https://ollama.com) com o modelo baixado (`ollama pull qwen2.5:7b`).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r conectores/whatsapp/requirements.txt
playwright install chromium
cp .env.example .env   # ajuste OLLAMA_MODEL ao nome que aparece em `ollama list`
```

### Rodar o bot (três terminais, na raiz do repositório, com o venv ativo)

```bash
# 1. Redis (fila)
docker compose -f conectores/whatsapp/docker-compose.yml up -d redis

# 2. Núcleo
arq app.worker.WorkerSettings

# 3. Conector: nomes exatos dos contatos, como aparecem na lista de conversas do WhatsApp do bot
export WHATSAPP_CONTATOS_PERMITIDOS='["Contato de teste"]'
python -m conectores.whatsapp.src.main
```

- No primeiro uso, escaneie o QR code na janela do Chromium com o celular do **número dedicado do bot**. A sessão fica salva em `conectores/whatsapp/whatsapp_profile/` (fora do git).
- O conector começa em **modo simulação (dry-run)**: as respostas aparecem só no terminal. Para enviar de verdade, inicie com `WHATSAPP_DRY_RUN=false` ou digite `d` + Enter no terminal do conector.
- Uma análise leva cerca de 20 a 30 s com o Qwen 7B numa GPU local. Recusas dos guardrails são imediatas.
- Se o Ollama não responder, o usuário recebe uma mensagem de indisponibilidade.

### Banco de dados

O esquema da base de checagens (extensão `vector`, tabelas `documentos`, `trechos` e `boatos_conhecidos`, índices HNSW e de texto em português) é criado por migrações do [Alembic](https://alembic.sqlalchemy.org), em `migrations/versions/`. Para aplicar no banco de `DATABASE_URL`:

```bash
docker compose up -d db   # local; em produção, a URL aponta para o Neon
alembic upgrade head
```

Mudanças no esquema entram sempre como uma nova migração, nunca alterando o banco à mão.

O serviço `db` usa o PostgreSQL 17, a mesma versão do Neon. Um volume criado por outra versão impede o container de subir (`database files are incompatible with server`); se o banco local não tiver nada a preservar, recrie-o com `docker compose down -v`.

### Base de checagens

A base é preenchida por scripts de ingestão em `app/ingestao/`, idempotentes: rodar de novo só processa páginas que ainda não estão no banco, e um documento só ganha novos embeddings quando o texto muda. Os embeddings usam o `intfloat/multilingual-e5-base` em CPU (cerca de 1,5 GB de RAM, 40 ms por consulta), baixado do Hugging Face na primeira execução.

**FakeTrue.Br** (`app/ingestao/faketrue.py`): baixa de novo as páginas das checagens, das notícias e do Boatos.org pelas URLs do corpus, porque o texto do corpus vem sem maiúsculas, sem título e sem data; o texto do corpus só é usado quando a página não pode ser lida. Respeita o `robots.txt` de cada site. O texto de cada boato vai para `boatos_conhecidos`, ligado à sua página no Boatos.org; os boatos do golden set ficam de fora, para não contaminar a avaliação.

```bash
python -m app.ingestao.faketrue \
  --corpus ../experimentos/data/raw/FakeTrue.Br/FakeTrueBr_corpus.csv \
  --golden-set ../experimentos/data/processed/golden_set_v1.jsonl
```

`--limite N` processa só os N primeiros pares (teste) e `--sem-download` usa apenas o texto do corpus.

Páginas que hoje levam a outro conteúdo (redirecionamento para a página inicial do site, galerias de fotos) são recusadas, e o documento fica com o texto do corpus. `--atualizar --url <URL>` refaz documentos específicos.

**Auditoria** (`app/ingestao/auditoria.py`): lista documentos cujo texto talvez não seja a matéria da URL (endereço sem relação com o título e o texto, anos citados muito depois da publicação, galerias), para revisão manual. `--corrigir-metadados` reaplica as regras atuais de agência, natureza e título sem recalcular embeddings, por exemplo depois de incluir uma agência em `AGENCIAS_CHECAGEM`.

```bash
python -m app.ingestao.auditoria
python -m app.ingestao.auditoria --corrigir-metadados
```

### Testar o núcleo sem WhatsApp

```python
from app.contrato import PedidoAnalise
from app.graph.grafo import analisar

r = analisar(PedidoAnalise(chat_id="teste", mensagem_id="1", texto="O governo vai taxar o Pix acima de 5 mil"))
print(r.resposta_texto)
```

### Variáveis de ambiente do núcleo

| Variável | Padrão | Uso |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Endereço do Ollama |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Modelo usado na extração, na síntese e no NeMo |
| `LLM_TIMEOUT_S` | `180` | Tempo máximo de uma chamada ao LLM |
| `REDIS_URL` | `redis://localhost:6379/0` | Fila ARQ |
| `GUARDRAILS_NEMO` | `1` | `0` desliga a etapa do NeMo (fica só a verificação por padrões) |
| `DATABASE_URL` | vazio | PostgreSQL + pgvector (Neon em produção) |

As variáveis do conector estão no README dele.

## Testes

```bash
GUARDRAILS_NEMO=0 python -m pytest tests conectores/whatsapp/tests
```

Os testes usam LLM, busca, fila e página falsos. Os testes de integração com o Qwen rodam só se o Ollama estiver no ar.

Os testes de migração rodam só com `TEST_DATABASE_URL`, porque apagam e recriam o esquema; use um banco descartável, nunca o de produção:

```bash
docker compose up -d db
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/challenge1 python -m pytest tests/db
```

## Estado e próximos passos

- Funciona localmente de ponta a ponta: WhatsApp → fila → grafo com Qwen e busca na web → resposta.
- Pendentes: aviso de "analisando" para o usuário enquanto a resposta é gerada; base de checagens no pgvector; medição com o golden set (repositório `experimentos`); Dockerfiles, CI e deploy na nuvem.
- O conector depende da estrutura da página do WhatsApp Web, que muda sem aviso. Quando ele deixar de ver mensagens, comece pelos seletores em `conectores/whatsapp/src/utils/playwright_utils.py`.

## Modelos e dados

Nenhum peso de modelo nem dataset é versionado aqui: arquivos binários grandes ficam para sempre no histórico do Git e o GitHub bloqueia arquivos acima de 100 MB. O projeto não faz fine-tuning; o "modelo" versionado é o conjunto de prompts (`app/prompts/`, `app/rag/prompts/`), configuração e guardrails deste repositório. Os datasets e a avaliação ficam no repositório `experimentos`, versionados com DVC.

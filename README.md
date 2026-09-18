# bot-core

Aplicação principal do Challenge 1 (Residência IA): bot de Telegram que avalia a confiabilidade de informações, integrando FastAPI, LangGraph, busca web e um LLM local. Documentação de produto está no repositório [`Documentacao`](https://github.com/Challenge-1-Residencia-IA/Documentacao).

## Arquitetura

```
Telegram → FastAPI (webhook) → LangGraph (orquestrador)
                                   ├─ Extração de afirmações            (LLM local)
                                   ├─ Classificação                     (LLM local)
                                   ├─ Busca (Tavily) + busca semântica  (pgvector)
                                   ├─ Comparação de evidências          (LLM local)
                                   ├─ Síntese + crítica                 (LLM local)
                                   → Resposta estruturada → Telegram
```

- **LLM**: modelo local mais leve (Qwen 2.5 7B via Ollama/MLX), em vez do modelo grande via API listado na documentação original do produto — decisão validada na prática pela Sprint 1.
- **Docker**: só para PostgreSQL + pgvector (ver `docker-compose.yml`). O LLM local (MLX) e o `ngrok` rodam nativos, fora de container — MLX depende de acesso direto ao Metal (GPU Apple Silicon), que não é exposto dentro de containers Docker no macOS.
- **Resultado ao usuário**: nunca um veredito fixo "verdadeiro/falso" — sempre uma combinação de evidências favoráveis/contrárias, contexto e nível de confiança (🟢 forte / 🟡 conflitante / 🟠 insuficiente / 🔴 contrária forte / ⚪ não verificável).

Decisões mais específicas de cada componente estão nos READMEs de `app/graph/`, `app/search/`, `app/db/`, `tests/dataset/` e `tests/eval/`.

## Estrutura

```
app/
├── main.py         # FastAPI + webhook do Telegram
├── graph/          # LangGraph: state, nodes e orquestração do pipeline
├── prompts/        # Prompts versionados de cada etapa
├── search/         # Integração com Tavily/SearXNG e extração de conteúdo
├── db/             # Models, migrations e queries (PostgreSQL + pgvector)
└── telegram/        # Cliente e handlers do bot

tests/
├── dataset/         # Dataset de teste (mensagens com gabarito)
└── eval/            # Scripts de avaliação e métricas
```

## Ambiente local

Pré-requisitos: Python 3.11+, Docker, Ollama (ou outro runtime local para o LLM).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencher as chaves necessárias

docker compose up -d   # sobe PostgreSQL + pgvector

uvicorn app.main:app --reload
```

## Modelos e datasets

Pesos de modelo (base ou fine-tunado) e datasets grandes **não são versionados neste repositório** (ver `.gitignore`): arquivos binários grandes ficam para sempre no histórico do Git mesmo se deletados depois, não têm diff eficiente, e o GitHub bloqueia arquivos acima de 100 MB.

### Hugging Face Hub

Modelos fine-tunados (adaptadores LoRA/QLoRA, tipicamente poucos MB a algumas centenas de MB — não os GBs de um fine-tuning completo) ficam hospedados no [Hugging Face Hub](https://huggingface.co). O modelo base (ex.: Qwen 2.5 7B) é baixado separadamente via Ollama, não pelo Hub.

**Convenção de nomeação**: `<usuário-ou-org>/qwen-fakenews-<versão>` (ex.: `challenge1-ia/qwen-fakenews-lora-v1`). Cada nova rodada de fine-tuning sobe uma revisão nova no mesmo repositório do Hub, em vez de criar um repositório novo por versão.

**Subir um modelo/adaptador treinado**:

```bash
pip install huggingface_hub
huggingface-cli login                 # cola o token de acesso (com permissão de escrita)
huggingface-cli upload <usuario>/qwen-fakenews-lora-v1 ./caminho/do/adaptador
```

**Baixar para usar localmente**:

```bash
huggingface-cli download <usuario>/qwen-fakenews-lora-v1 --local-dir models/qwen-fakenews
```

O caminho/ID baixado é referenciado pela variável `HF_MODEL_REPO` no `.env` (ver `.env.example`).

**Regra da equipe**: quem fizer uma nova rodada de fine-tuning sobe o resultado para o Hub e atualiza o `HF_MODEL_REPO` combinado com o time — evita que cada pessoa re-treine do zero ou passe arquivos manualmente.

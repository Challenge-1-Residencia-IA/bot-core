# bot-core

Aplicação principal do Challenge 1 (Residência IA): bot de Telegram que avalia a confiabilidade de informações, integrando FastAPI, LangGraph, busca web e um LLM local. Documentação do produto e das decisões de arquitetura está no repositório [`Documentacao`](https://github.com/Challenge-1-Residencia-IA/Documentacao).

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

O LLM local e o `ngrok` (para expor o webhook em desenvolvimento) rodam nativamente na máquina, fora do Docker.

## Modelos e datasets

Pesos de modelo (base ou fine-tunado) e datasets grandes não são versionados neste repositório — ficam hospedados no [Hugging Face Hub](https://huggingface.co) e são referenciados por `HF_MODEL_REPO` no `.env`. Veja `.gitignore` para os padrões excluídos.

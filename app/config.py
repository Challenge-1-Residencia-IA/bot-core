"""Configuração do núcleo, lida de variáveis de ambiente (arquivo `.env` em desenvolvimento)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# LLM servido pelo Ollama. Em produção, a URL aponta para o PC que roda o Qwen, pela rede do Tailscale.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "180"))

# Fila ARQ (Redis) entre o conector e o núcleo.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Banco Postgres + pgvector (Neon em produção). Vazio = recursos que dependem do banco ficam desligados.
DATABASE_URL = os.getenv("DATABASE_URL", "")

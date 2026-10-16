"""Configuração do núcleo, lida de variáveis de ambiente (arquivo `.env` em desenvolvimento)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# LLM servido pelo Ollama. Em produção, a URL aponta para o PC que roda o Qwen, pela rede do Tailscale.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")


def _servidores_ollama() -> list[tuple[str, str]]:
    """Servidores do Ollama em ordem de preferência, como (url, modelo).

    `OLLAMA_URLS` lista vários servidores separados por vírgula; cada um pode trazer o próprio nome de
    modelo depois de `#` (ex.: `http://100.80.160.24:11434#qwen2.5:latest,http://100.122.1.92:11434`).
    Sem `OLLAMA_URLS`, vale só `OLLAMA_URL` com `OLLAMA_MODEL`.
    """
    servidores = []
    for item in os.getenv("OLLAMA_URLS", "").split(","):
        if item.strip():
            url, _, modelo = item.strip().partition("#")
            servidores.append((url.rstrip("/"), modelo.strip() or OLLAMA_MODEL))
    return servidores or [(OLLAMA_URL, OLLAMA_MODEL)]


OLLAMA_SERVIDORES = _servidores_ollama()
# O primeiro servidor é o principal: os módulos que só falam com um servidor (NeMo Guardrails) usam ele.
OLLAMA_URL, OLLAMA_MODEL = OLLAMA_SERVIDORES[0]
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "180"))

# Fila ARQ (Redis) entre o conector e o núcleo.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Banco Postgres + pgvector (Neon em produção). Vazio = recursos que dependem do banco ficam desligados.
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Modelo de embedding da base de checagens (roda em CPU, na mesma máquina do núcleo). Trocar o modelo
# exige reindexar a base; a dimensão do vetor está fixada na migração do banco.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-base")

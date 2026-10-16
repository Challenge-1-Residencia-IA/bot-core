"""Cliente do LLM (Ollama).

Ponto único de acesso ao modelo (RNF-09): os nós do grafo chamam `gerar_json` e não sabem onde o
modelo roda. Em produção, `OLLAMA_URL` aponta para o PC com o Qwen, acessado pela rede do Tailscale.
"""

from __future__ import annotations

import json

import requests

from app import config


class LLMIndisponivel(RuntimeError):
    """O modelo não respondeu (PC desligado, Ollama fora do ar, tempo esgotado)."""


def gerar_json(sistema: str, usuario: str, esquema: dict, *, max_tokens: int = 700) -> dict:
    """Pede ao modelo uma resposta no formato JSON definido por `esquema` (temperatura 0)."""
    try:
        resposta = requests.post(
            f"{config.OLLAMA_URL}/api/chat",
            json={
                "model": config.OLLAMA_MODEL,
                "stream": False,
                "format": esquema,
                "keep_alive": "15m",
                "options": {"temperature": 0, "num_predict": max_tokens},
                "messages": [{"role": "system", "content": sistema},
                             {"role": "user", "content": usuario}],
            },
            timeout=config.LLM_TIMEOUT_S,
        )
        if resposta.status_code == 404:
            raise LLMIndisponivel(f"Modelo {config.OLLAMA_MODEL!r} não encontrado em {config.OLLAMA_URL} (ver `ollama list`)")
        resposta.raise_for_status()
    except requests.RequestException as erro:
        raise LLMIndisponivel(f"{type(erro).__name__}: {erro}") from erro
    try:
        return json.loads(resposta.json()["message"]["content"])
    except (KeyError, json.JSONDecodeError) as erro:
        raise ValueError("O modelo não devolveu um JSON válido") from erro


def disponivel(timeout: float = 15) -> bool:
    """Confere se o Ollama responde e se o modelo configurado está instalado.

    O limite de 15 s cobre o caso em que a VM fala com o PC por um relay do Tailscale (DERP), em que a
    listagem de modelos chega a levar alguns segundos.
    """
    try:
        tags = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=timeout).json().get("models", [])
    except (requests.RequestException, ValueError):
        return False
    return any(config.OLLAMA_MODEL in (m.get("name"), m.get("model")) for m in tags)

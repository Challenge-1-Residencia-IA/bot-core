"""Cliente do LLM (Ollama), com servidores reserva.

Ponto único de acesso ao modelo (RNF-09): os nós do grafo chamam `gerar_json` e não sabem onde o
modelo roda. Em produção, os servidores ficam em máquinas acessadas pela rede do Tailscale
(`config.OLLAMA_SERVIDORES`, em ordem de preferência).

Failover: cada chamada tenta os servidores na ordem. Um servidor que falha (sem conexão, tempo
esgotado, erro 5xx ou modelo ausente) fica de lado por `ESPERA_APOS_FALHA_S` segundos e a chamada segue
para o próximo; passado esse tempo, ele volta a ser tentado primeiro. Assim o bot usa a máquina reserva
enquanto a principal está fora e volta sozinho para ela. Se todos falharem, `LLMIndisponivel` sobe e
o grafo responde que o modelo está fora do ar.
"""

from __future__ import annotations

import json
import threading
import time

import requests

from app import config

ESPERA_APOS_FALHA_S = 60
TIMEOUT_CONEXAO_S = 5
"""Tempo para abrir a conexão: uma máquina desligada é detectada em segundos, não em LLM_TIMEOUT_S."""

_estado_lock = threading.Lock()
_fora_ate: dict[str, float] = {}
_ultimo: dict = {}


class LLMIndisponivel(RuntimeError):
    """Nenhum servidor do modelo respondeu (máquinas desligadas, Ollama fora do ar, tempo esgotado)."""


def _ordem(agora: float) -> list[tuple[str, str]]:
    """Servidores na ordem de preferência; os que falharam há pouco vão para o fim (não são descartados:
    se todos estiverem marcados, ainda vale tentar)."""
    disponiveis = [s for s in config.OLLAMA_SERVIDORES if _fora_ate.get(s[0], 0) <= agora]
    marcados = [s for s in config.OLLAMA_SERVIDORES if s not in disponiveis]
    return disponiveis + marcados


def _chamar(url: str, modelo: str, sistema: str, usuario: str, esquema: dict, max_tokens: int) -> dict:
    resposta = requests.post(
        f"{url}/api/chat",
        json={
            "model": modelo,
            "stream": False,
            "format": esquema,
            "keep_alive": "15m",
            "options": {"temperature": 0, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": sistema},
                         {"role": "user", "content": usuario}],
        },
        timeout=(TIMEOUT_CONEXAO_S, config.LLM_TIMEOUT_S),
    )
    if resposta.status_code == 404:
        raise LLMIndisponivel(f"modelo {modelo!r} não encontrado (ver `ollama list`)")
    resposta.raise_for_status()
    try:
        return json.loads(resposta.json()["message"]["content"])
    except (KeyError, json.JSONDecodeError) as erro:
        raise ValueError("O modelo não devolveu um JSON válido") from erro


def gerar_json(sistema: str, usuario: str, esquema: dict, *, max_tokens: int = 700) -> dict:
    """Pede ao modelo uma resposta no formato JSON definido por `esquema` (temperatura 0)."""
    falhas = []
    for url, modelo in _ordem(time.time()):
        inicio = time.time()
        try:
            resultado = _chamar(url, modelo, sistema, usuario, esquema, max_tokens)
        except (requests.RequestException, LLMIndisponivel) as erro:
            with _estado_lock:
                _fora_ate[url] = time.time() + ESPERA_APOS_FALHA_S
            falhas.append(f"{url}: {type(erro).__name__}: {str(erro)[:150]}")
            continue
        with _estado_lock:
            _fora_ate.pop(url, None)
            _ultimo.update({"url": url, "modelo": modelo, "quando": time.time(),
                            "duracao_s": round(time.time() - inicio, 2)})
        return resultado
    raise LLMIndisponivel("; ".join(falhas) or "nenhum servidor configurado")


def disponivel(timeout: float = 15) -> bool:
    """Confere se pelo menos um servidor responde e tem o modelo instalado."""
    return any(s["ok"] for s in situacao(timeout))


def situacao(timeout: float = 5) -> list[dict]:
    """Estado de cada servidor (para o preflight e o painel): responde, tem o modelo, tempo da consulta."""
    resultado = []
    for url, modelo in config.OLLAMA_SERVIDORES:
        inicio = time.time()
        try:
            tags = requests.get(f"{url}/api/tags", timeout=timeout).json().get("models", [])
            ok = any(modelo in (m.get("name"), m.get("model")) for m in tags)
            erro = "" if ok else f"modelo {modelo} não instalado"
        except (requests.RequestException, ValueError) as falha:
            ok, erro = False, type(falha).__name__
        resultado.append({"url": url, "modelo": modelo, "ok": ok, "erro": erro,
                          "tempo_s": round(time.time() - inicio, 2)})
    return resultado


def ultimo_uso() -> dict:
    """Servidor usado na última chamada bem-sucedida deste processo."""
    with _estado_lock:
        return dict(_ultimo)

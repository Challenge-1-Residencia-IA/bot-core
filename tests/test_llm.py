"""Failover do cliente do LLM entre servidores do Ollama (app/llm.py), com rede falsa."""

import json

import pytest
import requests

from app import config, llm

PC, MAC = "http://pc:11434", "http://mac:11434"


class Resposta:
    def __init__(self, conteudo, status=200):
        self.status_code = status
        self._conteudo = conteudo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return {"message": {"content": json.dumps(self._conteudo)}}


@pytest.fixture
def rede(monkeypatch):
    """Servidores ligados por padrão; `rede["fora"]` lista os que não respondem."""
    estado = {"fora": set(), "chamadas": [], "agora": 1000.0}
    monkeypatch.setattr(config, "OLLAMA_SERVIDORES", [(PC, "qwen2.5:latest"), (MAC, "qwen2.5:7b")])
    monkeypatch.setattr(llm, "_fora_ate", {})
    monkeypatch.setattr(llm, "_ultimo", {})
    monkeypatch.setattr(llm.time, "time", lambda: estado["agora"])

    def post(url, json=None, timeout=None):
        servidor = url.rsplit("/api/", 1)[0]
        estado["chamadas"].append((servidor, json["model"]))
        if servidor in estado["fora"]:
            raise requests.ConnectionError("sem rota")
        return Resposta({"ok": servidor})

    monkeypatch.setattr(llm.requests, "post", post)
    return estado


def gerar():
    return llm.gerar_json("sistema", "usuario", {"type": "object"})


def test_usa_o_principal_quando_ele_responde(rede):
    assert gerar() == {"ok": PC}
    assert rede["chamadas"] == [(PC, "qwen2.5:latest")]
    assert llm.ultimo_uso()["url"] == PC


def test_principal_fora_vai_para_a_reserva_com_o_modelo_dela(rede):
    rede["fora"].add(PC)
    assert gerar() == {"ok": MAC}
    assert rede["chamadas"] == [(PC, "qwen2.5:latest"), (MAC, "qwen2.5:7b")]


def test_principal_fica_de_lado_por_um_tempo_e_depois_volta(rede):
    rede["fora"].add(PC)
    gerar()
    rede["chamadas"].clear()
    rede["agora"] += 10  # ainda dentro da espera: vai direto para a reserva
    assert gerar() == {"ok": MAC} and rede["chamadas"] == [(MAC, "qwen2.5:7b")]
    rede["fora"].clear()
    rede["agora"] += llm.ESPERA_APOS_FALHA_S  # passou a espera: o principal volta a ser tentado primeiro
    rede["chamadas"].clear()
    assert gerar() == {"ok": PC} and rede["chamadas"] == [(PC, "qwen2.5:latest")]


def test_todos_fora_levanta_llm_indisponivel_com_os_motivos(rede):
    rede["fora"].update({PC, MAC})
    with pytest.raises(llm.LLMIndisponivel) as erro:
        gerar()
    assert PC in str(erro.value) and MAC in str(erro.value)


def test_modelo_ausente_conta_como_falha_do_servidor(rede, monkeypatch):
    original = llm.requests.post

    def post(url, json=None, timeout=None):
        if url.startswith(PC):
            return Resposta({}, status=404)
        return original(url, json=json, timeout=timeout)

    monkeypatch.setattr(llm.requests, "post", post)
    assert gerar() == {"ok": MAC}


def test_le_os_servidores_de_ollama_urls(monkeypatch):
    monkeypatch.setenv("OLLAMA_URLS", f"{PC}/#qwen2.5:latest, {MAC}")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen2.5:7b")
    assert config._servidores_ollama() == [(PC, "qwen2.5:latest"), (MAC, "qwen2.5:7b")]


def test_sem_ollama_urls_vale_ollama_url(monkeypatch):
    monkeypatch.delenv("OLLAMA_URLS", raising=False)
    monkeypatch.setattr(config, "OLLAMA_URL", PC)
    monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen2.5:7b")
    assert config._servidores_ollama() == [(PC, "qwen2.5:7b")]

"""Painel da VM (deploy/painel.py): montagem dos comandos e tela, com Redis falso."""

import json

import pytest

from app.contrato import FILA, PAINEL_ANALISES, PAINEL_ESTADO_CONECTOR, PAINEL_EVENTOS
from deploy import painel


@pytest.mark.parametrize("args,comando", [
    (["pausar"], {"acao": "pausar"}),
    (["simulacao", "off"], {"acao": "simulacao", "valor": False}),
    (["limite", "3"], {"acao": "limite", "valor": 3}),
    (["bloquear", "a1b2c3d4"], {"acao": "bloquear", "chat": "a1b2c3d4"}),
])
def test_montar_comando(args, comando):
    assert painel.montar_comando(args) == comando


@pytest.mark.parametrize("args", [["limite", "x"], ["simulacao", "talvez"], ["encerrar"], ["explodir"]])
def test_comando_invalido_mostra_a_ajuda(args):
    with pytest.raises(SystemExit):
        painel.montar_comando(args)


class RedisFalso:
    def __init__(self, valores, listas):
        self.valores, self.listas = valores, listas

    def get(self, chave):
        return self.valores.get(chave)

    def lrange(self, chave, inicio, fim):
        return self.listas.get(chave, [])[inicio:fim + 1]

    def zcard(self, chave):
        return 2 if chave == FILA else 0

    def scan_iter(self, padrao, count=None):
        return iter(["arq:in-progress:x"])


def test_tela_mostra_estado_sessoes_analises_e_comandos(monkeypatch):
    monkeypatch.setattr(painel, "servicos", lambda: {"bot-worker": "active", "bot-conector": "active"})
    conector = {"quando": 1e10, "modo_acesso": "aberto", "pausado": True, "simulacao": False, "limite": 10,
                "inatividade_s": 600, "pendentes_total": 1, "em_andamento": 1, "bloqueados": ["deadbeef"],
                "ativas": [{"id": "a1b2c3d4", "inativo_s": 12, "analisando": True, "pendentes": 0}],
                "espera": [{"id": "e5f6a7b8", "posicao": 1, "pendentes": 1}]}
    analise = {"quando": 1e9, "chat": "a1b2c3d4", "duracao_s": 21.5, "recusada": False, "motivo": None,
               "llm_indisponivel": False, "llm": "http://100.122.1.92:11434", "fontes": 4, "fontes_da_base": 3,
               "web": True}
    evento = {"quando": 1e9, "comando": {"acao": "pausar"}, "resultado": "ok: bot pausado"}
    r = RedisFalso({PAINEL_ESTADO_CONECTOR: json.dumps(conector)},
                   {PAINEL_ANALISES: [json.dumps(analise)], PAINEL_EVENTOS: [json.dumps(evento)]})
    ollama = [{"url": "http://pc:11434", "modelo": "qwen2.5:latest", "ok": False, "erro": "ConnectionError",
               "tempo_s": 5.0},
              {"url": "http://mac:11434", "modelo": "qwen2.5:7b", "ok": True, "erro": "", "tempo_s": 0.1}]
    tela = painel.tela(r, ollama, 1e9)
    for trecho in ("PAUSADO", "envio real", "principal", "reserva", "fila: 2 job(s) · em análise: 1",
                   "Sessões 1/10", "a1b2c3d4", "espera #1", "bloqueados: deadbeef",
                   "4 fonte(s), 3 da base + web", "100.122.1.92", "ok: bot pausado"):
        assert trecho in tela, trecho


def test_tela_avisa_quando_o_conector_nao_publica(monkeypatch):
    monkeypatch.setattr(painel, "servicos", lambda: {"bot-worker": "active", "bot-conector": "failed"})
    assert "CONECTOR SEM SINAL" in painel.tela(RedisFalso({}, {}), [], 0)

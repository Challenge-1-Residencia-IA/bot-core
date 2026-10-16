"""Decisão do nó de busca entre a base de checagens e a web."""

from datetime import date

import pytest

from app.graph.nodes import busca
from app.graph.nodes.guardrails import guardrail_saida
from app.rag.busca import Evidencia

ESTADO = {"mensagem": "URGENTE: vacina causa paralisia",
          "afirmacoes": [{"texto": "vacina causa paralisia", "natureza": "fato_verificavel", "tema": "saude",
                          "o_que_verificar": "vacina paralisia"}]}


def ev(url, tipo="checagem", fonte="base", titulo="vacina paralisia", data="2026-09-01"):
    return Evidencia(url=url, titulo=titulo, dominio="x.org", trecho=titulo, tipo=tipo, fonte_busca=fonte, data=data)


@pytest.fixture
def chamadas(monkeypatch):
    registro = {"base": [], "web": 0}
    monkeypatch.setattr(busca.app_config, "DATABASE_URL", "postgresql://teste")
    monkeypatch.setattr(busca, "hoje", lambda: date(2026, 10, 15))

    def web(afirmacao, **_):
        registro["web"] += 1
        return [ev("https://web/1", "web", "web"), ev("https://base/noticia", "web", "web"),
                ev("https://web/sem-relacao", "web", "web", titulo="futebol")]

    monkeypatch.setattr(busca, "buscar_na_web", web)
    return registro


def usar_base(monkeypatch, registro, resultado):
    def base(afirmacao, mensagem, excluir):
        registro["base"].append((afirmacao, mensagem, excluir))
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    monkeypatch.setattr(busca, "buscar_na_base", base)


def urls(saida):
    return [e["url"] for e in saida["evidencias"]]


def test_checagem_na_base_dispensa_a_web(monkeypatch, chamadas):
    usar_base(monkeypatch, chamadas, [ev("https://base/checagem")])
    assert urls(busca.buscar_evidencias(ESTADO)) == ["https://base/checagem"]
    assert chamadas["web"] == 0
    assert chamadas["base"] == [("vacina causa paralisia", "URGENTE: vacina causa paralisia", [])]


def test_sem_checagem_na_base_completa_com_a_web_sem_repetir(monkeypatch, chamadas):
    usar_base(monkeypatch, chamadas, [ev("https://base/noticia", "web")])
    assert urls(busca.buscar_evidencias(ESTADO)) == ["https://base/noticia", "https://web/1"]
    assert chamadas["web"] == 1


def test_banco_fora_do_ar_cai_para_a_web(monkeypatch, chamadas):
    usar_base(monkeypatch, chamadas, ConnectionError("Neon fora do ar"))
    assert urls(busca.buscar_evidencias(ESTADO)) == ["https://web/1", "https://base/noticia"]


def test_sem_database_url_nao_consulta_a_base(monkeypatch, chamadas):
    usar_base(monkeypatch, chamadas, [ev("https://base/checagem")])
    monkeypatch.setattr(busca.app_config, "DATABASE_URL", "")
    busca.buscar_evidencias(ESTADO)
    assert chamadas["base"] == [] and chamadas["web"] == 1


def test_excluir_urls_chega_a_base_e_a_web_pelo_config(monkeypatch, chamadas):
    usar_base(monkeypatch, chamadas, [])
    config = {"configurable": {"excluir_urls": ["https://web/1"]}}
    assert urls(busca.buscar_evidencias(ESTADO, config)) == ["https://base/noticia"]
    assert chamadas["base"][0][2] == ["https://web/1"]


# Checagem antiga (freshness) -------------------------------------------------------------------------

def test_checagem_recente_dispensa_a_web_e_informa_a_data(monkeypatch, chamadas):
    usar_base(monkeypatch, chamadas, [ev("https://base/checagem", data="2026-04-20")])
    saida = busca.buscar_evidencias(ESTADO)
    assert chamadas["web"] == 0
    assert saida["avisos"] == [busca.AVISO_CHECAGEM_RECENTE.format(data="abr/2026")]
    assert saida["busca"] == {"checagem_mais_recente": "2026-04-20", "web": False, "motivo_web": None}


def test_checagem_antiga_complementa_com_a_web_e_avisa_a_data(monkeypatch, chamadas):
    antigas = [ev(f"https://base/antiga{i}", data="2021-05-01") for i in range(5)]
    usar_base(monkeypatch, chamadas, antigas)
    saida = busca.buscar_evidencias(ESTADO)
    assert chamadas["web"] == 1 and saida["busca"]["motivo_web"] == "checagem_antiga"
    assert urls(saida) == ["https://base/antiga0", "https://base/antiga1", "https://base/antiga2",
                           "https://web/1", "https://base/noticia"]
    assert saida["avisos"] == [busca.AVISO_CHECAGEM_ANTIGA.format(data="mai/2021")]


def test_vale_a_checagem_mais_recente(monkeypatch, chamadas):
    usar_base(monkeypatch, chamadas, [ev("https://base/velha", data="2020-01-01"),
                                      ev("https://base/nova", data="2026-08-30")])
    assert busca.buscar_evidencias(ESTADO)["busca"]["motivo_web"] is None


def test_checagem_sem_data_complementa_com_a_web_sem_aviso(monkeypatch, chamadas):
    usar_base(monkeypatch, chamadas, [ev("https://base/checagem", data="")])
    saida = busca.buscar_evidencias(ESTADO)
    assert chamadas["web"] == 1 and saida["busca"]["motivo_web"] == "checagem_sem_data"
    assert saida["avisos"] == []


def test_meses_entre_conta_meses_completos():
    assert busca.meses_entre(date(2026, 4, 15), date(2026, 10, 15)) == 6
    assert busca.meses_entre(date(2026, 4, 16), date(2026, 10, 15)) == 5
    assert busca.meses_entre(date(2026, 4, 14), date(2026, 10, 15)) == 6
    assert busca.meses_entre(date(2026, 3, 31), date(2026, 10, 15)) == 6


def test_aviso_da_busca_aparece_no_fim_da_resposta():
    estado = {"resposta_texto": "Resposta.", "sintese": {"fontes": []}, "mensagem": "texto qualquer",
              "afirmacoes": [], "entrada": {"sinais": []}, "avisos": ["Aviso de data."]}
    assert guardrail_saida(estado)["resposta_texto"] == "Resposta.\n\n⚠️ Aviso de data."

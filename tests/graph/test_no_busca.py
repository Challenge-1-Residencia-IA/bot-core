"""Decisão do nó de busca entre a base de checagens e a web."""

import pytest

from app.graph.nodes import busca
from app.rag.busca import Evidencia

ESTADO = {"mensagem": "URGENTE: vacina causa paralisia",
          "afirmacoes": [{"texto": "vacina causa paralisia", "natureza": "fato_verificavel", "tema": "saude",
                          "o_que_verificar": "vacina paralisia"}]}


def ev(url, tipo="checagem", fonte="base", titulo="vacina paralisia"):
    return Evidencia(url=url, titulo=titulo, dominio="x.org", trecho=titulo, tipo=tipo, fonte_busca=fonte)


@pytest.fixture
def chamadas(monkeypatch):
    registro = {"base": [], "web": 0}
    monkeypatch.setattr(busca.app_config, "DATABASE_URL", "postgresql://teste")

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

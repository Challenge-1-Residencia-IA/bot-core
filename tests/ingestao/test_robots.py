from types import SimpleNamespace

import pytest

from app.ingestao import faketrue


@pytest.fixture(autouse=True)
def _limpar_cache():
    faketrue._robots.cache_clear()
    yield
    faketrue._robots.cache_clear()


def _responder(monkeypatch, status, texto=""):
    agentes = []

    def get(url, headers, timeout):
        agentes.append(headers["User-Agent"])
        return SimpleNamespace(status_code=status, ok=200 <= status < 300, text=texto)

    monkeypatch.setattr(faketrue.requests, "get", get)
    return agentes


def test_usa_o_user_agent_do_bot_e_respeita_as_regras(monkeypatch):
    agentes = _responder(monkeypatch, 200, "User-agent: *\nDisallow: /wp-admin\n")
    assert faketrue.permitido_por_robots("https://site.org/noticia.html")
    assert not faketrue.permitido_por_robots("https://site.org/wp-admin/x")
    assert agentes == [faketrue.USER_AGENT]


@pytest.mark.parametrize("status,permitido", [(403, False), (401, False), (404, True)])
def test_codigos_de_erro_seguem_a_convencao(monkeypatch, status, permitido):
    _responder(monkeypatch, status)
    assert faketrue.permitido_por_robots("https://site.org/x") is permitido

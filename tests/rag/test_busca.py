from app.rag.busca import (
    Pagina, ResultadoBusca, agencia_de, buscar_evidencias, checar_url_publica, grupo_origem,
    montar_consulta,
)

import pytest

from tests.rag.conftest import LeitorFalso, ProvedorFalso

AFIRMACAO = "A vacina da gripe causa paralisia"


def test_checagens_vem_primeiro_e_uma_por_origem(resultados, paginas):
    provedor = ProvedorFalso({"checagem": resultados})
    leitor = LeitorFalso(paginas)
    diag = {}
    evidencias = buscar_evidencias(AFIRMACAO, provedor=provedor, leitor=leitor, diagnostico=diag)
    assert [e.tipo for e in evidencias] == ["checagem", "checagem", "web"]
    assert evidencias[0].agencia == "Boatos.org"
    assert len({e.dominio for e in evidencias}) == len(evidencias)
    assert diag["checagens"] == 2 and diag["paginas_lidas"] == 2
    assert provedor.consultas == [f"{AFIRMACAO} checagem de fatos", AFIRMACAO]


def test_trecho_literal_da_pagina_lida(resultados, paginas):
    evidencias = buscar_evidencias(AFIRMACAO, provedor=ProvedorFalso({"checagem": resultados}),
                                   leitor=LeitorFalso(paginas))
    boatos = evidencias[0]
    assert boatos.lida and boatos.trecho in boatos.texto
    assert "paralisia" in boatos.trecho
    assert boatos.data == "2024-04-02"


def test_checagem_sem_coleta_entra_com_resumo_do_buscador(resultados, paginas):
    leitor = LeitorFalso(paginas)
    evidencias = buscar_evidencias(AFIRMACAO, provedor=ProvedorFalso({"checagem": resultados}), leitor=leitor)
    aos_fatos = next(e for e in evidencias if e.agencia == "Aos Fatos")
    assert not aos_fatos.lida and aos_fatos.trecho.startswith("É falso")
    assert all("aosfatos" not in url for url in leitor.lidas)


def test_checagem_nao_e_descartada_por_relevancia_baixa(paginas):
    # Título da checagem sem quase nada em comum com um boato longo: no experimento era descartada.
    boato = ("URGENTE repassem para todos os grupos a nova vacina distribuída nos postos deixa os "
             "idosos sem andar segundo um médico de Brasília que pediu para não ser identificado")
    resultados = [ResultadoBusca("Mensagem sobre imunizante é antiga", "https://www.e-farsas.com/x.html", "")]
    pagina = Pagina(url="https://www.e-farsas.com/x.html", titulo="Mensagem sobre imunizante é antiga",
                    texto="O texto circula desde 2019 e não cita estudo algum. " * 5)
    evidencias = buscar_evidencias(boato, provedor=ProvedorFalso({"": resultados}),
                                   leitor=LeitorFalso({pagina.url: pagina}))
    assert len(evidencias) == 1 and evidencias[0].agencia == "E-farsas"


def test_falhas_do_buscador_e_de_paginas_nao_interrompem(resultados):
    diag = {}
    assert buscar_evidencias(AFIRMACAO, provedor=ProvedorFalso(erro=RuntimeError("fora do ar")),
                             leitor=LeitorFalso({}), diagnostico=diag) == []
    assert len(diag["erros_busca"]) == 2
    # Páginas que falham: a web geral é ignorada; checagem com resumo continua.
    evidencias = buscar_evidencias(AFIRMACAO, provedor=ProvedorFalso({"checagem": resultados}),
                                   leitor=LeitorFalso({}), diagnostico=diag)
    assert {e.agencia for e in evidencias} == {"Boatos.org", "Aos Fatos"}
    assert diag["paginas_ignoradas"]


def test_max_resultados_e_excluir_url(resultados, paginas):
    evidencias = buscar_evidencias(AFIRMACAO, max_resultados=1, provedor=ProvedorFalso({"checagem": resultados}),
                                   leitor=LeitorFalso(paginas),
                                   excluir_url="https://www.boatos.org/saude/vacina-gripe-paralisia.html")
    assert len(evidencias) == 1 and evidencias[0].agencia == "Aos Fatos"


def test_afirmacao_vazia_nao_busca():
    provedor = ProvedorFalso()
    assert buscar_evidencias("  https://x.com  ", provedor=provedor, leitor=LeitorFalso({})) == []
    assert provedor.consultas == []


def test_utilitarios_de_url():
    assert agencia_de("https://g1.globo.com/fato-ou-fake/noticia/x.ghtml") == "Fato ou Fake (G1)"
    assert agencia_de("https://g1.globo.com/economia/x.ghtml") is None
    assert agencia_de("https://g1.globo.com/fato-ou-fake/") is None
    assert agencia_de("https://www.boatos.org/") is None
    assert agencia_de("https://www.estadao.com.br/estadao-verifica/x/") == "Estadão Verifica"
    assert grupo_origem("https://g1.globo.com/a") == "globo.com"
    assert grupo_origem("https://www.saude.gov.br/a") == "saude.gov.br"
    consulta = montar_consulta("veja " * 100 + "https://exemplo.com/a")
    assert len(consulta) <= 300 and "http" not in consulta and not consulta.endswith(" ")


@pytest.mark.parametrize("url", ["ftp://exemplo.com", "http://localhost/x", "http://127.0.0.1/",
                                 "http://exemplo.com:8080/", "http://user:senha@exemplo.com/"])
def test_bloqueio_de_urls_nao_publicas(url):
    with pytest.raises(ValueError):
        checar_url_publica(url)


class _RespostaFalsa:
    def __init__(self, corpo, tipo="text/html; charset=utf-8", status=200, location=None):
        self.corpo, self.status_code = corpo, status
        self.headers = {"Content-Type": tipo, **({"Location": location} if location else {})}
        self.is_redirect = location is not None
        self.encoding = "utf-8"

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.corpo

    def close(self):
        pass


@pytest.fixture
def rede_falsa(monkeypatch):
    import socket

    from app.rag import busca

    monkeypatch.setattr(busca.socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    respostas = {}
    monkeypatch.setattr(busca.requests, "get", lambda url, **k: respostas[url])
    return respostas


def test_ler_pagina_html_com_redirecionamento(rede_falsa):
    from app.rag.busca import ler_pagina

    corpo = ("<p>O Ministério da Saúde informou que não há registro de paralisia associada à vacina.</p>"
             "<p>Especialistas explicam que eventos adversos graves são raros e monitorados pelo SUS.</p>"
             "<p>A mensagem circula desde 2019 em grupos de aplicativos de conversa.</p>")
    html = f"<html><head><title>Checagem</title></head><body><nav>menu</nav><article>{corpo}</article></body></html>"
    rede_falsa["https://exemplo.com/a"] = _RespostaFalsa(b"", location="/b")
    rede_falsa["https://exemplo.com/b"] = _RespostaFalsa(html.encode())
    pagina = ler_pagina("https://exemplo.com/a")
    assert pagina.url == "https://exemplo.com/b"
    assert "não há registro de paralisia" in pagina.texto and "menu" not in pagina.texto


def test_ler_pagina_recusa_tipo_e_texto_curto(rede_falsa):
    from app.rag.busca import ler_pagina

    rede_falsa["https://exemplo.com/img"] = _RespostaFalsa(b"\x89PNG", tipo="image/png")
    rede_falsa["https://exemplo.com/curta"] = _RespostaFalsa(b"<html><body><p>oi</p></body></html>")
    with pytest.raises(ValueError, match="não suportado"):
        ler_pagina("https://exemplo.com/img")
    with pytest.raises(ValueError, match="texto suficiente"):
        ler_pagina("https://exemplo.com/curta")


def test_ler_pagina_nao_baixa_dominio_sem_coleta(rede_falsa):
    from app.rag.busca import ler_pagina

    with pytest.raises(ValueError, match="Coleta automatizada"):
        ler_pagina("https://www.aosfatos.org/noticias/x")


def test_extracao_bs4_quando_trafilatura_falha(monkeypatch):
    import trafilatura

    from app.rag.busca import _extrair_html

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: None)
    monkeypatch.setattr(trafilatura, "extract_metadata", lambda *a, **k: None)
    texto, titulo, _ = _extrair_html("<html><head><title>T</title><script>x()</script></head>"
                                     "<body><main><h1>Título</h1><p>Parágrafo um.</p></main></body></html>")
    assert titulo == "T" and texto == "Título\nParágrafo um."

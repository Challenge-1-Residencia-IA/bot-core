"""Coleta RSS num Postgres de teste (TEST_DATABASE_URL), com feed e páginas falsos."""

import os

import pytest

from app.ingestao import rss
from app.rag.busca import Pagina
from tests.db.test_base_checagens import ModeloFalso, conexao  # noqa: F401 - fixture

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")


def _feed(itens):
    corpo = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>Texto do feed sobre {t}.</description>"
        f"<pubDate>Mon, 05 Oct 2026 10:00:00 +0000</pubDate></item>" for t, u in itens)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{corpo}</channel></rss>'.encode()


ITENS = [
    ("Vídeo falso mostra Lula em Paris", "https://www.boatos.org/politica/lula-paris.html"),
    ("Resultado das eleições 2026 em Barreiras: senador",
     "https://www.boatos.org/eleicoes-2026/resultado-eleicoes-2026-senador-barreiras"),
]


@pytest.fixture()
def feed_falso(monkeypatch):
    baixadas = []
    monkeypatch.setattr(rss, "FEEDS", {"Boatos.org": "https://www.boatos.org/feed"})
    monkeypatch.setattr(rss, "baixar_feed", lambda url, pagina=1: _feed(ITENS if pagina == 1 else []))
    monkeypatch.setattr(rss, "permitido_por_robots", lambda url: True)

    def ler(url):
        baixadas.append(url)
        return Pagina(url=url, titulo="Título da página", texto="Texto completo da checagem. " * 5, data="2026-10-05")

    monkeypatch.setattr(rss, "ler_pagina", ler)
    return baixadas


def test_grava_so_checagens_e_nem_baixa_o_resto(conexao, feed_falso):  # noqa: F811
    r = rss.coletar(conexao, ModeloFalso(), pausa_s=0)
    assert r["novos"] == 1 and r["nao_checagem"] == 1
    assert feed_falso == [ITENS[0][1]]
    assert conexao.execute("SELECT url FROM documentos").fetchall() == [(ITENS[0][1],)]


def test_para_quando_o_banco_passa_do_limite(conexao, feed_falso):  # noqa: F811
    r = rss.coletar(conexao, ModeloFalso(), pausa_s=0, limite_mb=0)
    assert r["interrompida_por_tamanho"] == 1 and r["novos"] == 0
    assert feed_falso == []


def test_remove_nao_checagens_ja_gravadas(conexao, feed_falso):  # noqa: F811
    from app.db.base_checagens import Documento, gravar_documento

    for titulo, url in ITENS:
        gravar_documento(conexao, Documento(url=url, texto="Texto. " * 5, natureza="checagem",
                                            origem="rss_pagina", titulo=titulo), ModeloFalso())
    conexao.commit()
    assert rss.remover_nao_checagens(conexao) == 1
    assert conexao.execute("SELECT count(*) FROM documentos").fetchone()[0] == 1
    assert conexao.execute("SELECT count(*) FROM trechos t JOIN documentos d ON d.id = t.documento_id").fetchone()[0] >= 1

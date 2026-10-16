"""Ingestão do FakeTrue.Br num Postgres de teste, com páginas e embeddings falsos."""

import csv
import json
import os

import pytest

from app.ingestao import faketrue
from app.rag.busca import Pagina
from tests.db.test_base_checagens import ModeloFalso, conexao  # noqa: F401 - fixture

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")

BOATO_A = "https://www.boatos.org/saude/vacina-paralisia.html"
BOATO_GS = "https://www.boatos.org/brasil/lazaro-fugiu.html"
G1 = "https://g1.globo.com/fato-ou-fake/noticia/2021/05/01/vacina.ghtml"
UOL = "https://noticias.uol.com.br/saude/2021/05/02/vacina-gripe.htm"


@pytest.fixture()
def arquivos(tmp_path):
    corpus = tmp_path / "corpus.csv"
    with corpus.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title_fake", "fake", "link_f", "true", "link_t"])
        w.writeheader()
        w.writerow({"title_fake": "vacina causa paralisia", "fake": "urgente vacina causa paralisia compartilhe",
                    "link_f": BOATO_A, "true": "circula que a vacina causa paralisia. é fake.", "link_t": G1})
        w.writerow({"title_fake": "vacina causa paralisia", "fake": "outra versão: vacina paralisa",
                    "link_f": BOATO_A, "true": "o ministério diz que a vacina é segura.", "link_t": UOL})
        w.writerow({"title_fake": "lázaro fugiu", "fake": "Lázaro fugiu de caminhão", "link_f": BOATO_GS,
                    "true": "a polícia segue as buscas.", "link_t": UOL})
    golden = tmp_path / "golden.jsonl"
    golden.write_text(json.dumps({"texto": "lázaro fugiu de caminhão", "fonte_esperada": BOATO_GS}) + "\n",
                      encoding="utf-8")
    return corpus, golden


def _paginas_falsas(monkeypatch, falham=()):
    def ler(url):
        if url in falham:
            raise ValueError("403")
        return Pagina(url=url, titulo=f"Título de {url[-20:]}", texto=f"Texto original da página {url}. Com Maiúsculas.",
                      data="2021-05-03T10:00:00")
    monkeypatch.setattr(faketrue, "ler_pagina", ler)
    monkeypatch.setattr(faketrue, "permitido_por_robots", lambda url: True)


def _rodar(conexao, arquivos, **kwargs):
    pares = faketrue.ler_corpus(arquivos[0])
    return faketrue.ingerir(pares, faketrue.ler_golden_set(arquivos[1]), conexao=conexao, modelo=ModeloFalso(), pausa_s=0, **kwargs)


def test_ingere_paginas_com_reserva_e_exclui_o_golden_set(conexao, arquivos, monkeypatch):  # noqa: F811
    _paginas_falsas(monkeypatch, falham={UOL})
    r = _rodar(conexao, arquivos)
    assert r["novo"] == 4 and r["origem_faketrue"] == 3 and r["origem_faketrue_corpus"] == 1
    assert r["boato_novo"] == 2 and r["boato_do_golden_set"] == 1

    docs = dict(conexao.execute("SELECT url, row(natureza, agencia, origem, publicado_em::text)::text FROM documentos"))
    assert docs[G1] == "(checagem,\"Fato ou Fake (G1)\",faketrue,2021-05-03)"
    assert docs[BOATO_A].startswith("(checagem,Boatos.org,faketrue")
    assert docs[UOL] == "(noticia,,faketrue_corpus,)"
    textos_boatos = {t for (t,) in conexao.execute("SELECT texto FROM boatos_conhecidos")}
    assert not any("Lázaro" in t for t in textos_boatos)
    ligados = conexao.execute(
        "SELECT DISTINCT d.url FROM boatos_conhecidos b JOIN documentos d ON d.id = b.checagem_id").fetchall()
    assert ligados == [(BOATO_A,)]


def test_segunda_execucao_nao_baixa_nem_grava_de_novo(conexao, arquivos, monkeypatch):  # noqa: F811
    _paginas_falsas(monkeypatch)
    _rodar(conexao, arquivos)
    monkeypatch.setattr(faketrue, "ler_pagina", lambda url: pytest.fail("não deveria baixar"))
    r = _rodar(conexao, arquivos)
    assert r["ja_na_base"] == 4 and r["novo"] == 0 and r["boato_igual"] == 2


def test_titulo_do_endereco_e_data():
    assert faketrue.titulo_do_endereco(UOL) == "Vacina gripe"
    assert faketrue.data_de("2021-05-03T10:00:00") is not None and faketrue.data_de("") is None


def test_pagina_que_redireciona_para_a_inicial_usa_o_corpus(conexao, arquivos, monkeypatch):  # noqa: F811
    _paginas_falsas(monkeypatch)

    def ler(url):
        final = "https://g1.globo.com/" if url == G1 else url
        return Pagina(url=final, titulo="g1 - O portal de notícias", texto="Menu do portal. " * 20, data="2026-10-15")

    monkeypatch.setattr(faketrue, "ler_pagina", ler)
    _rodar(conexao, arquivos)
    origem, titulo = conexao.execute("SELECT origem, titulo FROM documentos WHERE url = %s", (G1,)).fetchone()
    assert origem == "faketrue_corpus" and titulo == "Vacina"


def test_somente_urls_limita_o_processamento(conexao, arquivos, monkeypatch):  # noqa: F811
    _paginas_falsas(monkeypatch)
    pares = faketrue.ler_corpus(arquivos[0])
    r = faketrue.ingerir(pares, faketrue.ler_golden_set(arquivos[1]), conexao=conexao, modelo=ModeloFalso(),
                         pausa_s=0, somente_urls={UOL})
    assert r["novo"] == 1
    assert conexao.execute("SELECT url FROM documentos").fetchall() == [(UOL,)]

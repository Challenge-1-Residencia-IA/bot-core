"""Auditoria e correção de metadados num Postgres de teste (TEST_DATABASE_URL)."""

import os

import pytest

from app.db.base_checagens import Documento, gravar_documento
from app.ingestao import auditoria
from tests.db.test_base_checagens import ModeloFalso, conexao  # noqa: F401 - fixture

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")
LUPA = "https://piaui.folha.uol.com.br/lupa/2021/02/12/verificamos-whatsapp-atualizacao/amp/"


def test_corrige_lupa_antiga_sem_mexer_no_texto(conexao):  # noqa: F811
    gravar_documento(conexao, Documento(url=LUPA, texto="O WhatsApp não vai cobrar pela atualização.",
                                        natureza="noticia", origem="faketrue_corpus", titulo="Amp"), ModeloFalso())
    conexao.commit()
    antes = conexao.execute("SELECT hash_conteudo FROM documentos").fetchone()
    assert auditoria.corrigir_metadados(conexao) == 1
    assert conexao.execute("SELECT natureza, agencia, titulo FROM documentos").fetchone() == (
        "checagem", "Agência Lupa", "Verificamos whatsapp atualizacao")
    assert conexao.execute("SELECT hash_conteudo FROM documentos").fetchone() == antes
    assert auditoria.corrigir_metadados(conexao) == 0


def test_lista_suspeitos(conexao):  # noqa: F811
    gravar_documento(conexao, Documento(
        url="https://www.torcedores.com/noticias/2017/06/messi-ri-de-fatura-de-r-137-mil-em-balada-com-suarez",
        texto="Rossi volta a enfrentar o Estudiantes. Vasco encaminha compras.", natureza="noticia",
        origem="faketrue", titulo="Torcedores | Notícias esportivas"), ModeloFalso())
    conexao.commit()
    assert [i for i, _, _ in auditoria.listar_suspeitos(conexao)] == [1]

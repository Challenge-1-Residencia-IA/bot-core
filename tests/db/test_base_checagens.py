"""Gravação idempotente na base de checagens, num Postgres de teste (TEST_DATABASE_URL)."""

import hashlib
import os
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.db.base_checagens import Documento, conectar, gravar_boato, gravar_documento

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")
RAIZ = Path(__file__).resolve().parents[2]


class ModeloFalso:
    nome = "falso"

    def __init__(self):
        self.chamadas = 0

    def _vetor(self, texto):
        semente = hashlib.sha256(texto.encode()).digest()
        return [b / 255 for b in (semente * 24)[:768]]

    def passagens(self, textos):
        self.chamadas += 1
        return [self._vetor(t) for t in textos]

    def consulta(self, texto):
        return self._vetor(texto)


@pytest.fixture()
def conexao():
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.attributes["database_url"] = URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with conectar(URL) as c:
        yield c
    command.downgrade(cfg, "base")


def _doc(texto="Circula uma mensagem falsa. A vacina não causa paralisia, diz o ministério."):
    return Documento(url="https://www.boatos.org/saude/vacina.html", texto=texto, natureza="checagem",
                     origem="teste", titulo="Vacina causa paralisia #boato", agencia="Boatos.org",
                     publicado_em=date(2021, 5, 1))


def test_gravar_de_novo_nao_duplica_nem_recalcula(conexao):
    modelo = ModeloFalso()
    assert gravar_documento(conexao, _doc(), modelo) == "novo"
    assert gravar_documento(conexao, _doc(), modelo) == "igual"
    assert modelo.chamadas == 1
    assert conexao.execute("SELECT count(*) FROM documentos").fetchone()[0] == 1
    dominio, = conexao.execute("SELECT dominio FROM documentos").fetchone()
    assert dominio == "boatos.org"


def test_texto_alterado_substitui_os_trechos(conexao):
    modelo = ModeloFalso()
    gravar_documento(conexao, _doc(" ".join(f"Frase número {i} do texto." for i in range(80))), modelo)
    antes = conexao.execute("SELECT count(*) FROM trechos").fetchone()[0]
    assert antes > 1
    assert gravar_documento(conexao, _doc("Texto novo e curto."), modelo) == "atualizado"
    assert conexao.execute("SELECT texto FROM trechos").fetchall() == [("Texto novo e curto.",)]


def test_boato_liga_a_checagem_e_nao_duplica(conexao):
    modelo = ModeloFalso()
    gravar_documento(conexao, _doc(), modelo)
    assert gravar_boato(conexao, _doc().url, "URGENTE  compartilhe: vacina causa paralisia", modelo) == "novo"
    assert gravar_boato(conexao, _doc().url, "URGENTE compartilhe: vacina causa paralisia", modelo) == "igual"
    assert conexao.execute("SELECT count(*), min(relacao) FROM boatos_conhecidos").fetchone() == (1, "automatica")
    with pytest.raises(ValueError):
        gravar_boato(conexao, "https://nao.existe/x", "texto", modelo)

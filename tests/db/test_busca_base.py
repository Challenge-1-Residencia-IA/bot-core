"""Busca híbrida na base de checagens, num Postgres de teste (TEST_DATABASE_URL)."""

import math
import os
import re
from datetime import date

import pytest

from app.db.base_checagens import Documento, gravar_boato, gravar_documento
from app.rag.base import BaseChecagens, buscar_na_base
from tests.db.test_base_checagens import conexao  # noqa: F401 - fixture

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")

TEMAS = ["vacina", "urna", "pix", "enchente", "chip"]


class ModeloTematico:
    """Vetor por tema: textos do mesmo tema têm similaridade ~1; de temas diferentes, ~0."""

    nome = "tematico"

    def _vetor(self, texto):
        palavras = re.findall(r"\w+", texto.lower())
        vetor = [0.0] * 768
        for i, tema in enumerate(TEMAS):
            vetor[i] = float(sum(p.startswith(tema) for p in palavras))
        vetor[767] = 0.1
        norma = math.sqrt(sum(v * v for v in vetor))
        return [v / norma for v in vetor]

    def passagens(self, textos):
        return [self._vetor(t) for t in textos]

    def consulta(self, texto):
        return self._vetor(texto)


CHECAGEM_VACINA = "https://www.boatos.org/saude/vacina-paralisia.html"
NOTICIA_VACINA = "https://g1.globo.com/saude/noticia/vacina.ghtml"
CHECAGEM_PIX = "https://www.boatos.org/economia/pix.html"
CHECAGEM_URNA = "https://www.e-farsas.com/urna.html"
TEXTO_VACINA = " ".join(f"A vacina número {i} foi aprovada e não causa paralisia." for i in range(60))


@pytest.fixture()
def base(conexao):  # noqa: F811 - fixture importada
    modelo = ModeloTematico()
    docs = [
        Documento(CHECAGEM_VACINA, TEXTO_VACINA, "checagem", "teste", "Vacina causa paralisia #boato", "Boatos.org",
                  date(2021, 5, 1)),
        Documento(NOTICIA_VACINA, "Ministério amplia a vacina contra a gripe.", "noticia", "teste",
                  "Vacinação ampliada"),
        Documento(CHECAGEM_PIX, "O pix não será taxado, diz a Receita.", "checagem", "teste",
                  "Taxação do pix #boato", "Boatos.org"),
        # O texto da checagem fala de enchente; o boato ligado a ela fala de urna.
        Documento(CHECAGEM_URNA, "A foto mostra uma enchente de 2019, não uma fila de votação.",
                  "checagem", "teste", "Foto antiga #farsa", "E-farsas"),
    ]
    for doc in docs:
        gravar_documento(conexao, doc, modelo)
    gravar_boato(conexao, CHECAGEM_URNA, "URGENTE: urna fraudada no Pará, compartilhe antes que apaguem", modelo)
    conexao.commit()
    return BaseChecagens(conexao, modelo)


def test_boato_parente_vem_antes_dos_trechos(base):
    acertos = base.buscar("A vacina causa paralisia", "A urna foi fraudada no Pará, compartilhe")
    assert acertos[0].url == CHECAGEM_URNA and acertos[0].via == "boato"
    assert {a.url for a in acertos[1:]} == {CHECAGEM_VACINA, NOTICIA_VACINA}
    assert all(a.via == "trecho" for a in acertos[1:])


def test_sem_mensagem_nao_busca_boato(base):
    assert [a.via for a in base.buscar("A vacina causa paralisia")] == ["trecho", "trecho"]


def test_limiar_descarta_documentos_sem_relacao(base):
    assert base.buscar("O chip implantado rastreia pessoas") == []


def test_documento_aparece_uma_vez_com_os_trechos_mais_parecidos(base):
    acertos = base.buscar("A vacina causa paralisia")
    assert [a.url for a in acertos].count(CHECAGEM_VACINA) == 1
    vacina = next(a for a in acertos if a.url == CHECAGEM_VACINA)
    assert len(vacina.trechos) > 1


def test_excluir_urls_esconde_documentos(base):
    acertos = base.buscar("A vacina causa paralisia", "urna fraudada", excluir_urls=[CHECAGEM_VACINA, CHECAGEM_URNA])
    assert [a.url for a in acertos] == [NOTICIA_VACINA]


def test_caracteres_especiais_nao_quebram_a_busca_textual(base):
    acertos = base.buscar("vacina & 'paralisia' !!! (urgente) | :* <-> \\", "urna's | !")
    assert CHECAGEM_VACINA in {a.url for a in acertos}


def test_buscar_na_base_devolve_evidencias_com_trechos_literais(base, conexao):  # noqa: F811
    evidencias = buscar_na_base("A vacina causa paralisia", conexao=conexao, modelo=base.modelo)
    checagem = next(e for e in evidencias if e.url == CHECAGEM_VACINA)
    assert checagem.fonte_busca == "base" and checagem.tipo == "checagem"
    assert checagem.agencia == "Boatos.org" and checagem.data == "2021-05-01"
    assert checagem.trecho in checagem.texto
    assert all(parte in TEXTO_VACINA for parte in checagem.texto.split("\n\n"))
    noticia = next(e for e in evidencias if e.url == NOTICIA_VACINA)
    assert noticia.tipo == "web" and noticia.agencia is None

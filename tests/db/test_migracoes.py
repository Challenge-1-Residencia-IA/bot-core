"""Migrações do banco num Postgres + pgvector de teste.

Rodam só com `TEST_DATABASE_URL` definida (ex.: o serviço `db` do docker-compose); o banco é
apagado e recriado pelas migrações, então nunca aponte para o banco de produção.
"""

import os
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")

RAIZ = Path(__file__).resolve().parents[2]


def _alembic() -> Config:
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.attributes["database_url"] = URL
    return cfg


def _vetor(valor: float, dim: int = 768) -> str:
    return "[" + ",".join([str(valor)] * dim) + "]"


@pytest.fixture()
def banco():
    command.downgrade(_alembic(), "base")
    command.upgrade(_alembic(), "head")
    with psycopg.connect(URL, autocommit=True) as conexao:
        yield conexao
    command.downgrade(_alembic(), "base")


def test_cria_extensao_tabelas_e_indices(banco):
    assert banco.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
    tabelas = {linha[0] for linha in banco.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    assert {"documentos", "trechos", "boatos_conhecidos", "alembic_version"} <= tabelas
    indices = {linha[0] for linha in banco.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
    assert {"trechos_embedding_hnsw", "trechos_tsv_gin", "boatos_embedding_hnsw", "boatos_tsv_gin"} <= indices


def _documento(banco, url="https://www.boatos.org/x.html"):
    return banco.execute(
        "INSERT INTO documentos (url, dominio, natureza, origem, hash_conteudo) "
        "VALUES (%s, 'boatos.org', 'checagem', 'teste', 'h') RETURNING id", (url,)).fetchone()[0]


def test_trecho_gera_tsvector_e_busca_por_similaridade(banco):
    doc = _documento(banco)
    banco.execute(
        "INSERT INTO trechos (documento_id, ordem, texto, embedding, modelo_embedding) VALUES (%s, 0, %s, %s, 'm')",
        (doc, "A vacina contra a gripe não causa paralisia", _vetor(0.1)))
    linha = banco.execute(
        "SELECT texto, embedding <=> %s::vector AS distancia FROM trechos "
        "WHERE tsv @@ plainto_tsquery('portuguese', 'vacinas paralisia') ORDER BY distancia LIMIT 1",
        (_vetor(0.1),)).fetchone()
    assert linha is not None and linha[1] == pytest.approx(0, abs=1e-6)


def test_rejeita_dimensao_errada_e_natureza_invalida(banco):
    doc = _documento(banco)
    with pytest.raises(psycopg.errors.DataException):
        banco.execute(
            "INSERT INTO trechos (documento_id, ordem, texto, embedding, modelo_embedding) VALUES (%s, 0, 't', %s, 'm')",
            (doc, _vetor(0.1, dim=384)))
    with pytest.raises(psycopg.errors.CheckViolation):
        banco.execute("INSERT INTO documentos (url, dominio, natureza, origem, hash_conteudo) "
                      "VALUES ('u', 'd', 'opiniao', 'teste', 'h')")


def test_url_unica_e_remocao_em_cascata(banco):
    doc = _documento(banco)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _documento(banco)
    banco.execute("INSERT INTO trechos (documento_id, ordem, texto, embedding, modelo_embedding) "
                  "VALUES (%s, 0, 't', %s, 'm')", (doc, _vetor(0.2)))
    banco.execute("INSERT INTO boatos_conhecidos (checagem_id, texto, hash_texto, embedding, modelo_embedding) "
                  "VALUES (%s, 'compartilhe urgente', 'hb', %s, 'm')", (doc, _vetor(0.3)))
    banco.execute("DELETE FROM documentos WHERE id = %s", (doc,))
    assert banco.execute("SELECT count(*) FROM trechos").fetchone()[0] == 0
    assert banco.execute("SELECT count(*) FROM boatos_conhecidos").fetchone()[0] == 0


def test_downgrade_remove_as_tabelas():
    command.upgrade(_alembic(), "head")
    command.downgrade(_alembic(), "base")
    with psycopg.connect(URL) as conexao:
        restantes = conexao.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' "
            "AND table_name IN ('documentos', 'trechos', 'boatos_conhecidos')").fetchone()[0]
    assert restantes == 0

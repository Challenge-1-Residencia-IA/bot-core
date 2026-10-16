"""Base de checagens: documentos, trechos com embeddings e boatos conhecidos.

- `documentos`: uma linha por página (checagem de agência ou notícia), identificada pela URL.
- `trechos`: pedaços do texto de cada documento, com embedding e `tsvector` em português, para a
  busca híbrida (similaridade vetorial + palavras-chave).
- `boatos_conhecidos`: texto de boatos já checados, ligado à checagem correspondente. Serve só para
  encontrar a checagem (claim matching); o texto do boato nunca é apresentado como evidência.

Os embeddings têm 768 dimensões (`intfloat/multilingual-e5-base`). Trocar para um modelo de outra
dimensão exige nova migração e reindexação; `modelo_embedding` registra o modelo de cada vetor.

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE documentos (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            url             text NOT NULL UNIQUE,
            titulo          text,
            agencia         text,
            dominio         text NOT NULL,
            natureza        text NOT NULL CHECK (natureza IN ('checagem', 'noticia')),
            publicado_em    date,
            origem          text NOT NULL,
            hash_conteudo   text NOT NULL,
            criado_em       timestamptz NOT NULL DEFAULT now(),
            atualizado_em   timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("COMMENT ON COLUMN documentos.agencia IS 'Nome da agência de checagem; nulo para notícias'")
    op.execute("COMMENT ON COLUMN documentos.origem IS 'De onde veio o documento (ex.: faketrue, rss)'")
    op.execute("COMMENT ON COLUMN documentos.hash_conteudo IS 'SHA-256 do texto; reindexa só o que mudou'")

    op.execute("""
        CREATE TABLE trechos (
            id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            documento_id      bigint NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
            ordem             integer NOT NULL,
            texto             text NOT NULL,
            embedding         vector(768) NOT NULL,
            modelo_embedding  text NOT NULL,
            tsv               tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', texto)) STORED,
            UNIQUE (documento_id, ordem)
        )
    """)

    op.execute("""
        CREATE TABLE boatos_conhecidos (
            id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            checagem_id       bigint NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
            texto             text NOT NULL,
            hash_texto        text NOT NULL UNIQUE,
            relacao           text NOT NULL DEFAULT 'automatica'
                              CHECK (relacao IN ('automatica', 'confirmada')),
            embedding         vector(768) NOT NULL,
            modelo_embedding  text NOT NULL,
            tsv               tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', texto)) STORED,
            criado_em         timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "COMMENT ON COLUMN boatos_conhecidos.relacao IS "
        "'automatica = par vindo do dataset ou do coletor; confirmada = conferido por uma pessoa'"
    )

    op.execute("CREATE INDEX trechos_embedding_hnsw ON trechos USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX trechos_tsv_gin ON trechos USING gin (tsv)")
    op.execute("CREATE INDEX boatos_embedding_hnsw ON boatos_conhecidos USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX boatos_tsv_gin ON boatos_conhecidos USING gin (tsv)")
    op.execute("CREATE INDEX boatos_checagem_id ON boatos_conhecidos (checagem_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS boatos_conhecidos")
    op.execute("DROP TABLE IF EXISTS trechos")
    op.execute("DROP TABLE IF EXISTS documentos")

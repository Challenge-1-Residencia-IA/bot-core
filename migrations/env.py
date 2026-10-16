"""Ambiente do Alembic: aplica as migrações no banco de `DATABASE_URL`.

As migrações são escritas em SQL explícito (`op.execute`), sem modelos ORM: o esquema de pgvector
(tipo `vector`, índices HNSW, colunas `tsvector` geradas) fica legível e revisável no próprio arquivo.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from app.db import url_sqlalchemy

config = context.config


def _url() -> str:
    # Testes passam a URL por `config.attributes`; fora deles vale DATABASE_URL.
    return url_sqlalchemy(config.attributes.get("database_url"))


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=None, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conexao:
        context.configure(connection=conexao, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

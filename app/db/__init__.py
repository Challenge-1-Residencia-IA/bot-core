"""Acesso ao PostgreSQL + pgvector (base de checagens)."""

from __future__ import annotations

from app import config


def url_sqlalchemy(url: str | None = None) -> str:
    """Converte a `DATABASE_URL` (formato libpq) para o dialeto psycopg 3 do SQLAlchemy."""
    url = url if url is not None else config.DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL não definida")
    for prefixo in ("postgresql://", "postgres://"):
        if url.startswith(prefixo):
            return "postgresql+psycopg://" + url[len(prefixo):]
    return url

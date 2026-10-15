"""Gravação na base de checagens (tabelas `documentos`, `trechos` e `boatos_conhecidos`).

A gravação é idempotente: um documento é identificado pela URL e só é reprocessado quando o hash do
texto muda, então rodar a mesma ingestão duas vezes não duplica nada nem gera embeddings de novo.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

import psycopg
from pgvector.psycopg import register_vector

from app import config
from app.rag.embeddings import ModeloEmbedding
from app.rag.fatiamento import fatiar


@dataclass
class Documento:
    url: str
    texto: str
    natureza: str
    """`checagem` (agência de checagem) ou `noticia`."""
    origem: str
    """De onde veio (ex.: `faketrue`, `rss`)."""
    titulo: str | None = None
    agencia: str | None = None
    publicado_em: date | None = None

    @property
    def dominio(self) -> str:
        return (urlparse(self.url).hostname or "").removeprefix("www.")


def hash_texto(texto: str) -> str:
    return hashlib.sha256(" ".join(texto.split()).encode("utf-8")).hexdigest()


def conectar(url: str | None = None) -> psycopg.Connection:
    url = url if url is not None else config.DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL não definida")
    conexao = psycopg.connect(url)
    register_vector(conexao)
    return conexao


def gravar_documento(conexao: psycopg.Connection, doc: Documento, modelo: ModeloEmbedding) -> str:
    """Insere ou atualiza o documento e seus trechos. Devolve `novo`, `atualizado` ou `igual`."""
    hash_atual = hash_texto(doc.texto)
    linha = conexao.execute("SELECT id, hash_conteudo FROM documentos WHERE url = %s", (doc.url,)).fetchone()
    if linha and linha[1] == hash_atual:
        return "igual"
    trechos = fatiar(doc.texto)
    if not trechos:
        raise ValueError(f"Documento sem texto: {doc.url}")
    vetores = modelo.passagens(trechos)
    with conexao.transaction():
        documento_id = conexao.execute(
            """
            INSERT INTO documentos (url, titulo, agencia, dominio, natureza, publicado_em, origem, hash_conteudo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET
                titulo = EXCLUDED.titulo, agencia = EXCLUDED.agencia, dominio = EXCLUDED.dominio,
                natureza = EXCLUDED.natureza, publicado_em = EXCLUDED.publicado_em, origem = EXCLUDED.origem,
                hash_conteudo = EXCLUDED.hash_conteudo, atualizado_em = now()
            RETURNING id
            """,
            (doc.url, doc.titulo, doc.agencia, doc.dominio, doc.natureza, doc.publicado_em, doc.origem, hash_atual),
        ).fetchone()[0]
        conexao.execute("DELETE FROM trechos WHERE documento_id = %s", (documento_id,))
        with conexao.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO trechos (documento_id, ordem, texto, embedding, modelo_embedding) "
                "VALUES (%s, %s, %s, %s, %s)",
                [(documento_id, ordem, texto, vetor, modelo.nome)
                 for ordem, (texto, vetor) in enumerate(zip(trechos, vetores))],
            )
    return "atualizado" if linha else "novo"


def gravar_boato(conexao: psycopg.Connection, url_checagem: str, texto: str, modelo: ModeloEmbedding,
                 *, relacao: str = "automatica") -> str:
    """Liga o texto de um boato à checagem já gravada. Devolve `novo` ou `igual`.

    O texto inteiro vira um único vetor: boatos de corrente são curtos, e o que interessa é reconhecer a
    mensagem como um todo. Textos acima de 512 tokens são truncados pelo modelo.
    """
    checagem = conexao.execute("SELECT id FROM documentos WHERE url = %s", (url_checagem,)).fetchone()
    if checagem is None:
        raise ValueError(f"Checagem não está na base: {url_checagem}")
    texto = " ".join(texto.split())
    if conexao.execute("SELECT 1 FROM boatos_conhecidos WHERE hash_texto = %s", (hash_texto(texto),)).fetchone():
        return "igual"
    vetor = modelo.passagens([texto])[0]
    with conexao.transaction():
        inserido = conexao.execute(
            """
            INSERT INTO boatos_conhecidos (checagem_id, texto, hash_texto, relacao, embedding, modelo_embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (hash_texto) DO NOTHING
            RETURNING id
            """,
            (checagem[0], texto, hash_texto(texto), relacao, vetor, modelo.nome),
        ).fetchone()
    return "novo" if inserido else "igual"

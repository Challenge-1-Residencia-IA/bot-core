"""Auditoria da base de checagens.

Uso (na raiz do repositório, com DATABASE_URL definida):
    python -m app.ingestao.auditoria                        # lista documentos suspeitos, sem alterar nada
    python -m app.ingestao.auditoria --corrigir-metadados   # reaplica as regras atuais de agência,
                                                            # natureza e título (sem recalcular embeddings)
"""

from __future__ import annotations

import argparse
import logging

from app.db.base_checagens import conectar
from app.ingestao.faketrue import ORIGEM_TEXTO_DO_CORPUS, natureza_de, titulo_do_endereco
from app.ingestao.validacao import sinais_suspeitos
from app.rag.busca import agencia_de

log = logging.getLogger("ingestao.auditoria")

CONSULTA_DOCUMENTOS = """
    SELECT d.id, d.url, coalesce(d.titulo, ''), d.publicado_em, d.origem, d.natureza, d.agencia,
           string_agg(t.texto, ' ' ORDER BY t.ordem)
    FROM documentos d JOIN trechos t ON t.documento_id = d.id
    GROUP BY d.id
"""


def listar_suspeitos(conexao) -> list[tuple[int, str, list[str]]]:
    suspeitos = []
    for i, url, titulo, publicado_em, origem, _natureza, _agencia, texto in conexao.execute(CONSULTA_DOCUMENTOS):
        if sinais := sinais_suspeitos(url, titulo, texto, publicado_em,
                                      texto_da_pagina=origem != ORIGEM_TEXTO_DO_CORPUS):
            suspeitos.append((i, url, sinais))
    return suspeitos


def metadados_esperados(url: str, titulo: str, origem: str, natureza: str, texto: str) -> tuple[str, str | None, str]:
    """Natureza, agência e título segundo as regras atuais. Itens de feed RSS são sempre checagens."""
    nova_natureza = natureza if origem.startswith("rss") else natureza_de(url, texto)
    novo_titulo = titulo_do_endereco(url) if origem == ORIGEM_TEXTO_DO_CORPUS else titulo
    return nova_natureza, agencia_de(url), novo_titulo


def corrigir_metadados(conexao) -> int:
    alterados = 0
    for i, url, titulo, _data, origem, natureza, agencia, texto in conexao.execute(CONSULTA_DOCUMENTOS).fetchall():
        esperado = metadados_esperados(url, titulo, origem, natureza, texto)
        if esperado != (natureza, agencia, titulo):
            conexao.execute("UPDATE documentos SET natureza = %s, agencia = %s, titulo = %s, atualizado_em = now() "
                            "WHERE id = %s", (*esperado, i))
            alterados += 1
    conexao.commit()
    return alterados


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Auditoria da base de checagens")
    parser.add_argument("--corrigir-metadados", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with conectar() as conexao:
        if args.corrigir_metadados:
            log.info("Documentos com metadados corrigidos: %d", corrigir_metadados(conexao))
            return
        suspeitos = listar_suspeitos(conexao)
        for i, url, sinais in suspeitos:
            print(f"{i}\t{url}\t{'; '.join(sinais)}")
        log.info("Documentos suspeitos: %d", len(suspeitos))


if __name__ == "__main__":
    main()

"""Busca híbrida na base de checagens (pgvector + busca textual do Postgres, fundidas por RRF).

Duas buscas, sempre na mesma ordem:

1. **Boato parente** (claim matching): a mensagem recebida é comparada com `boatos_conhecidos`. Um boato
   parecido leva direto à checagem dele, mesmo quando a corrente volta com outras palavras. O texto do
   boato nunca vira evidência; só a checagem ligada a ele.
2. **Trechos**: a afirmação é comparada com os trechos de checagens e notícias.

Cada busca combina a similaridade vetorial (E5, índice HNSW, cosseno) com a busca textual em português
(`tsvector`), pela Reciprocal Rank Fusion (Cormack et al., 2009): `soma de 1 / (k + posição)` nas duas
listas. Só entram resultados com similaridade vetorial acima do limiar, para a busca textual sozinha
não trazer páginas que só dividem palavras soltas com a mensagem.

Limiares calibrados em 2026-10-15 (base com 2.907 páginas do FakeTrue.Br e 1.698 boatos):
- Trechos (0,85), com as afirmações do golden set v1: a checagem esperada fica em 1º lugar em 85% dos
  itens e entre as 5 primeiras em 89%. Com a checagem esperada escondida (simulação do IA-07), o que
  passa do limiar é, na maioria, o mesmo boato checado por outra agência. Com 0,82, todos os itens
  traziam 5 fontes, quase todas sem relação; com 0,86, a checagem esperada caía para 72%.
- Boato parente (0,93), com as 205 checagens que têm duas ou mais variações do mesmo boato: variações
  do mesmo boato ficam acima de 0,93 em 97% dos casos (mediana 0,98); boatos diferentes sobre o mesmo
  tema, em 17% (mediana 0,90). Com 0,88, boatos de outros assuntos passavam em 77% e ficavam na frente
  da checagem certa.
"""

from __future__ import annotations

import os
import re
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

from app.rag.busca import Evidencia
from app.rag.embeddings import ModeloEmbedding
from app.rag.trechos import STOPWORDS, relevancia, sem_acento

K_RRF = 60
"""Constante da RRF; 60 é o valor do artigo original e o padrão de mercado."""
CANDIDATOS = 40
"""Resultados lidos de cada lista (vetorial e textual) antes da fusão."""
EF_SEARCH = 100
"""Tamanho da lista dinâmica do HNSW na consulta; maior que o padrão (40) por causa dos filtros."""
MAX_TERMOS = 30

LIMIAR_TRECHO = float(os.getenv("BASE_LIMIAR_TRECHO", "0.85"))
"""Similaridade mínima entre a afirmação e um trecho (calibrada; ver o início do módulo)."""
LIMIAR_BOATO = float(os.getenv("BASE_LIMIAR_BOATO", "0.93"))
"""Similaridade mínima entre a mensagem e um boato conhecido (calibrada; ver o início do módulo)."""


@dataclass
class Acerto:
    """Um documento encontrado na base, com o melhor trecho para a consulta."""
    documento_id: int
    url: str
    titulo: str | None
    agencia: str | None
    dominio: str
    natureza: str
    publicado_em: object
    similaridade: float
    pontuacao: float
    via: str
    """`boato` (checagem de um boato parecido) ou `trecho` (trecho parecido com a afirmação)."""
    trechos: list[tuple[int, str, float]] = field(default_factory=list)
    """(ordem, texto, similaridade) dos trechos encontrados, para a seleção de citações."""


def rrf(listas: Iterable[Sequence[Hashable]], k: int = K_RRF) -> dict[Hashable, float]:
    """Reciprocal Rank Fusion: cada item soma `1 / (k + posição)` em cada lista em que aparece."""
    pontos: dict[Hashable, float] = {}
    for lista in listas:
        for posicao, item in enumerate(lista, start=1):
            pontos[item] = pontos.get(item, 0.0) + 1.0 / (k + posicao)
    return pontos


def consulta_textual(texto: str) -> str | None:
    """Monta um `tsquery` com OU entre as palavras de conteúdo do texto.

    O `websearch_to_tsquery` exige todas as palavras, e mensagens longas quase nunca casam por inteiro;
    com OU, o `ts_rank_cd` premia os trechos que têm mais palavras em comum. As palavras mantêm o acento
    para o stemmer do português do Postgres reduzi-las da mesma forma que o texto indexado.
    """
    vistas: list[str] = []
    for palavra in re.findall(r"\w{4,}", (texto or "").lower()):
        if palavra.isdigit() or sem_acento(palavra) in STOPWORDS or palavra in vistas:
            continue
        vistas.append(palavra)
        if len(vistas) >= MAX_TERMOS:
            break
    return " | ".join(vistas) or None


_FILTRO = "NOT (d.url = ANY(%(excluir)s))"

_SQL_TRECHOS_VETOR = f"""
    SELECT t.id, t.documento_id, 1 - (t.embedding <=> %(vetor)s) AS similaridade
    FROM trechos t JOIN documentos d ON d.id = t.documento_id
    WHERE {_FILTRO}
    ORDER BY t.embedding <=> %(vetor)s
    LIMIT %(limite)s
"""

_SQL_TRECHOS_TEXTO = f"""
    SELECT t.id, t.documento_id, 1 - (t.embedding <=> %(vetor)s) AS similaridade
    FROM trechos t JOIN documentos d ON d.id = t.documento_id,
         to_tsquery('portuguese', %(consulta)s) q
    WHERE t.tsv @@ q AND {_FILTRO}
    ORDER BY ts_rank_cd(t.tsv, q) DESC
    LIMIT %(limite)s
"""

_SQL_BOATOS_VETOR = f"""
    SELECT b.id, b.checagem_id, 1 - (b.embedding <=> %(vetor)s) AS similaridade
    FROM boatos_conhecidos b JOIN documentos d ON d.id = b.checagem_id
    WHERE {_FILTRO}
    ORDER BY b.embedding <=> %(vetor)s
    LIMIT %(limite)s
"""

_SQL_BOATOS_TEXTO = f"""
    SELECT b.id, b.checagem_id, 1 - (b.embedding <=> %(vetor)s) AS similaridade
    FROM boatos_conhecidos b JOIN documentos d ON d.id = b.checagem_id,
         to_tsquery('portuguese', %(consulta)s) q
    WHERE b.tsv @@ q AND {_FILTRO}
    ORDER BY ts_rank_cd(b.tsv, q) DESC
    LIMIT %(limite)s
"""

_SQL_DOCUMENTOS = """
    SELECT id, url, titulo, agencia, dominio, natureza, publicado_em
    FROM documentos WHERE id = ANY(%(ids)s)
"""

_SQL_TRECHOS_DO_DOCUMENTO = """
    SELECT documento_id, ordem, texto, 1 - (embedding <=> %(vetor)s) AS similaridade
    FROM trechos WHERE documento_id = ANY(%(ids)s)
"""


class BaseChecagens:
    """Consultas de leitura na base. Recebe a conexão e o modelo de embedding (injetáveis nos testes)."""

    def __init__(self, conexao, modelo: ModeloEmbedding) -> None:
        self.conexao = conexao
        self.modelo = modelo

    def _fundir(self, sql_vetor: str, sql_texto: str, vetor, texto: str, excluir: list[str],
                limiar: float) -> dict[int, tuple[float, float]]:
        """Roda as duas listas, funde por RRF no nível do documento e devolve
        `{documento_id: (pontuação RRF, melhor similaridade)}` só para o que passa do limiar."""
        parametros = {"vetor": vetor, "excluir": excluir, "limite": CANDIDATOS}
        with self.conexao.transaction():
            # Filtros com HNSW: busca iterativa (pgvector >= 0.8) para não perder resultados depois do filtro.
            self.conexao.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
            self.conexao.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            por_vetor = self.conexao.execute(sql_vetor, parametros).fetchall()
            consulta = consulta_textual(texto)
            por_texto = (self.conexao.execute(sql_texto, {**parametros, "consulta": consulta}).fetchall()
                         if consulta else [])

        melhor: dict[int, float] = {}
        for _, documento_id, similaridade in (*por_vetor, *por_texto):
            melhor[documento_id] = max(melhor.get(documento_id, -1.0), float(similaridade))

        def documentos_em_ordem(linhas) -> list[int]:
            return list(dict.fromkeys(documento_id for _, documento_id, _ in linhas))

        pontos = rrf([documentos_em_ordem(por_vetor), documentos_em_ordem(por_texto)])
        return {d: (p, melhor[d]) for d, p in pontos.items() if melhor[d] >= limiar}

    def buscar(self, afirmacao: str, mensagem: str | None = None, *, limite: int = 5,
               excluir_urls: Sequence[str] = (), limiar_trecho: float | None = None,
               limiar_boato: float | None = None) -> list[Acerto]:
        """Documentos para a afirmação: primeiro as checagens de boatos parecidos com a mensagem, depois
        os documentos com trechos parecidos com a afirmação. Um documento aparece uma vez só.

        `excluir_urls` esconde documentos da busca; serve para a avaliação simular checagens fora da base
        (IA-07) sem manter um índice separado.
        """
        limiar_trecho = LIMIAR_TRECHO if limiar_trecho is None else limiar_trecho
        limiar_boato = LIMIAR_BOATO if limiar_boato is None else limiar_boato
        excluir = list(excluir_urls)
        # numpy: o adaptador do pgvector converte para `vector`; uma lista viraria `numeric[]`.
        vetor_afirmacao = np.asarray(self.modelo.consulta(afirmacao), dtype=np.float32)

        por_boato: dict[int, tuple[float, float]] = {}
        if mensagem and mensagem.strip():
            vetor_mensagem = np.asarray(self.modelo.consulta(mensagem), dtype=np.float32)
            por_boato = self._fundir(_SQL_BOATOS_VETOR, _SQL_BOATOS_TEXTO, vetor_mensagem, mensagem,
                                     excluir, limiar_boato)
        por_trecho = self._fundir(_SQL_TRECHOS_VETOR, _SQL_TRECHOS_TEXTO, vetor_afirmacao, afirmacao,
                                  excluir, limiar_trecho)

        ordem = [(d, "boato", *por_boato[d]) for d in sorted(por_boato, key=lambda d: -por_boato[d][0])]
        ordem += [(d, "trecho", *por_trecho[d]) for d in sorted(por_trecho, key=lambda d: -por_trecho[d][0])
                  if d not in por_boato]
        ordem = ordem[:limite]
        if not ordem:
            return []

        ids = [d for d, *_ in ordem]
        documentos = {linha[0]: linha for linha in
                      self.conexao.execute(_SQL_DOCUMENTOS, {"ids": ids}).fetchall()}
        trechos: dict[int, list[tuple[int, str, float]]] = {}
        for documento_id, posicao, texto, similaridade in self.conexao.execute(
                _SQL_TRECHOS_DO_DOCUMENTO, {"ids": ids, "vetor": vetor_afirmacao}).fetchall():
            trechos.setdefault(documento_id, []).append((posicao, texto, float(similaridade)))

        acertos = []
        for documento_id, via, pontuacao, similaridade in ordem:
            _, url, titulo, agencia, dominio, natureza, publicado_em = documentos[documento_id]
            acertos.append(Acerto(
                documento_id=documento_id, url=url, titulo=titulo, agencia=agencia, dominio=dominio,
                natureza=natureza, publicado_em=publicado_em, similaridade=similaridade,
                pontuacao=pontuacao, via=via, trechos=sorted(trechos.get(documento_id, [])),
            ))
        return acertos


TRECHOS_POR_DOCUMENTO = 3
"""Trechos de cada documento passados à síntese (os mais parecidos com a afirmação, na ordem do texto)."""


def para_evidencia(acerto: Acerto, afirmacao: str) -> Evidencia:
    """Converte um acerto da base no formato de evidência usado pela síntese.

    O `trecho` é o mais parecido com a afirmação; o `texto` junta os mais parecidos, na ordem em que
    aparecem no documento, para a síntese escolher citações literais.
    """
    melhores = sorted(acerto.trechos, key=lambda t: -t[2])[:TRECHOS_POR_DOCUMENTO]
    trecho = melhores[0][1] if melhores else ""
    texto = "\n\n".join(t for _, t, _ in sorted(melhores))
    titulo = acerto.titulo or acerto.dominio
    nivel, _ = relevancia(afirmacao, titulo, texto)
    data = acerto.publicado_em.isoformat() if hasattr(acerto.publicado_em, "isoformat") else ""
    return Evidencia(
        url=acerto.url, titulo=titulo, dominio=acerto.dominio, trecho=trecho, data=data,
        tipo="checagem" if acerto.natureza == "checagem" else "web", agencia=acerto.agencia,
        relevancia=nivel, lida=True, texto=texto, fonte_busca="base",
        similaridade=round(acerto.similaridade, 4),
    )


def buscar_na_base(afirmacao: str, mensagem: str | None = None, *, conexao=None, modelo=None,
                   limite: int = 5, excluir_urls: Sequence[str] = ()) -> list[Evidencia]:
    """Atalho para o grafo: abre a conexão (se não for passada), busca e converte em evidências."""
    from app.db.base_checagens import conectar
    from app.rag.embeddings import modelo_padrao

    modelo = modelo or modelo_padrao()
    if conexao is not None:
        acertos = BaseChecagens(conexao, modelo).buscar(afirmacao, mensagem, limite=limite,
                                                        excluir_urls=excluir_urls)
    else:
        with conectar() as nova:
            acertos = BaseChecagens(nova, modelo).buscar(afirmacao, mensagem, limite=limite,
                                                         excluir_urls=excluir_urls)
    return [para_evidencia(a, afirmacao) for a in acertos]

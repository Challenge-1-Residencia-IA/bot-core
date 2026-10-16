"""Embeddings da base de checagens (`intfloat/multilingual-e5-base`, 768 dimensões, CPU).

O E5 foi treinado com prefixos: textos indexados recebem `passage: ` e consultas, `query: `. Sem os
prefixos a qualidade da busca cai. Os vetores saem normalizados, então a distância de cosseno do
pgvector (`<=>`) equivale a 1 - produto escalar.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from app import config

DIMENSAO = 768
MAX_TOKENS = 512
"""Limite de entrada do modelo; o excedente é truncado, por isso os trechos indexados são curtos."""


class ModeloEmbedding(Protocol):
    nome: str

    def passagens(self, textos: Sequence[str]) -> list[list[float]]: ...

    def consulta(self, texto: str) -> list[float]: ...


class EmbeddingE5:
    def __init__(self, nome: str | None = None, *, lote: int = 16) -> None:
        from sentence_transformers import SentenceTransformer

        self.nome = nome or config.EMBEDDING_MODEL
        self.lote = lote
        self._modelo = SentenceTransformer(self.nome, device="cpu")
        self._modelo.max_seq_length = MAX_TOKENS
        dimensao = self._modelo.get_embedding_dimension()
        if dimensao != DIMENSAO:
            raise ValueError(f"{self.nome} gera vetores de {dimensao} dimensões; a base espera {DIMENSAO}")

    def _codificar(self, textos: list[str]) -> list[list[float]]:
        vetores = self._modelo.encode(textos, batch_size=self.lote, normalize_embeddings=True,
                                      show_progress_bar=False, convert_to_numpy=True)
        return vetores.tolist()

    def passagens(self, textos: Sequence[str]) -> list[list[float]]:
        return self._codificar([f"passage: {t}" for t in textos])

    def consulta(self, texto: str) -> list[float]:
        return self._codificar([f"query: {texto}"])[0]


@lru_cache(maxsize=1)
def modelo_padrao() -> EmbeddingE5:
    """Carrega o modelo uma vez por processo (cerca de 1 GB de RAM)."""
    return EmbeddingE5()

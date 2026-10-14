"""Blindagem (shielding) do conteúdo externo enviado ao modelo (GR-03).

Páginas da web e trechos da base são dados, nunca instruções. Antes de entrarem no prompt, os trechos
têm removidos os caracteres invisíveis, as marcações de papel de chat ("<|system|>", "[INST]") e as
frases que tentam instruir o modelo. A delimitação é feita pelo próprio formato JSON do prompt e pela
regra do prompt de sistema de ignorar instruções presentes nas fontes (spotlighting).
"""

from __future__ import annotations

import re

from app.guardrails.entrada import CONTROLE
from app.guardrails.padroes import INJECAO, normalizar

MARCACAO_DE_PAPEL = re.compile(
    r"<\|?\s*(im_start|im_end|system|user|assistant|endoftext)\s*\|?>|\[/?INST\]|<<\s*/?SYS\s*>>|</?\s*system\s*>",
    re.IGNORECASE,
)
SUBSTITUTO = "[instrução removida]"
FRASE = re.compile(r"[^.!?\n]+[.!?]?")


def blindar_trecho(texto: str) -> tuple[str, int]:
    """Devolve o trecho limpo e quantas frases com instruções ao modelo foram removidas."""
    limpo = MARCACAO_DE_PAPEL.sub(" ", CONTROLE.sub("", texto or ""))
    removidas = 0

    def substituir(frase: re.Match) -> str:
        nonlocal removidas
        if INJECAO.search(normalizar(frase.group(0))):
            removidas += 1
            return f" {SUBSTITUTO} "
        return frase.group(0)

    limpo = FRASE.sub(substituir, limpo)
    return re.sub(r"[ \t]+", " ", limpo).strip(), removidas

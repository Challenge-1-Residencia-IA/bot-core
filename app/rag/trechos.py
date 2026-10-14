"""Seleção de trechos literais de uma página para uma afirmação.

Os trechos são sempre fatias contíguas do texto original (`texto[inicio:fim]`), nunca texto
reescrito: o que o usuário lê como citação existe na página. A pontuação é lexical (sobreposição de
termos com a afirmação), sem modelo de embeddings, para rodar em qualquer máquina; um `pontuador`
semântico pode ser injetado depois sem mudar a API.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

STOPWORDS = {
    "acerca", "afirma", "afirmacao", "ainda", "algum", "alguma", "aquela", "aquele", "aquilo",
    "como", "contra", "dessa", "desse", "deste", "disso", "entao", "estao", "existe", "falso",
    "falsa", "haver", "isso", "mesmo", "outra", "outro", "pode", "podem", "quando", "quais",
    "seria", "sobre", "verdade", "verdadeira", "verdadeiro", "voce", "para", "pela", "pelo",
    "esta", "este", "essa", "esse", "foram", "sera", "todos", "todas", "muito", "mais",
}

NIVEIS = {"direta": 2, "relacionada": 1, "nao_relacionada": 0}

FIM_DE_FRASE = re.compile(r"(?<=[.!?…])\s+|\n+")

Pontuador = Callable[[str, list[str]], list[float]]
"""Recebe a afirmação e os trechos candidatos e devolve uma nota por trecho (maior = melhor)."""


def normalizar_espacos(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def sem_acento(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", (texto or "").casefold())
    return "".join(c for c in plano if not unicodedata.combining(c))


def termos(texto: str) -> set[str]:
    """Palavras de conteúdo (4+ caracteres, sem acento, sem stopwords)."""
    return {p for p in re.findall(r"[a-z0-9]{4,}", sem_acento(texto)) if p not in STOPWORDS}


def relevancia(afirmacao: str, titulo: str, trecho: str) -> tuple[str, float]:
    """Classifica a correspondência lexical entre afirmação e fonte; não infere verdade nem polaridade.

    Retorna o nível (`direta`, `relacionada`, `nao_relacionada`) e uma nota para ordenação. Os
    limiares são os do experimento (`rag/relevance.py`), mais um caso: cobertura de pelo menos
    metade dos termos no trecho também conta como `relacionada`, porque boatos longos raramente
    têm todos os termos no título da checagem.
    """
    consulta = termos(afirmacao)
    if not consulta:
        return "relacionada", 0.0
    fracao_titulo = len(consulta & termos(titulo)) / len(consulta)
    fracao_texto = len(consulta & termos(trecho[:1200])) / len(consulta)
    frase_direta = len(consulta) >= 2 and any(
        consulta <= termos(frase) for frase in FIM_DE_FRASE.split(trecho[:2000])
    )
    if len(consulta) >= 2 and (fracao_titulo == 1.0 or frase_direta):
        nivel = "direta"
    elif fracao_titulo >= 0.4 or (fracao_titulo >= 0.25 and fracao_texto >= 0.6) or fracao_texto >= 0.5:
        nivel = "relacionada"
    else:
        nivel = "nao_relacionada"
    return nivel, round(3 * fracao_titulo + fracao_texto, 4)


def frases(texto: str) -> list[tuple[int, int]]:
    """Posições (início, fim) das frases do texto, sem os espaços das bordas."""
    posicoes = []
    inicio = 0
    for separador in [*FIM_DE_FRASE.finditer(texto), None]:
        fim = separador.start() if separador else len(texto)
        bruto = texto[inicio:fim]
        if bruto.strip():
            esquerda = len(bruto) - len(bruto.lstrip())
            direita = len(bruto.rstrip())
            posicoes.append((inicio + esquerda, inicio + direita))
        if separador:
            inicio = separador.end()
    return posicoes


def janelas(texto: str, *, max_palavras: int = 80, max_caracteres: int = 700) -> list[tuple[int, int]]:
    """Janelas de frases consecutivas, cada uma uma fatia contígua do texto."""
    posicoes = frases(texto)
    resultado = []
    for i, (inicio, fim) in enumerate(posicoes):
        atual_fim = fim
        palavras = len(texto[inicio:fim].split())
        for proximo_inicio, proximo_fim in posicoes[i + 1:]:
            palavras_proximo = len(texto[proximo_inicio:proximo_fim].split())
            if palavras + palavras_proximo > max_palavras or proximo_fim - inicio > max_caracteres:
                break
            atual_fim = proximo_fim
            palavras += palavras_proximo
        if atual_fim - inicio > max_caracteres:
            corte = texto.rfind(" ", inicio, inicio + max_caracteres)
            atual_fim = corte if corte > inicio else inicio + max_caracteres
        resultado.append((inicio, atual_fim))
    return resultado


def _nota_lexical(consulta: set[str], trecho: str) -> float:
    if not consulta:
        return 0.0
    return len(consulta & termos(trecho)) / len(consulta)


def selecionar_trechos(
    afirmacao: str,
    texto: str,
    *,
    limite: int = 1,
    max_palavras: int = 80,
    max_caracteres: int = 700,
    pontuador: Pontuador | None = None,
) -> list[str]:
    """Devolve até `limite` trechos literais do `texto`, os mais relevantes para a `afirmacao`.

    Os trechos não se sobrepõem e saem na ordem de relevância. Empate favorece o início da página
    (título e lide costumam resumir a checagem). Texto vazio devolve lista vazia.
    """
    if not texto or not texto.strip():
        return []
    candidatos = janelas(texto, max_palavras=max_palavras, max_caracteres=max_caracteres)
    trechos = [texto[i:f] for i, f in candidatos]
    if pontuador is not None:
        notas = list(pontuador(afirmacao, trechos))
    else:
        consulta = termos(afirmacao)
        notas = [_nota_lexical(consulta, t) for t in trechos]
    ordem = sorted(range(len(candidatos)), key=lambda k: (-notas[k], candidatos[k][0]))
    escolhidos: list[tuple[int, int]] = []
    for k in ordem:
        inicio, fim = candidatos[k]
        if any(inicio < f and i < fim for i, f in escolhidos):
            continue
        escolhidos.append((inicio, fim))
        if len(escolhidos) >= limite:
            break
    return [texto[i:f].strip() for i, f in escolhidos]


def e_literal(citacao: str, *fontes: str, minimo: int = 12) -> bool:
    """Confere se `citacao` aparece literalmente em alguma fonte (ignorando espaços e caixa)."""
    alvo = normalizar_espacos(citacao).strip(" \"“”'‘’").casefold()
    if len(alvo) < minimo:
        return False
    return any(alvo in normalizar_espacos(fonte).casefold() for fonte in fontes if fonte)

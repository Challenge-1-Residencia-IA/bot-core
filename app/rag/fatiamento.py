"""Corte de textos em trechos para indexação (chunking).

Cada trecho é uma sequência de frases inteiras com até `max_palavras` palavras, e trechos vizinhos
repetem as últimas frases do anterior (`sobreposicao`), para que uma ideia dividida entre dois trechos
apareça inteira em pelo menos um. O limite de 180 palavras deixa o trecho abaixo dos 512 tokens do E5
em português. Os trechos são fatias do texto original, sem reescrita.
"""

from __future__ import annotations

from app.rag.trechos import frases


def fatiar(texto: str, *, max_palavras: int = 180, sobreposicao: int = 40) -> list[str]:
    if sobreposicao >= max_palavras:
        raise ValueError("sobreposicao deve ser menor que max_palavras")
    posicoes = _frases_curtas(texto, max_palavras)
    trechos: list[str] = []
    i = 0
    while i < len(posicoes):
        fim = i
        palavras = _palavras(texto, posicoes[i])
        while fim + 1 < len(posicoes) and palavras + _palavras(texto, posicoes[fim + 1]) <= max_palavras:
            fim += 1
            palavras += _palavras(texto, posicoes[fim])
        trechos.append(texto[posicoes[i][0]:posicoes[fim][1]])
        if fim + 1 >= len(posicoes):
            break
        # Recua frases inteiras até cobrir a sobreposição, sempre avançando pelo menos uma frase.
        proximo, repetidas = fim + 1, 0
        while proximo - 1 > i and repetidas + _palavras(texto, posicoes[proximo - 1]) <= sobreposicao:
            proximo -= 1
            repetidas += _palavras(texto, posicoes[proximo])
        i = proximo
    return trechos


def _palavras(texto: str, posicao: tuple[int, int]) -> int:
    return len(texto[posicao[0]:posicao[1]].split())


def _frases_curtas(texto: str, max_palavras: int) -> list[tuple[int, int]]:
    """Frases do texto; uma frase maior que o limite (texto sem pontuação) é dividida por palavras."""
    resultado = []
    for inicio, fim in frases(texto):
        if _palavras(texto, (inicio, fim)) <= max_palavras:
            resultado.append((inicio, fim))
            continue
        posicao, contagem, corte_inicio = inicio, 0, inicio
        while posicao < fim:
            espaco = texto.find(" ", posicao, fim)
            proximo = espaco if espaco != -1 else fim
            if texto[posicao:proximo].strip():
                contagem += 1
            if contagem == max_palavras or proximo == fim:
                resultado.append((corte_inicio, proximo))
                corte_inicio, contagem = proximo + 1, 0
            posicao = proximo + 1
    return [(i, f) for i, f in resultado if texto[i:f].strip()]

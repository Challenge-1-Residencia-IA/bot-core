"""Guardrail de saída: confere o texto apresentado ao usuário (GR-01, GR-04, GR-06).

Frases com veredito de verdadeiro/falso ou que julgam o usuário são removidas dos campos de texto
gerados pelo sistema. Trechos entre aspas são citações das fontes e não são alterados: apresentar a
conclusão de uma checagem é permitido; o sistema afirmar por conta própria, não.
"""

from __future__ import annotations

import re

from app.guardrails.padroes import CITACAO, JULGAMENTO_USUARIO, TEMAS_DE_RISCO, VEREDITO, normalizar

CAMPOS_DE_TEXTO = ("veredito", "contexto", "limitacoes")
FRASE = re.compile(r"[^.!?\n]+[.!?]?")


def violacoes(frase: str) -> list[str]:
    texto = normalizar(CITACAO.sub(" ", frase))
    encontradas = []
    if VEREDITO.search(texto):
        encontradas.append("GR-01 veredito")
    if JULGAMENTO_USUARIO.search(texto):
        encontradas.append("GR-04 julgamento do usuário")
    return encontradas


def filtrar_texto(texto: str) -> tuple[str, list[str]]:
    """Remove as frases que violam GR-01 ou GR-04 e lista as violações encontradas."""
    encontradas: list[str] = []

    def substituir(frase: re.Match) -> str:
        problemas = violacoes(frase.group(0))
        encontradas.extend(problemas)
        return "" if problemas else frase.group(0)

    return re.sub(r"\s{2,}", " ", FRASE.sub(substituir, texto or "")).strip(), encontradas


def temas_de_risco(texto: str) -> list[str]:
    normalizado = normalizar(texto or "")
    return [tema for tema, (padrao, _) in TEMAS_DE_RISCO.items() if padrao.search(normalizado)]


def aplicar_saida(resposta: dict, mensagem: str) -> dict:
    """Aplica os guardrails de saída à resposta da tutora e registra o que foi alterado."""
    registro = {"violacoes_removidas": [], "temas_de_risco": []}
    for campo in CAMPOS_DE_TEXTO:
        if isinstance(resposta.get(campo), str):
            resposta[campo], encontradas = filtrar_texto(resposta[campo])
            registro["violacoes_removidas"] += [f"{campo}: {v}" for v in encontradas]
    pontos = []
    for ponto in resposta.get("pontos_de_atencao", []):
        filtrado, encontradas = filtrar_texto(ponto)
        registro["violacoes_removidas"] += [f"pontos_de_atencao: {v}" for v in encontradas]
        if filtrado:
            pontos.append(filtrado)

    alegacao = resposta.get("alegacao_verificada") or resposta.get("afirmacao") or mensagem
    temas = temas_de_risco(f"{mensagem} {alegacao}")
    registro["temas_de_risco"] = temas
    avisos = [TEMAS_DE_RISCO[tema][1] for tema in temas]
    resposta["pontos_de_atencao"] = avisos + [p for p in pontos if p not in avisos]
    return registro

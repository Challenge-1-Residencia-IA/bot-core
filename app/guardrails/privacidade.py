"""Mascaramento de dados pessoais (LGPD) antes da busca na web e do LLM.

A busca envia a alegação a serviços de terceiros. CPF, CNPJ, e-mail e telefone são trocados por
marcadores ("[CPF]", "[TELEFONE]") e a análise segue com o resto da mensagem, em vez de ser recusada:
golpes costumam trazer um telefone ou e-mail, e a mensagem continua merecendo análise. É um filtro por
padrões (como os reconhecedores do Microsoft Presidio), não uma solução completa de DLP.
"""

from __future__ import annotations

import re

PADROES = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zA-Z]{2,}\b"),
    "cnpj": re.compile(r"(?<![\w.])\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?![\w])"),
    "cpf": re.compile(r"(?<![\w.])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\w])|(?<![\w.,])\d{11}(?![\w.,])"),
    "telefone": re.compile(r"(?<![\w.,])(?:\+?55[ -]?)?\(?\d{2}\)?[ -]?9?\d{4}[ -]?\d{4}(?![\w.,])"),
}
MARCADORES = {"email": "[E-MAIL]", "cnpj": "[CNPJ]", "cpf": "[CPF]", "telefone": "[TELEFONE]"}


def mascarar_dados_pessoais(texto: str) -> tuple[str, list[str]]:
    """Devolve o texto mascarado e os tipos de dado encontrados (na ordem de `PADROES`)."""
    encontrados = []
    for tipo, padrao in PADROES.items():
        texto, quantidade = padrao.subn(MARCADORES[tipo], texto)
        if quantidade:
            encontrados.append(tipo)
    return texto, encontrados

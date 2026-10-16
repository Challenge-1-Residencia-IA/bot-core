from typing import TypedDict


class EstadoAnalise(TypedDict, total=False):
    # Entrada (contrato do conector, app/contrato.py)
    chat_id: str
    mensagem_id: str
    mensagem: str
    """Mensagem já limpa pelo guardrail de entrada."""
    contexto: list

    # Guardrail de entrada
    entrada: dict
    """{"recusada": bool, "motivo": str | None, "sinais": [...]}"""

    # Extração (IA-01, IA-02)
    afirmacoes: list
    """[{"texto", "natureza", "tema", "o_que_verificar"}]"""
    elementos_manipulacao: list

    # Busca e síntese
    evidencias: list
    busca: dict
    """Como a busca decidiu: {"checagem_mais_recente": "AAAA-MM-DD" | None, "web": bool, "motivo_web": str | None}"""
    avisos: list
    """Avisos acrescentados ao fim da resposta pelo guardrail de saída (ex.: checagem antiga)."""
    sintese: dict

    # Saída
    resposta_texto: str
    saida: dict
    """Registro do guardrail de saída: violações removidas e temas de risco."""

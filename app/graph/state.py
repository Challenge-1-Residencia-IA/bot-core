from typing import TypedDict


class EstadoAnalise(TypedDict):
    chat_id: str
    mensagem: str
    afirmacoes: list
    evidencias: list
    sintese: str

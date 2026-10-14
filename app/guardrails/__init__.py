"""Guardrails do bot: entrada, blindagem do conteúdo externo e saída.

Portados de `experimentos/guardrails/` (avaliados no relatório técnico do modelo). No grafo, a entrada
roda antes de qualquer busca ou chamada ao LLM e a saída roda sobre o texto final. Os rails de entrada
usam o NeMo Guardrails (`camada_nemo.py`); `GUARDRAILS_NEMO=0` usa só as verificações por padrão.
"""

from __future__ import annotations

import os

from app.guardrails.blindagem import blindar_trecho
from app.guardrails.entrada import TAMANHO_MAXIMO, DecisaoEntrada, verificar_entrada
from app.guardrails.saida import filtrar_texto, temas_de_risco

COMO_USAR = "Envie a mensagem, alegação ou link que você recebeu e quer verificar."
RESPOSTAS_RECUSA = {
    "vazia": (COMO_USAR, "Nenhuma mensagem foi recebida."),
    "apresentacao": (
        "Olá! Eu ajudo a avaliar informações que você recebeu: mostro fontes, checagens já publicadas e "
        "os sinais de manipulação da mensagem, para você mesmo tirar a conclusão. " + COMO_USAR,
        "Saudação ou pergunta sobre o assistente.",
    ),
    "fora_do_escopo": (
        "Isso está fora do que eu faço. Eu só ajudo a verificar informações, como notícias, mensagens "
        "encaminhadas, alegações e links; não crio códigos, textos ou trabalhos nem respondo a pedidos "
        "gerais. " + COMO_USAR,
        "Pedido fora do escopo de verificação de informações.",
    ),
    "idioma": (
        "Por enquanto eu só analiso mensagens em português. Se a informação circula em português, envie "
        "essa versão ou o link original.",
        "Mensagem em outro idioma.",
    ),
    "tentativa_de_instrucao": (
        "Esta mensagem parece tentar alterar o funcionamento do assistente, e isso não é possível. " + COMO_USAR,
        "A mensagem contém instruções dirigidas ao assistente.",
    ),
}

AVISO_TRUNCADA = (
    f"A mensagem passou de {TAMANHO_MAXIMO} caracteres; a análise considerou só o início. "
    "Para analisar outra parte, envie o trecho ou o link original."
)

AVISO_DADOS_PESSOAIS = (
    "Antes de pesquisar, ocultei dados pessoais da mensagem ({tipos}). Não envie seus próprios dados "
    "para conferir uma mensagem."
)
NOMES_DADOS_PESSOAIS = {"email": "e-mail", "cnpj": "CNPJ", "cpf": "CPF", "telefone": "telefone"}

__all__ = [
    "AVISO_DADOS_PESSOAIS", "AVISO_TRUNCADA", "NOMES_DADOS_PESSOAIS", "RESPOSTAS_RECUSA", "DecisaoEntrada", "blindar_trecho", "filtrar_texto",
    "temas_de_risco", "verificar_entrada", "verificar_entrada_completa",
]


def verificar_entrada_completa(mensagem: str) -> DecisaoEntrada:
    """Rails de entrada pelo NeMo Guardrails; se ele estiver desativado (GUARDRAILS_NEMO=0) ou falhar,
    as verificações por padrão continuam valendo e a falha fica registrada nos sinais."""
    if os.getenv("GUARDRAILS_NEMO", "1") == "0":
        return verificar_entrada(mensagem)
    try:
        from app.guardrails.camada_nemo import verificar_entrada_nemo

        return verificar_entrada_nemo(mensagem)
    except Exception as erro:  # NeMo não instalado, Ollama fora do ar etc.
        decisao = verificar_entrada(mensagem)
        decisao.sinais.append(f"nemo_indisponivel:{type(erro).__name__}")
        return decisao

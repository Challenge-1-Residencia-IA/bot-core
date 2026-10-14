"""Nó de extração: afirmações, natureza, tema e elementos de manipulação (IA-01, IA-02).

O prompt (`app/prompts/extracao.md`) vem do `prompt-analise.txt` da branch `new-branch`, adaptado para
saída em JSON (structured output do Ollama) e para a taxonomia de `fluxo-e-interacao.md`.
"""

from __future__ import annotations

from pathlib import Path

from app import llm
from app.graph.state import EstadoAnalise

PROMPT_BASE = (Path(__file__).resolve().parents[2] / "prompts" / "extracao.md").read_text(encoding="utf-8")
NATUREZAS = ["fato_verificavel", "opiniao", "previsao", "satira", "fora_de_contexto"]
TEMAS = ["saude", "politica", "economia", "ciencia", "outro"]
MAX_AFIRMACOES = 3
# Técnicas de manipulação (IA-02): chave estável para avaliação e rótulo para exibir ao usuário.
ELEMENTOS_MANIPULACAO = {
    "urgencia": "urgência (\"URGENTE\", \"antes que apaguem\")",
    "apelo_emocional": "apelo ao medo ou à indignação",
    "pedido_compartilhamento": "pedido para compartilhar",
    "sem_fonte": "nenhuma fonte verificável citada",
    "autoridade_vaga": "autoridade vaga (\"um médico disse\")",
    "numeros_sem_origem": "números sem origem",
    "ataque_instituicoes": "ataque a instituições ou à imprensa",
    "conspiracao": "narrativa de conspiração ou de segredo escondido",
}

PROMPT = PROMPT_BASE + "\nValores aceitos em `elementos_manipulacao`:\n" + "\n".join(
    f"- `{chave}`: {rotulo}" for chave, rotulo in ELEMENTOS_MANIPULACAO.items())

ESQUEMA = {
    "type": "object",
    "properties": {
        "afirmacoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "texto": {"type": "string"},
                    "natureza": {"type": "string", "enum": NATUREZAS},
                    "tema": {"type": "string", "enum": TEMAS},
                    "o_que_verificar": {"type": "string"},
                },
                "required": ["texto", "natureza", "tema", "o_que_verificar"],
            },
        },
        "elementos_manipulacao": {"type": "array", "items": {"type": "string", "enum": list(ELEMENTOS_MANIPULACAO)}},
    },
    "required": ["afirmacoes", "elementos_manipulacao"],
}


def _normalizar(bruto: dict) -> dict:
    """Descarta itens fora do esquema (modelos pequenos às vezes ignoram o enum)."""
    afirmacoes = []
    for item in bruto.get("afirmacoes") or []:
        if not isinstance(item, dict) or not str(item.get("texto", "")).strip():
            continue
        afirmacoes.append({
            "texto": item["texto"].strip(),
            "natureza": item.get("natureza") if item.get("natureza") in NATUREZAS else "fato_verificavel",
            "tema": item.get("tema") if item.get("tema") in TEMAS else "outro",
            "o_que_verificar": str(item.get("o_que_verificar") or item["texto"]).strip(),
        })
    elementos = list(dict.fromkeys(e for e in bruto.get("elementos_manipulacao") or [] if e in ELEMENTOS_MANIPULACAO))
    return {"afirmacoes": afirmacoes[:MAX_AFIRMACOES], "elementos_manipulacao": elementos}


def extrair_afirmacoes(state: EstadoAnalise) -> dict:
    bruto = llm.gerar_json(PROMPT, f"Mensagem recebida pelo usuário:\n\"\"\"\n{state['mensagem']}\n\"\"\"", ESQUEMA)
    resultado = _normalizar(bruto)
    if not resultado["afirmacoes"]:
        # Sem afirmação extraída, a mensagem inteira vira a consulta (a busca ainda pode achar a checagem).
        resultado["afirmacoes"] = [{"texto": state["mensagem"][:300], "natureza": "fato_verificavel",
                                    "tema": "outro", "o_que_verificar": state["mensagem"][:200]}]
    return resultado

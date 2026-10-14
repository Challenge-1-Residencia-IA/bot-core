"""Nós de guardrails: entrada (antes de qualquer busca ou LLM) e saída (sobre o texto final)."""

from __future__ import annotations

from app.graph.state import EstadoAnalise
from app.guardrails import (AVISO_DADOS_PESSOAIS, AVISO_TRUNCADA, NOMES_DADOS_PESSOAIS, RESPOSTAS_RECUSA,
                            filtrar_texto, temas_de_risco, verificar_entrada_completa)
from app.guardrails.padroes import TEMAS_DE_RISCO


def guardrail_entrada(state: EstadoAnalise) -> dict:
    decisao = verificar_entrada_completa(state.get("mensagem", ""))
    atualizacao = {
        "mensagem": decisao.mensagem,
        "entrada": {"recusada": not decisao.permitida, "motivo": decisao.motivo, "sinais": decisao.sinais},
    }
    if not decisao.permitida:
        atualizacao["resposta_texto"] = RESPOSTAS_RECUSA[decisao.motivo][0]
    return atualizacao


def rota_apos_entrada(state: EstadoAnalise) -> str:
    return "recusada" if state["entrada"]["recusada"] else "analisar"


def guardrail_saida(state: EstadoAnalise) -> dict:
    """Acrescenta os avisos de tema de risco (GR-06) e, quando a resposta é texto livre, remove frases
    com veredito (GR-01) ou julgamento do usuário (GR-04).

    A resposta da tutora já chega filtrada pelo nó de síntese, que aplica o filtro só ao texto escrito
    pelo modelo: passar o texto final inteiro pelo filtro cortaria citações, títulos e endereços das fontes.
    """
    texto = state.get("resposta_texto", "")
    registro = dict(state.get("saida") or {})
    registro.setdefault("violacoes_removidas", [])
    if "fontes" not in state.get("sintese", {}):  # texto livre, sem a estrutura da tutora
        texto, encontradas = filtrar_texto(texto)
        registro["violacoes_removidas"] += encontradas

    alegacoes = " ".join(a["texto"] for a in state.get("afirmacoes", []))
    temas = temas_de_risco(f"{state.get('mensagem', '')} {alegacoes}")
    registro["temas_de_risco"] = temas
    avisos = [TEMAS_DE_RISCO[tema][1] for tema in temas]
    sinais = state.get("entrada", {}).get("sinais", [])
    if "truncada" in sinais:
        avisos.append(AVISO_TRUNCADA)
    dados = [NOMES_DADOS_PESSOAIS[s.split(":", 1)[1]] for s in sinais if s.startswith("dados_pessoais:")]
    if dados:
        avisos.append(AVISO_DADOS_PESSOAIS.format(tipos=", ".join(dados)))
    if avisos:
        texto += "\n\n" + "\n".join(f"⚠️ {aviso}" for aviso in avisos)
    return {"resposta_texto": texto, "saida": registro}

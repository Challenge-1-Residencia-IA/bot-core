"""Resposta da tutora: técnicas de manipulação, o que as fontes dizem, como verificar e fontes.

Portado de `experimentos/rag/tutor.py` (`ask`, `validate_answer`, `safe_fallback`), com três mudanças:

- não há classificação de "evidências favoráveis/contrárias" nem rótulo de avaliação: a tutora nunca
  dá veredito, só aponta técnicas, cita trechos literais e sugere leitura lateral;
- todas as fontes recuperadas vão ao modelo (checagens primeiro), sem o filtro de relevância "direta"
  que no experimento descartava a checagem certa e deixava o LLM sem fontes;
- itens inválidos (citação não literal, técnica fora da lista, passo com veredito) são descartados um a
  um; o fallback extrativo só entra quando a resposta inteira é inutilizável.

`LLMIndisponivel` não é tratada aqui: sobe para o grafo, que decide como avisar o usuário.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.rag.busca import Evidencia
from app.rag.trechos import NIVEIS, e_literal, normalizar_espacos

PROMPT_SISTEMA = (Path(__file__).parent / "prompts" / "tutora_sintese.txt").read_text(encoding="utf-8")
VERSAO_PROMPT = hashlib.sha256(PROMPT_SISTEMA.encode("utf-8")).hexdigest()[:12]

TECNICAS = {
    "urgencia": ("Urgência", "pressiona para agir rápido, antes de conferir"),
    "pedido_compartilhamento": ("Pedido de compartilhamento", "pede para espalhar a mensagem"),
    "autoridade_anonima": ("Autoridade sem nome", "cita uma fonte vaga, que não dá para conferir"),
    "apelo_emocional": ("Apelo emocional", "usa palavras que provocam medo, raiva ou indignação"),
    "outra": ("Outro recurso", ""),
}

ESQUEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tecnicas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string", "enum": list(TECNICAS)},
                    "trecho_mensagem": {"type": "string"},
                    "explicacao": {"type": "string"},
                },
                "required": ["nome", "trecho_mensagem"],
            },
        },
        "o_que_as_fontes_dizem": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"fonte": {"type": "integer"}, "citacao": {"type": "string"}},
                "required": ["fonte", "citacao"],
            },
        },
        "como_verificar": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tecnicas", "o_que_as_fontes_dizem", "como_verificar"],
}

VEREDITO = re.compile(
    r"\b(?:[eé]|eh|s[aã]o|parece(?:m)? ser|est[aá] comprovad[ao])\s+(?:uma?\s+)?"
    r"(?:fals[ao]s?|verdadeir[ao]s?|fake|mentira|ver[ií]dic[ao])\b"
    r"|\bfake news\b|\bboato confirmado\b|\bnot[ií]cia falsa\b|\bcomprovadamente\b",
    re.IGNORECASE,
)
"""Juízo de veracidade escrito pela própria tutora (não se aplica às citações literais das fontes)."""

PASSOS_PADRAO = [
    "Procure o assunto em agências de checagem (Lupa, Aos Fatos, Fato ou Fake, Boatos.org, E-farsas).",
    "Busque a fonte original citada na mensagem e confira se ela diz o mesmo.",
    "Confira a data e o autor: conteúdo antigo ou sem autor costuma circular fora de contexto.",
    "Veja se veículos de imprensa conhecidos noticiaram o mesmo fato.",
]

MAX_TRECHO_PROMPT = 1100
MAX_CITACAO = 320

Gerador = Callable[..., dict]
Blindador = Callable[[str], tuple[str, int]]


@dataclass
class Tecnica:
    nome: str
    trecho_mensagem: str
    explicacao: str = ""


@dataclass
class Citacao:
    fonte: int
    """Número da fonte em `RespostaTutora.fontes` (começa em 1)."""
    citacao: str


@dataclass
class RespostaTutora:
    afirmacao: str
    tecnicas: list[Tecnica]
    citacoes: list[Citacao]
    como_verificar: list[str]
    fontes: list[Evidencia]
    """Todas as fontes encontradas, numeradas a partir de 1 nesta ordem (checagens primeiro)."""
    fallback: bool = False
    diagnostico: dict = field(default_factory=dict)
    texto: str = ""
    """Resposta pronta para a plataforma de mensagens."""

    def para_dict(self) -> dict:
        dados = asdict(self)
        dados["fontes"] = [f.para_dict() for f in self.fontes]
        return dados


# Ordenação e prompt ---------------------------------------------------------------------------------

def ordenar_fontes(evidencias: list[Evidencia]) -> list[Evidencia]:
    """Checagens publicadas primeiro; depois por nível de relevância. Ordem estável."""
    return sorted(evidencias, key=lambda e: (e.tipo != "checagem", -NIVEIS.get(e.relevancia, 0)))


def _blindador_padrao() -> Blindador:
    try:
        from app.guardrails.blindagem import blindar_trecho
    except ImportError:
        controle = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f​-‏‪-‮⁠-⁤﻿]")
        return lambda texto: (controle.sub("", texto or ""), 0)
    return blindar_trecho


def _gerar_padrao(sistema: str, usuario: str, esquema: dict, **kwargs) -> dict:
    from app import llm

    return llm.gerar_json(sistema, usuario, esquema, **kwargs)


def montar_prompt(afirmacao: str, mensagem: str, fontes: list[Evidencia], blindar: Blindador,
                  *, max_fontes: int = 5) -> tuple[str, dict[int, str], int]:
    """JSON do prompt do usuário, os textos enviados por fonte (para validar citações) e quantas
    instruções a blindagem removeu."""
    enviados: dict[int, str] = {}
    removidas = 0
    itens = []
    for numero, fonte in enumerate(fontes[:max_fontes], 1):
        titulo, r1 = blindar(fonte.titulo)
        trecho, r2 = blindar(fonte.trecho[:MAX_TRECHO_PROMPT])
        removidas += r1 + r2
        enviados[numero] = f"{titulo}\n{trecho}"
        tipo = f"checagem publicada ({fonte.agencia})" if fonte.tipo == "checagem" else "página da web"
        itens.append({"numero": numero, "tipo": tipo, "titulo": titulo, "site": fonte.dominio,
                      "data": fonte.data, "trecho": trecho})
    mensagem_blindada, r3 = blindar(mensagem)
    removidas += r3
    dados = {"afirmacao": afirmacao, "mensagem_recebida": mensagem_blindada, "fontes": itens}
    return json.dumps(dados, ensure_ascii=False), enviados, removidas


# Validação ------------------------------------------------------------------------------------------

def validar_resposta(payload: dict, enviados: dict[int, str], mensagem: str) -> tuple[dict, dict]:
    """Confere a resposta do modelo item a item.

    Levanta `ValueError` se a estrutura for inutilizável. Itens isolados inválidos são descartados e
    contados: citação que não é cópia literal do trecho enviado (ou cita fonte inexistente), técnica
    fora da lista ou sem trecho literal da mensagem, explicação ou passo com juízo de veracidade.
    """
    if not isinstance(payload, dict):
        raise ValueError("O modelo não devolveu um objeto JSON")
    for campo in ESQUEMA["required"]:
        if not isinstance(payload.get(campo), list):
            raise ValueError(f"Campo ausente ou inválido: {campo}")
    descartes = {"tecnicas": 0, "citacoes": 0, "passos": 0}

    tecnicas, nomes_vistos = [], set()
    for item in payload["tecnicas"]:
        nome = item.get("nome") if isinstance(item, dict) else None
        trecho = normalizar_espacos(str(item.get("trecho_mensagem", ""))) if isinstance(item, dict) else ""
        explicacao = normalizar_espacos(str(item.get("explicacao", ""))) if isinstance(item, dict) else ""
        if (nome not in TECNICAS or nome in nomes_vistos or not e_literal(trecho, mensagem, minimo=3)
                or VEREDITO.search(explicacao)):
            descartes["tecnicas"] += 1
            continue
        nomes_vistos.add(nome)
        tecnicas.append(Tecnica(nome=nome, trecho_mensagem=trecho.strip(" \"“”"), explicacao=explicacao))

    citacoes, vistas = [], set()
    for item in payload["o_que_as_fontes_dizem"]:
        if not isinstance(item, dict):
            descartes["citacoes"] += 1
            continue
        numero, texto = item.get("fonte"), normalizar_espacos(str(item.get("citacao", ""))).strip(" \"“”")
        if (not isinstance(numero, int) or numero not in enviados or (numero, texto) in vistas
                or "[instrução removida]" in texto or not e_literal(texto, enviados[numero])):
            descartes["citacoes"] += 1
            continue
        vistas.add((numero, texto))
        citacoes.append(Citacao(fonte=numero, citacao=_encurtar(texto)))
    citacoes = citacoes[:4]

    passos = []
    for passo in payload["como_verificar"]:
        passo = normalizar_espacos(str(passo))
        if not passo or VEREDITO.search(passo):
            descartes["passos"] += 1
            continue
        passos.append(passo)
    return {"tecnicas": tecnicas, "citacoes": citacoes, "como_verificar": passos[:4]}, descartes


def _encurtar(texto: str, limite: int = MAX_CITACAO) -> str:
    """Corta no fim de uma palavra; o que fica continua sendo prefixo literal do original."""
    texto = normalizar_espacos(texto)
    if len(texto) <= limite:
        return texto
    return texto[: limite - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


# Fallback e formatação ------------------------------------------------------------------------------

def safe_fallback(afirmacao: str, evidencias: list[Evidencia], *, motivo: str = "",
                  limite: int = 3) -> RespostaTutora:
    """Resposta extrativa, sem LLM: trechos literais das melhores fontes e passos padrão de verificação."""
    fontes = ordenar_fontes(evidencias)
    citacoes = [Citacao(fonte=n, citacao=_encurtar(f.trecho))
                for n, f in enumerate(fontes, 1)
                if f.trecho.strip() and (f.tipo == "checagem" or f.relevancia != "nao_relacionada")][:limite]
    resposta = RespostaTutora(afirmacao=afirmacao, tecnicas=[], citacoes=citacoes,
                              como_verificar=PASSOS_PADRAO[:3], fontes=fontes, fallback=True,
                              diagnostico={"fallback": True, "motivo": motivo})
    resposta.texto = formatar_resposta(resposta)
    return resposta


def _descrever_fonte(numero: int, fonte: Evidencia) -> str:
    origem = fonte.agencia or fonte.dominio
    data = f", {fonte.data}" if fonte.data else ""
    return f"[{numero}] {fonte.titulo} ({origem}{data})\n{fonte.url}"


def formatar_resposta(resposta: RespostaTutora) -> str:
    """Texto para WhatsApp (negrito com `*`, itálico com `_`). Nunca contém veredito."""
    partes = []
    if resposta.tecnicas:
        linhas = []
        for t in resposta.tecnicas:
            rotulo, padrao = TECNICAS[t.nome]
            explicacao = t.explicacao or padrao
            linhas.append(f"• {rotulo}: “{t.trecho_mensagem}”" + (f" ({explicacao})" if explicacao else ""))
        partes.append("*O que observar na mensagem*\n" + "\n".join(linhas))
    if resposta.citacoes:
        linhas = []
        for c in resposta.citacoes:
            fonte = resposta.fontes[c.fonte - 1]
            linhas.append(f"• [{c.fonte}] {fonte.agencia or fonte.dominio}: “{c.citacao}”")
        titulo = "*O que as fontes dizem*" if not resposta.fallback else "*Trechos das fontes encontradas*"
        partes.append(titulo + "\n" + "\n".join(linhas))
    elif not resposta.fontes:
        partes.append("Não encontrei fontes que tratem desta mensagem nesta busca. "
                      "Isso não confirma nem desmente o conteúdo.")
    if resposta.como_verificar:
        partes.append("*Como conferir por conta própria*\n"
                      + "\n".join(f"{i}. {p}" for i, p in enumerate(resposta.como_verificar, 1)))
    if resposta.fontes:
        partes.append("*Fontes consultadas*\n"
                      + "\n".join(_descrever_fonte(n, f) for n, f in enumerate(resposta.fontes, 1)))
    partes.append("_Não digo se a mensagem é verdadeira ou falsa: mostro o que encontrei para você "
                  "decidir. Os trechos são cópias das páginas; confira o original._")
    return "\n\n".join(partes)


# Ponto de entrada -----------------------------------------------------------------------------------

def sintetizar(
    afirmacao: str,
    evidencias: list[Evidencia],
    *,
    mensagem: str | None = None,
    gerar: Gerador | None = None,
    blindar: Blindador | None = None,
    max_fontes: int = 5,
) -> RespostaTutora:
    """Monta a resposta da tutora para `afirmacao` a partir das `evidencias`.

    `mensagem` é o texto original recebido (para apontar técnicas); se ausente, usa `afirmacao`.
    `gerar` segue a assinatura de `app.llm.gerar_json`; `blindar`, a de
    `app.guardrails.blindagem.blindar_trecho`. Resposta do modelo inválida ou sem conteúdo útil vira
    `safe_fallback`. `app.llm.LLMIndisponivel` não é capturada.
    """
    mensagem = mensagem if mensagem is not None else afirmacao
    gerar = gerar or _gerar_padrao
    blindar = blindar or _blindador_padrao()
    fontes = ordenar_fontes(evidencias)
    usuario, enviados, removidas = montar_prompt(afirmacao, mensagem, fontes, blindar, max_fontes=max_fontes)
    base = {"versao_prompt": VERSAO_PROMPT, "fontes_no_prompt": len(enviados),
            "instrucoes_removidas_das_fontes": removidas}
    try:
        payload = gerar(PROMPT_SISTEMA, usuario, ESQUEMA, max_tokens=900)
        conteudo, descartes = validar_resposta(payload, enviados, mensagem)
    except (ValueError, TypeError, KeyError, AttributeError) as erro:
        resposta = safe_fallback(afirmacao, fontes, motivo=f"{type(erro).__name__}: {erro}")
        resposta.diagnostico.update(base)
        return resposta
    if enviados and not conteudo["citacoes"] and not conteudo["tecnicas"]:
        resposta = safe_fallback(afirmacao, fontes, motivo="Resposta sem citação literal nem técnica válida")
        resposta.diagnostico.update(base | {"descartes": descartes})
        return resposta
    resposta = RespostaTutora(
        afirmacao=afirmacao,
        tecnicas=conteudo["tecnicas"],
        citacoes=conteudo["citacoes"],
        como_verificar=conteudo["como_verificar"] or PASSOS_PADRAO[:3],
        fontes=fontes,
        diagnostico=base | {"fallback": False, "descartes": descartes},
    )
    resposta.texto = formatar_resposta(resposta)
    return resposta

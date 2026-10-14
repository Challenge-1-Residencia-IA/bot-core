"""Guardrail de entrada: valida a mensagem do usuário antes de qualquer busca ou chamada ao modelo
(RNF-02, GR-03 e escopo do produto)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.guardrails.padroes import APRESENTACAO, FORA_DO_ESCOPO, INJECAO, normalizar
from app.guardrails.privacidade import mascarar_dados_pessoais

# Tamanho máximo aceito (caracteres). Valor provisório até a medição da linha de base (IA-01, "LB").
TAMANHO_MAXIMO = 6000
# Detecção de idioma só em mensagens com pelo menos esta quantidade de palavras e sem link: em textos
# curtos o langdetect erra muito.
MIN_PALAVRAS_IDIOMA = 8
CONFIANCA_IDIOMA = 0.95
# Só estes idiomas são recusados. Nas mensagens curtas em português, o langdetect aponta catalão,
# italiano, galego ou esloveno; esses palpites são ignorados.
IDIOMAS_RECUSADOS = frozenset({"en", "es"})
# Palavras frequentes que existem em português e não em espanhol ou italiano. O langdetect confunde
# português sem acento (comum no WhatsApp) com espanhol e italiano; uma dessas palavras basta para
# manter a mensagem como português.
PALAVRAS_SO_PORTUGUES = frozenset(
    "do da dos das nao não voce você vocês voces com em ao aos pelo pela pelos pelas isso isto essa esse "
    "está esta estão estao são sao também tambem muito muita está foi ja já agora entao então porque "
    "vai vão vao mensagem compartilhe compartilhem urgente".split())
CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")


@dataclass
class DecisaoEntrada:
    permitida: bool
    mensagem: str
    """Mensagem limpa, que segue para a análise quando permitida."""
    motivo: str | None = None
    sinais: list[str] = field(default_factory=list)


def verificar_entrada(mensagem: str) -> DecisaoEntrada:
    """Recusa, sem chamar o modelo, mensagens vazias, saudações, pedidos fora do escopo e tentativas de
    alterar o comportamento do sistema; encurta as longas demais.

    Caracteres de controle e invisíveis (usados para esconder instruções) são removidos antes das
    demais verificações.
    """
    limpa = CONTROLE.sub("", mensagem or "").strip()
    sinais = ["caracteres_invisiveis"] if limpa != (mensagem or "").strip() else []
    if not limpa:
        return DecisaoEntrada(False, limpa, "vazia", sinais)
    if len(limpa) > TAMANHO_MAXIMO:
        # IA-01: acima do tamanho máximo, analisa-se o início da mensagem e o usuário é avisado.
        limpa = limpa[:TAMANHO_MAXIMO].rsplit(" ", 1)[0]
        sinais.append("truncada")
    limpa, dados_pessoais = mascarar_dados_pessoais(limpa)
    sinais += [f"dados_pessoais:{tipo}" for tipo in dados_pessoais]
    normalizada = normalizar(limpa)
    if INJECAO.search(normalizada):
        return DecisaoEntrada(False, limpa, "tentativa_de_instrucao", [*sinais, "injecao"])
    if APRESENTACAO.search(normalizada):
        return DecisaoEntrada(False, limpa, "apresentacao", sinais)
    if FORA_DO_ESCOPO.search(normalizada):
        return DecisaoEntrada(False, limpa, "fora_do_escopo", sinais)
    idioma = idioma_estrangeiro(limpa)
    if idioma:
        return DecisaoEntrada(False, limpa, "idioma", [*sinais, f"idioma:{idioma}"])
    return DecisaoEntrada(True, limpa, None, sinais)


def idioma_estrangeiro(texto: str) -> str | None:
    """Código do idioma quando a mensagem está, com alta confiança, em outro idioma que não o português."""
    palavras = re.findall(r"\w+", texto.lower())
    if len(palavras) < MIN_PALAVRAS_IDIOMA or re.search(r"https?://", texto):
        return None
    if PALAVRAS_SO_PORTUGUES.intersection(palavras):
        return None
    from langdetect import DetectorFactory, LangDetectException, detect_langs

    DetectorFactory.seed = 0  # resultado determinístico
    try:
        provavel = detect_langs(texto)[0]
    except LangDetectException:
        return None
    return provavel.lang if provavel.lang in IDIOMAS_RECUSADOS and provavel.prob >= CONFIANCA_IDIOMA else None

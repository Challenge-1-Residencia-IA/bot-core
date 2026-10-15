"""Validação do conteúdo baixado antes de gravar na base (e auditoria do que já foi gravado).

Uma URL antiga pode hoje levar a outro conteúdo: redirecionar para a página inicial do site ou abrir
uma galeria de fotos cujo texto extraído é só o menu do portal. Esses casos são recusados na ingestão
(o chamador usa o texto de reserva). A auditoria aplica sinais aproximados ao que já está no banco e
só lista os suspeitos, para revisão humana: o texto de reserva do FakeTrue.Br é ruidoso demais para
servir de gabarito automático.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from urllib.parse import urlparse

from app.rag.trechos import termos

CAMINHO_GALERIA = re.compile(r"/(album|albuns|galeria|galerias|fotos)/", re.IGNORECASE)
SEGMENTOS_IGNORADOS = {"amp", "index.html", "index.htm", "index.php"}
EXTENSOES = re.compile(r"(\.amp)?\.(s?html?|ghtml|php)$|\.amp$", re.IGNORECASE)
ANO = re.compile(r"\b(20[0-3]\d)\b")


def motivo_para_recusar(url_pedida: str, url_final: str) -> str | None:
    """Motivo para não usar a página baixada, ou None quando ela parece ser a matéria pedida."""
    pedida, final = urlparse(url_pedida), urlparse(url_final)
    if CAMINHO_GALERIA.search(pedida.path) or CAMINHO_GALERIA.search(final.path):
        return "galeria de fotos, sem texto de matéria"
    if pedida.path.strip("/") and not final.path.strip("/"):
        return "redirecionou para a página inicial do site"
    return None


def slug(url: str) -> str:
    """Parte descritiva da URL (o último segmento com letras, sem extensão e sem `amp`), com espaços."""
    segmentos = [s for s in urlparse(url).path.split("/")
                 if s and s.lower() not in SEGMENTOS_IGNORADOS and re.search(r"[a-zA-Z]", s)]
    if not segmentos:
        return ""
    return re.sub(r"[-_]+", " ", EXTENSOES.sub("", segmentos[-1])).strip()


def sinais_suspeitos(url: str, titulo: str, texto: str, publicado_em: date | None,
                     *, texto_da_pagina: bool = True) -> list[str]:
    """Sinais de que o texto gravado não é a matéria da URL.

    `texto_da_pagina` é False quando o texto veio de outra fonte (o corpus), e aí o endereço de galeria
    deixa de ser um problema.
    """
    sinais = []
    if texto_da_pagina and CAMINHO_GALERIA.search(urlparse(url).path):
        sinais.append("galeria")
    ano_url = re.search(r"/(20[0-2]\d)/", url)
    referencia = publicado_em.year if publicado_em else int(ano_url.group(1)) if ano_url else None
    if referencia:
        anos = Counter(int(a) for a in ANO.findall(texto))
        posteriores = sum(n for ano, n in anos.items() if ano >= max(2024, referencia + 3))
        if posteriores >= 10:
            sinais.append(f"cita {posteriores}x anos muito posteriores à publicação ({referencia})")
    termos_slug = termos(slug(url))
    if len(termos_slug) >= 3:
        no_titulo = len(termos_slug & termos(titulo or "")) / len(termos_slug)
        no_texto = len(termos_slug & termos(texto)) / len(termos_slug)
        if no_titulo < 0.2 and no_texto < 0.5:
            sinais.append(f"endereço sem relação com título e texto ({no_titulo:.0%} / {no_texto:.0%})")
    return sinais

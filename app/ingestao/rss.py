"""Atualiza a base com checagens publicadas em feeds RSS.

Uso: python -m app.ingestao.rss [--historico --max-paginas 500]

Itens que não são checagens (páginas de serviço, como os resultados de apuração eleitoral publicados
em massa pelo Boatos.org) são descartados pelo título, pela URL ou pela categoria, antes de qualquer
download. A coleta para quando o banco passa de `--limite-mb`, para não estourar a cota do Neon.
"""

from __future__ import annotations

import argparse
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlencode, urlparse

import requests
from bs4 import BeautifulSoup

from app.db.base_checagens import Documento, conectar, gravar_documento
from app.ingestao.faketrue import permitido_por_robots
from app.ingestao.validacao import motivo_para_recusar
from app.rag.busca import DOMINIOS_SEM_COLETA, USER_AGENT, casa_dominio, ler_pagina

log = logging.getLogger(__name__)
INICIO_HISTORICO = date(2024, 1, 1)
LIMITE_BANCO_MB = 450
"""Margem abaixo dos 512 MB do plano gratuito do Neon."""
NAO_CHECAGEM_TITULO = re.compile(r"^\s*resultado (das|da) (eleições|eleição|apuração)", re.IGNORECASE)
NAO_CHECAGEM_URL = re.compile(r"/resultado-(das-)?eleicoes|/apuracao-", re.IGNORECASE)
NAO_CHECAGEM_CATEGORIAS = {"apuração 2026", "apuracao 2026"}
MAX_FEED_BYTES = 5_000_000
FEEDS = {
    "Boatos.org": "https://www.boatos.org/feed",
    "E-farsas": "https://www.e-farsas.com/feed",
    "Aos Fatos": "https://www.aosfatos.org/noticias/feed/",
    "Agência Lupa": "https://www.agencialupa.org/feed/",
}
DOMINIOS = {
    "Boatos.org": ["boatos.org"],
    "E-farsas": ["e-farsas.com"],
    "Aos Fatos": ["aosfatos.org"],
    "Agência Lupa": ["agencialupa.org"],
}


@dataclass(frozen=True)
class Item:
    url: str
    titulo: str
    texto_feed: str
    publicado_em: date | None
    categorias: tuple[str, ...] = ()


def e_checagem(item: Item) -> bool:
    """False para páginas de serviço publicadas no feed (ex.: resultados de apuração por município)."""
    if NAO_CHECAGEM_TITULO.search(item.titulo) or NAO_CHECAGEM_URL.search(urlparse(item.url).path):
        return False
    return not any(c.strip().casefold() in NAO_CHECAGEM_CATEGORIAS for c in item.categorias)


def _texto_html(valor: str) -> str:
    return " ".join(BeautifulSoup(unescape(valor), "html.parser").stripped_strings)


def _data(valor: str) -> date | None:
    if not valor:
        return None
    try:
        return parsedate_to_datetime(valor).date()
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _campo(elemento: ET.Element, nome: str) -> str:
    filho = elemento.find(nome)
    return "" if filho is None else "".join(filho.itertext()).strip()


def ler_feed(xml: bytes, dominios: list[str]) -> list[Item]:
    raiz = ET.fromstring(xml)
    itens = []
    for elemento in raiz.findall(".//item"):
        url = _campo(elemento, "link").split("#", 1)[0].strip()
        partes = urlparse(url)
        if partes.scheme != "https" or not casa_dominio(url, dominios):
            continue
        texto = _texto_html(_campo(elemento, "{http://purl.org/rss/1.0/modules/content/}encoded")
                            or _campo(elemento, "description"))
        titulo = _texto_html(_campo(elemento, "title"))
        if not titulo or not texto:
            continue
        categorias = tuple(c.text.strip() for c in elemento.findall("category") if c.text and c.text.strip())
        itens.append(Item(url, titulo, texto, _data(_campo(elemento, "pubDate")), categorias))
    return itens


def baixar_feed(url: str, pagina: int = 1) -> bytes:
    endereco = url if pagina == 1 else url.rstrip("/") + "/?" + urlencode({"paged": pagina})
    resposta = requests.get(endereco, headers={"User-Agent": USER_AGENT}, timeout=(5, 20))
    resposta.raise_for_status()
    if len(resposta.content) > MAX_FEED_BYTES:
        raise ValueError("feed maior que o limite")
    return resposta.content


def montar_documento(item: Item, agencia: str, pausa_s: float = 0.5) -> Documento:
    texto, titulo, data, origem = item.texto_feed, item.titulo, item.publicado_em, "rss_feed"
    if not casa_dominio(item.url, DOMINIOS_SEM_COLETA) and permitido_por_robots(item.url):
        try:
            pagina = ler_pagina(item.url)
            if motivo := motivo_para_recusar(item.url, pagina.url):
                log.warning("Página recusada, usando o feed: %s (%s)", item.url, motivo)
            elif pagina.texto.strip():
                texto, titulo, origem = pagina.texto, pagina.titulo or titulo, "rss_pagina"
                data = _data(pagina.data) or data
        except (requests.RequestException, ValueError) as erro:
            log.warning("Página indisponível, usando feed: %s (%s)", item.url, erro)
        finally:
            time.sleep(pausa_s)
    return Documento(url=item.url, texto=texto, titulo=titulo, agencia=agencia,
                     natureza="checagem", publicado_em=data, origem=origem)


def tamanho_banco_mb(conexao) -> float:
    return conexao.execute("SELECT pg_database_size(current_database())").fetchone()[0] / 1_000_000


def coletar(conexao, modelo, *, historico: bool = False, max_paginas: int = 500,
            pausa_s: float = 0.5, limite_mb: float = LIMITE_BANCO_MB) -> dict[str, int]:
    if max_paginas < 1:
        raise ValueError("max_paginas deve ser positivo")
    contagem = {"novos": 0, "existentes": 0, "falhas": 0, "nao_checagem": 0}
    existentes = {linha[0] for linha in conexao.execute("SELECT url FROM documentos")}
    for agencia, feed in FEEDS.items():
        vistos: set[str] = set()
        for numero in range(1, max_paginas + 1 if historico else 2):
            try:
                itens = ler_feed(baixar_feed(feed, numero), DOMINIOS[agencia])
            except (requests.RequestException, ET.ParseError, ValueError) as erro:
                log.warning("Feed indisponível: %s página %d (%s)", agencia, numero, erro)
                contagem["falhas"] += 1
                break
            novos_na_pagina = [item for item in itens if item.url not in vistos]
            if not novos_na_pagina:
                break
            vistos.update(item.url for item in novos_na_pagina)
            tamanho = tamanho_banco_mb(conexao)
            if tamanho > limite_mb:
                log.error("Banco com %.0f MB, acima do limite de %.0f MB: coleta interrompida", tamanho, limite_mb)
                contagem["interrompida_por_tamanho"] = 1
                return contagem
            novos_antes = contagem["novos"]
            for item in novos_na_pagina:
                if historico and item.publicado_em and item.publicado_em < INICIO_HISTORICO:
                    continue
                if not e_checagem(item):
                    contagem["nao_checagem"] += 1
                    continue
                if item.url in existentes:
                    contagem["existentes"] += 1
                    continue
                try:
                    doc = montar_documento(item, agencia, pausa_s)
                    gravar_documento(conexao, doc, modelo)
                    conexao.commit()
                    existentes.add(item.url)
                    contagem["novos"] += 1
                except Exception:
                    conexao.rollback()
                    contagem["falhas"] += 1
                    log.exception("Falha ao gravar %s", item.url)
            datas = [item.publicado_em for item in novos_na_pagina if item.publicado_em]
            log.info("%s página %d (até %s): %d novos; banco com %.0f MB", agencia, numero,
                     min(datas) if datas else "?", contagem["novos"] - novos_antes, tamanho)
            if not historico or all(item.publicado_em and item.publicado_em < INICIO_HISTORICO
                                    for item in novos_na_pagina):
                break
            time.sleep(pausa_s)
    return contagem


def remover_nao_checagens(conexao) -> int:
    """Apaga da base itens de feed já gravados que não são checagens (trechos saem em cascata)."""
    linhas = conexao.execute("SELECT id, url, coalesce(titulo, '') FROM documentos WHERE origem LIKE 'rss%'").fetchall()
    ids = [i for i, url, titulo in linhas if not e_checagem(Item(url, titulo, "", None))]
    if ids:
        conexao.execute("DELETE FROM documentos WHERE id = ANY(%s)", (ids,))
        conexao.commit()
    return len(ids)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historico", action="store_true", help="pagina feeds até 2024 quando disponível")
    parser.add_argument("--max-paginas", type=int, default=500)
    parser.add_argument("--limite-mb", type=float, default=LIMITE_BANCO_MB,
                        help="interrompe a coleta quando o banco passa deste tamanho")
    parser.add_argument("--remover-nao-checagens", action="store_true",
                        help="só apaga da base os itens de feed que o filtro descarta, sem coletar")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from app.rag.embeddings import modelo_padrao
    with conectar() as conexao:
        if args.remover_nao_checagens:
            log.info("Itens que não são checagens removidos: %d", remover_nao_checagens(conexao))
            return
        log.info("Coleta concluída: %s", coletar(conexao, modelo_padrao(), historico=args.historico,
                                                  max_paginas=args.max_paginas, limite_mb=args.limite_mb))


if __name__ == "__main__":
    main()

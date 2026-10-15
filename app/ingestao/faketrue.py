"""Ingestão do FakeTrue.Br na base de checagens.

Cada linha do corpus é um par boato (página do Boatos.org, `link_f`) × checagem ou notícia (`link_t`).
O texto do corpus vem pré-processado (minúsculas, sem hífens e sem parte da pontuação) e sem título nem
data, então as páginas são baixadas de novo pela URL; o texto do corpus só é usado quando a página não
pode ser lida (fora do ar, bloqueio, robots.txt).

O que é gravado:
- `documentos`/`trechos`: as páginas de `link_t` (checagens e notícias) e as páginas do Boatos.org de
  `link_f`, que são checagens completas do próprio boato;
- `boatos_conhecidos`: o texto de cada boato, ligado à sua página do Boatos.org (par exato).

Boatos do golden set ficam fora de `boatos_conhecidos`, para a avaliação não encontrar a mensagem já
indexada. As páginas de checagem deles entram normalmente: são o que a busca precisa encontrar.

Uso (na raiz do repositório, com DATABASE_URL definida):
    python -m app.ingestao.faketrue --corpus FakeTrueBr_corpus.csv --golden-set golden_set_v1.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import requests

from app.db.base_checagens import Documento, conectar, gravar_boato, gravar_documento
from app.ingestao.validacao import motivo_para_recusar, slug
from app.rag.busca import USER_AGENT, agencia_de, ler_pagina

ORIGEM_PAGINA = "faketrue"
ORIGEM_TEXTO_DO_CORPUS = "faketrue_corpus"
MARCAS_CHECAGEM = re.compile(r"\bcircula|fato ou fake|é fake|\bchecagem", re.IGNORECASE)
"""Mesma regra de `experimentos/dados/preparar.py` para separar checagem de notícia no lado verdadeiro."""

log = logging.getLogger("ingestao.faketrue")


@dataclass
class Par:
    url_boato: str
    texto_boato: str
    titulo_boato: str
    url_checagem: str
    texto_checagem: str


def ler_corpus(caminho: Path) -> list[Par]:
    csv.field_size_limit(sys.maxsize)
    with caminho.open(encoding="utf-8", newline="") as arquivo:
        return [Par(url_boato=l["link_f"].strip(), texto_boato=l["fake"], titulo_boato=l["title_fake"].strip(),
                    url_checagem=l["link_t"].strip(), texto_checagem=l["true"])
                for l in csv.DictReader(arquivo)]


def _normalizar(texto: str) -> str:
    """Minúsculas, só letras e números separados por um espaço (ignora pontuação e aspas)."""
    return " ".join(re.findall(r"\w+", texto.casefold()))


@dataclass
class GoldenSet:
    """O que identifica um boato do golden set dentro do corpus.

    Um mesmo boato aparece em vários pares do corpus, com outra URL e pequenas variações de texto, então
    a comparação é por trecho: o começo da mensagem ou uma afirmação anotada contida no texto do boato.
    """

    urls: set[str] = field(default_factory=set)
    trechos: set[str] = field(default_factory=set)

    def contem(self, par: Par) -> bool:
        if par.url_boato in self.urls:
            return True
        texto = _normalizar(par.texto_boato)
        return any(trecho in texto for trecho in self.trechos)


TAMANHO_TRECHO = 60
MINIMO_TRECHO = 25
"""Trechos mais curtos que isso (afirmações genéricas) dariam falsos positivos."""


def ler_golden_set(caminho: Path) -> GoldenSet:
    golden = GoldenSet()
    with caminho.open(encoding="utf-8") as arquivo:
        for linha in arquivo:
            item = json.loads(linha)
            if item.get("fonte_esperada"):
                golden.urls.add(item["fonte_esperada"].strip())
            candidatos = [item["texto"], *(a.get("texto", "") for a in item.get("afirmacoes", []))]
            for candidato in candidatos:
                trecho = _normalizar(candidato)[:TAMANHO_TRECHO].strip()
                if len(trecho) >= MINIMO_TRECHO:
                    golden.trechos.add(trecho)
    return golden


def natureza_de(url: str, texto: str) -> str:
    return "checagem" if agencia_de(url) or MARCAS_CHECAGEM.search(texto[:2000]) else "noticia"


def data_de(valor: str) -> date | None:
    try:
        return date.fromisoformat(valor[:10])
    except (TypeError, ValueError):
        return None


def titulo_do_endereco(url: str) -> str:
    """Título provisório a partir da parte descritiva da URL, para documentos sem página lida."""
    return (slug(url) or urlparse(url).hostname or url).capitalize()


@lru_cache(maxsize=None)
def _robots(origem: str) -> robotparser.RobotFileParser | None:
    """robots.txt baixado com o mesmo User-Agent da coleta.

    `RobotFileParser.read()` usa o agente padrão do urllib, que muitos sites bloqueiam com 403 e o
    parser interpreta como "tudo proibido". Mesma convenção do parser: 401/403 proíbem, outros 4xx liberam.
    """
    try:
        resposta = requests.get(origem + "/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=10)
    except requests.RequestException:
        return None
    leitor = robotparser.RobotFileParser()
    if resposta.status_code in (401, 403):
        leitor.disallow_all = True
    elif resposta.ok:
        leitor.parse(resposta.text.splitlines())
    else:
        leitor.allow_all = True
    return leitor


def permitido_por_robots(url: str) -> bool:
    partes = urlparse(url)
    leitor = _robots(f"{partes.scheme}://{partes.netloc}")
    return leitor is None or leitor.can_fetch(USER_AGENT, url)


def baixar(url: str, pausa_s: float) -> tuple[str, object]:
    if not permitido_por_robots(url):
        return url, PermissionError("robots.txt não permite a coleta")
    try:
        return url, ler_pagina(url)
    except Exception as erro:  # noqa: BLE001 - qualquer falha de leitura cai no texto do corpus
        return url, erro
    finally:
        time.sleep(pausa_s)


def montar_documento(url: str, pagina, texto_corpus: str | None) -> Documento | None:
    if not isinstance(pagina, Exception) and (motivo := motivo_para_recusar(url, pagina.url)):
        log.warning("Página recusada, usando o texto do corpus: %s (%s)", url, motivo)
        pagina = ValueError(motivo)
    if not isinstance(pagina, Exception):
        return Documento(url=url, texto=pagina.texto, natureza=natureza_de(url, pagina.texto), origem=ORIGEM_PAGINA,
                         titulo=pagina.titulo, agencia=agencia_de(url), publicado_em=data_de(pagina.data))
    if not texto_corpus or not texto_corpus.strip():
        return None
    texto = " ".join(texto_corpus.split())
    return Documento(url=url, texto=texto, natureza=natureza_de(url, texto), origem=ORIGEM_TEXTO_DO_CORPUS,
                     titulo=titulo_do_endereco(url), agencia=agencia_de(url))


def ingerir(pares: list[Par], golden: GoldenSet, *, conexao, modelo,
            baixar_paginas: bool = True, atualizar: bool = False, paralelo: int = 4, pausa_s: float = 0.5,
            somente_urls: set[str] | None = None) -> Counter:
    resultado: Counter = Counter()
    # Texto do corpus por URL de checagem; páginas do Boatos.org não têm texto de reserva (o corpus só traz o boato).
    reserva: dict[str, str | None] = {}
    for par in pares:
        reserva.setdefault(par.url_checagem, par.texto_checagem)
        reserva.setdefault(par.url_boato, None)

    existentes = {linha[0] for linha in conexao.execute("SELECT url FROM documentos")}
    pendentes = [url for url in reserva if (atualizar or url not in existentes)
                 and (somente_urls is None or url in somente_urls)]
    resultado["ja_na_base"] = len(reserva) - len(pendentes)
    log.info("%d páginas no corpus; %d a processar", len(reserva), len(pendentes))

    # Downloads em paralelo; cada documento é gravado (e confirmado) assim que chega, então uma
    # interrupção não perde o que já foi feito e a próxima execução continua de onde parou.
    with ThreadPoolExecutor(max_workers=paralelo) as executor:
        if baixar_paginas:
            lidas = executor.map(lambda u: baixar(u, pausa_s), pendentes)
        else:
            lidas = ((url, RuntimeError("download desligado")) for url in pendentes)
        for i, (url, pagina) in enumerate(lidas, 1):
            doc = montar_documento(url, pagina, reserva[url])
            if doc is None:
                resultado["sem_texto"] += 1
                log.warning("Sem texto, ignorada: %s (%s)", url, pagina)
                continue
            resultado[f"origem_{doc.origem}"] += 1
            resultado[gravar_documento(conexao, doc, modelo)] += 1
            conexao.commit()
            if i % 100 == 0:
                log.info("%d/%d documentos", i, len(pendentes))

    na_base = {linha[0] for linha in conexao.execute("SELECT url FROM documentos")}
    for par in pares:
        if golden.contem(par):
            resultado["boato_do_golden_set"] += 1
            continue
        if par.url_boato not in na_base or not par.texto_boato.strip():
            resultado["boato_sem_checagem"] += 1
            continue
        resultado[f"boato_{gravar_boato(conexao, par.url_boato, par.texto_boato, modelo)}"] += 1
        conexao.commit()
    return resultado


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True, help="FakeTrueBr_corpus.csv")
    parser.add_argument("--golden-set", type=Path, required=True, help="golden_set_v1.jsonl (boatos excluídos)")
    parser.add_argument("--limite", type=int, default=0, help="processa só as N primeiras linhas (teste)")
    parser.add_argument("--sem-download", action="store_true", help="usa só o texto do corpus")
    parser.add_argument("--atualizar", action="store_true", help="baixa de novo páginas que já estão na base")
    parser.add_argument("--paralelo", type=int, default=4)
    parser.add_argument("--url", action="append", dest="urls", metavar="URL",
                        help="processa só esta URL do corpus (pode repetir); use com --atualizar para refazer")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from app.rag.embeddings import modelo_padrao

    pares = ler_corpus(args.corpus)
    if args.limite:
        pares = pares[:args.limite]
    golden = ler_golden_set(args.golden_set)
    inicio = time.time()
    with conectar() as conexao:
        resultado = ingerir(pares, golden, conexao=conexao, modelo=modelo_padrao(),
                            baixar_paginas=not args.sem_download, atualizar=args.atualizar, paralelo=args.paralelo,
                            somente_urls=set(args.urls) if args.urls else None)
    log.info("Concluído em %.0f s: %s", time.time() - inicio, dict(sorted(resultado.items())))


if __name__ == "__main__":
    main()

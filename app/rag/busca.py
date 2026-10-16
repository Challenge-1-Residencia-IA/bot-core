"""Busca de evidências na web: primeiro checagens já publicadas, depois a web geral (IA-03).

Portado de `experimentos/rag/web.py` (`WebResearcher.research` e `read_page`). O provedor de busca e o
leitor de páginas são injetáveis: os testes usam versões falsas, e trocar `ddgs` por Tavily ou SearXNG
é escrever outro `ProvedorBusca`, sem mexer no resto (padrão Strategy / porta e adaptador).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from io import BytesIO
from typing import Protocol
from urllib.parse import urljoin, urlparse

import requests

from app.rag.trechos import NIVEIS, relevancia, selecionar_trechos

MAX_BYTES = 5_000_000
MAX_TEXTO = 30_000
RESULTADOS_POR_CONSULTA = 12
MAX_CONSULTA = 300
USER_AGENT = "BotCore/0.1 (verificacao educativa de informacoes)"
URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
SUFIXOS_COMPOSTOS = {"com.br", "gov.br", "org.br", "jus.br", "leg.br", "co.uk", "com.au"}

AGENCIAS_CHECAGEM: list[tuple[str, str | None, str]] = [
    # (domínio, trecho obrigatório no caminho da URL ou None, nome exibido)
    ("lupa.uol.com.br", None, "Agência Lupa"),
    ("lupa.news", None, "Agência Lupa"),
    ("agencialupa.org", None, "Agência Lupa"),
    ("piaui.folha.uol.com.br", "/lupa/", "Agência Lupa"),
    ("piaui.uol.com.br", "/lupa/", "Agência Lupa"),
    ("aosfatos.org", None, "Aos Fatos"),
    ("g1.globo.com", "/fato-ou-fake", "Fato ou Fake (G1)"),
    ("boatos.org", None, "Boatos.org"),
    ("e-farsas.com", None, "E-farsas"),
    ("estadao.com.br", "estadao-verifica", "Estadão Verifica"),
    ("projetocomprova.com.br", None, "Projeto Comprova"),
    ("checamos.afp.com", None, "AFP Checamos"),
    ("noticias.uol.com.br", "/confere", "UOL Confere"),
    ("reuters.com", "/fact-check", "Reuters Fact Check"),
]
"""Seções de checagem de fatos (nível 3 de `fontes-e-evidencias.md`)."""

DOMINIOS_SEM_COLETA = ["agencialupa.org", "aosfatos.org", "ibge.gov.br", "agenciadenoticias.ibge.gov.br"]
"""Publicadores que não autorizam coleta automatizada: a página não é baixada; usa-se o resumo do buscador."""

DOMINIOS_INSTITUCIONAIS = [
    "gov.br", "tse.jus.br", "stf.jus.br", "senado.leg.br", "camara.leg.br", "who.int", "paho.org",
    "reuters.com", "apnews.com", "bbc.com", "g1.globo.com", "agenciabrasil.ebc.com.br",
]
"""Fontes de nível 1 a 3 usadas só como critério de desempate na web geral."""


@dataclass
class ResultadoBusca:
    titulo: str
    url: str
    resumo: str = ""


@dataclass
class Pagina:
    url: str
    titulo: str
    texto: str
    data: str = ""


@dataclass
class Evidencia:
    url: str
    titulo: str
    dominio: str
    """Host sem `www.` (ex.: `g1.globo.com`)."""
    trecho: str
    """Trecho literal da página (ou do resumo do buscador, quando `lida` é False)."""
    data: str = ""
    tipo: str = "web"
    """`checagem` (agência de checagem) ou `web` (busca geral)."""
    agencia: str | None = None
    relevancia: str = "relacionada"
    lida: bool = True
    texto: str = field(default="", repr=False)
    """Texto completo extraído (até 30 mil caracteres), para nova seleção de trechos sem baixar de novo."""

    def para_dict(self, *, com_texto: bool = False) -> dict:
        dados = asdict(self)
        if not com_texto:
            dados.pop("texto")
        return dados


class ProvedorBusca(Protocol):
    def __call__(self, consulta: str, max_resultados: int) -> list[ResultadoBusca]: ...


LeitorPagina = Callable[[str], Pagina]


class ProvedorDDGS:
    """Busca gratuita pelo DuckDuckGo (`ddgs`). Sem chave, mas é scraping não oficial."""

    def __init__(self, *, regiao: str = "br-pt", timeout: int = 10) -> None:
        self.regiao = regiao
        self.timeout = timeout

    def __call__(self, consulta: str, max_resultados: int) -> list[ResultadoBusca]:
        from ddgs import DDGS

        brutos = DDGS(timeout=self.timeout).text(
            consulta, region=self.regiao, safesearch="moderate", max_results=max_resultados
        ) or []
        return [ResultadoBusca(titulo=r.get("title", ""), url=r.get("href") or r.get("url") or "",
                               resumo=r.get("body", "")) for r in brutos]


# Domínios e URLs ------------------------------------------------------------------------------------

def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def casa_dominio(url: str, dominios: list[str]) -> bool:
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in dominios)


def grupo_origem(url: str) -> str:
    """Domínio registrável (ex.: `g1.globo.com` -> `globo.com`), usado para não repetir a mesma origem."""
    host = _host(url).removeprefix("www.")
    if host == "ibge.gov.br" or host.endswith(".ibge.gov.br"):
        return "ibge"
    partes = host.split(".")
    if len(partes) >= 3 and ".".join(partes[-2:]) in SUFIXOS_COMPOSTOS:
        return ".".join(partes[-3:])
    return ".".join(partes[-2:]) if len(partes) >= 2 else host


def agencia_de(url: str) -> str | None:
    """Nome da agência quando a URL é uma checagem; None para outras páginas e para a capa da seção."""
    caminho = urlparse(url).path.lower()
    segmentos = [s for s in caminho.split("/") if s]
    for dominio, trecho_caminho, nome in AGENCIAS_CHECAGEM:
        if not casa_dominio(url, [dominio]):
            continue
        if trecho_caminho is None:
            return nome if segmentos else None
        if trecho_caminho in caminho and len(segmentos) >= 2:
            return nome
    return None


def montar_consulta(afirmacao: str) -> str:
    """Remove URLs e espaços extras e corta em 300 caracteres sem quebrar palavra."""
    consulta = re.sub(r"\s+", " ", URL.sub(" ", afirmacao or "")).strip()
    if len(consulta) > MAX_CONSULTA:
        consulta = consulta[:MAX_CONSULTA].rsplit(" ", 1)[0]
    return consulta


# Leitura de páginas ---------------------------------------------------------------------------------

def reparar_mojibake(valor: str) -> str:
    """Corrige sequências UTF-8 lidas como Latin-1, sem alterar texto íntegro."""
    def corrigir(m: re.Match[str]) -> str:
        palavra = m.group()
        if not any(marca in palavra for marca in ("Ã", "Â", "â€")):
            return palavra
        try:
            corrigida = palavra.encode("cp1252").decode("utf-8")
        except (UnicodeError, LookupError):
            return palavra
        ruins = corrigida.count("Ã") + corrigida.count("Â")
        return corrigida if ruins < palavra.count("Ã") + palavra.count("Â") else palavra

    return re.sub(r"\S+", corrigir, valor or "")


def _decodificar(bruto: bytes, declarado: str | None) -> str:
    try:
        return bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        return bruto.decode(declarado or "cp1252", errors="replace")


def checar_url_publica(url: str) -> None:
    """Bloqueia SSRF: só HTTP(S), portas padrão e endereços públicos."""
    partes = urlparse(url)
    if partes.scheme not in {"http", "https"} or not partes.hostname or partes.username or partes.password:
        raise ValueError("URL pública HTTP(S) inválida")
    if partes.port not in {None, 80, 443}:
        raise ValueError("Porta da URL não permitida")
    host = partes.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError("Endereço local não permitido")
    try:
        enderecos = socket.getaddrinfo(host, partes.port or (443 if partes.scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
    except socket.gaierror as erro:
        raise ValueError(f"Não foi possível resolver o domínio: {host}") from erro
    if not enderecos or any(not ipaddress.ip_address(e[4][0]).is_global for e in enderecos):
        raise ValueError("A URL resolve para endereço não público")


def _extrair_html(html: str) -> tuple[str, str, str]:
    """Texto principal, título e data. trafilatura primeiro; BeautifulSoup se ele não extrair nada."""
    titulo = data = ""
    texto = ""
    try:
        import trafilatura

        texto = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
        metadados = trafilatura.extract_metadata(html)
        if metadados:
            titulo = metadados.title or ""
            data = metadados.date or ""
    except ImportError:
        pass
    if not texto or not titulo:
        from bs4 import BeautifulSoup

        sopa = BeautifulSoup(html, "html.parser")
        if not titulo and sopa.title and sopa.title.string:
            titulo = sopa.title.string.strip()
        if not texto:
            for tag in sopa(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
                tag.decompose()
            raiz = sopa.find("article") or sopa.find("main") or sopa.body or sopa
            paragrafos = [p.get_text(" ", strip=True) for p in raiz.find_all(["h1", "h2", "p", "li"])]
            texto = "\n".join(p for p in paragrafos if p)
    return texto, titulo, data


def ler_pagina(url: str) -> Pagina:
    """Baixa HTML, PDF ou texto, conferindo a URL a cada redirecionamento."""
    atual = url
    for _ in range(5):
        checar_url_publica(atual)
        if casa_dominio(atual, DOMINIOS_SEM_COLETA):
            raise ValueError("Coleta automatizada não habilitada para este publicador")
        resposta = requests.get(atual, headers={"User-Agent": USER_AGENT}, timeout=(5, 15),
                                allow_redirects=False, stream=True)
        if resposta.is_redirect:
            destino = urljoin(atual, resposta.headers.get("Location", ""))
            resposta.close()
            if destino == atual:
                raise ValueError("Redirecionamento inválido")
            atual = destino
            continue
        try:
            resposta.raise_for_status()
            tipo = resposta.headers.get("Content-Type", "").lower()
            if not any(t in tipo for t in ("text/html", "application/pdf", "text/plain")):
                raise ValueError(f"Tipo de página não suportado: {tipo or 'desconhecido'}")
            partes, tamanho = [], 0
            for parte in resposta.iter_content(chunk_size=65536):
                tamanho += len(parte)
                if tamanho > MAX_BYTES:
                    raise ValueError("Página maior que 5 MB")
                partes.append(parte)
            bruto = b"".join(partes)
            titulo, data = "", ""
            if "pdf" in tipo:
                try:
                    from pypdf import PdfReader
                except ImportError as erro:
                    raise ValueError("Leitura de PDF indisponível (pypdf não instalado)") from erro
                texto = "\n\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(bruto)).pages)
            elif "html" in tipo:
                texto, titulo, data = _extrair_html(_decodificar(bruto, resposta.encoding))
            else:
                texto = _decodificar(bruto, resposta.encoding)
            titulo = reparar_mojibake(titulo or urlparse(atual).path.rsplit("/", 1)[-1] or grupo_origem(atual))
            texto = re.sub(r"\n{3,}", "\n\n", reparar_mojibake(texto)).strip()
            if len(texto) < 100:
                raise ValueError("A página não forneceu texto suficiente")
            return Pagina(url=atual, titulo=titulo, texto=texto[:MAX_TEXTO], data=data)
        finally:
            resposta.close()
    raise ValueError("Redirecionamentos em excesso")


# Busca ----------------------------------------------------------------------------------------------

def _ordenar(afirmacao: str, resultados: list[ResultadoBusca]) -> list[ResultadoBusca]:
    """Checagens primeiro (na ordem do buscador); depois a web por relevância lexical e tipo de fonte."""
    checagens = [r for r in resultados if agencia_de(r.url)]
    web = [r for r in resultados if not agencia_de(r.url)]

    def chave(r: ResultadoBusca):
        nivel, nota = relevancia(afirmacao, r.titulo, r.resumo)
        return (NIVEIS[nivel], nota, casa_dominio(r.url, DOMINIOS_INSTITUCIONAIS))

    return checagens + sorted(web, key=chave, reverse=True)


def buscar_evidencias(
    afirmacao: str,
    *,
    max_resultados: int = 6,
    max_checagens: int = 3,
    provedor: ProvedorBusca | None = None,
    leitor: LeitorPagina | None = None,
    excluir_url: str | None = None,
    diagnostico: dict | None = None,
) -> list[Evidencia]:
    """Busca evidências para `afirmacao`: até `max_checagens` checagens publicadas, depois a web geral.

    Faz duas consultas (`"<afirmação> checagem de fatos"` e a afirmação pura), junta os resultados,
    lê no máximo `max_resultados` páginas (uma por origem) e extrai de cada uma um trecho literal.
    Nenhum resultado de agência de checagem é descartado por relevância: se a página não puder ser
    baixada, entra com o resumo do buscador (`lida=False`). Falhas do buscador ou de páginas
    individuais ficam em `diagnostico` e não interrompem a busca.
    """
    provedor = provedor or ProvedorDDGS()
    leitor = leitor or ler_pagina
    diag = diagnostico if diagnostico is not None else {}
    diag.update({"consultas": [], "resultados": 0, "paginas_lidas": 0, "checagens": 0,
                 "paginas_ignoradas": [], "erros_busca": []})
    consulta = montar_consulta(afirmacao)
    if not consulta:
        return []

    vistos: set[str] = set()
    resultados: list[ResultadoBusca] = []
    for texto_consulta in (f"{consulta} checagem de fatos", consulta):
        try:
            encontrados = provedor(texto_consulta, RESULTADOS_POR_CONSULTA)
        except Exception as erro:  # buscador externo: qualquer falha vira diagnóstico
            diag["erros_busca"].append(f"{type(erro).__name__}: {str(erro)[:160]}")
            continue
        diag["consultas"].append(texto_consulta)
        diag["resultados"] += len(encontrados)
        for r in encontrados:
            if r.url and r.url not in vistos and r.url != excluir_url:
                vistos.add(r.url)
                resultados.append(r)

    candidatos: list[ResultadoBusca] = []
    grupos: set[str] = set()
    n_checagens = 0
    for r in _ordenar(afirmacao, resultados):
        grupo = grupo_origem(r.url)
        e_checagem = agencia_de(r.url) is not None
        if grupo in grupos or (e_checagem and n_checagens >= max_checagens):
            continue
        grupos.add(grupo)
        n_checagens += e_checagem
        candidatos.append(r)
    # Margem de leitura: páginas da web geral podem falhar; as primeiras que funcionarem ficam.
    candidatos = candidatos[: max_resultados * 2]

    def ler(r: ResultadoBusca) -> Pagina | Exception:
        if casa_dominio(r.url, DOMINIOS_SEM_COLETA):
            return ValueError("Coleta automatizada não habilitada para este publicador")
        try:
            return leitor(r.url)
        except Exception as erro:  # sites externos falham de formas variadas
            return erro

    with ThreadPoolExecutor(max_workers=4) as executor:
        paginas = list(executor.map(ler, candidatos))

    evidencias: list[Evidencia] = []
    for r, pagina in zip(candidatos, paginas):
        if len(evidencias) >= max_resultados:
            break
        agencia = agencia_de(r.url)
        if isinstance(pagina, Exception):
            diag["paginas_ignoradas"].append(
                {"url": r.url, "motivo": f"{type(pagina).__name__}: {str(pagina)[:100]}"})
            if agencia is None or not r.resumo.strip():
                continue
            titulo, trecho, texto, data, lida, url = r.titulo, r.resumo.strip(), "", "", False, r.url
        else:
            titulo = pagina.titulo or r.titulo
            texto, data, lida, url = pagina.texto, pagina.data, True, pagina.url
            trecho = (selecionar_trechos(afirmacao, texto, limite=1) or [texto[:700]])[0]
            diag["paginas_lidas"] += 1
        nivel, _ = relevancia(afirmacao, titulo, trecho if not texto else texto[:2000])
        evidencias.append(Evidencia(
            url=url, titulo=titulo, dominio=_host(url).removeprefix("www."), trecho=trecho, data=data,
            tipo="checagem" if agencia else "web", agencia=agencia, relevancia=nivel,
            lida=lida, texto=texto,
        ))
    diag["checagens"] = sum(e.tipo == "checagem" for e in evidencias)
    return evidencias

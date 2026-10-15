"""Coleta RSS: parsing e limite de coleta de páginas."""

from datetime import date

from app.ingestao import rss


XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Checagem nova</title><link>https://www.boatos.org/brasil/checagem.html</link>
<description><![CDATA[<p>Texto do feed com a explicação.</p>]]></description>
<pubDate>Thu, 15 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>Link externo</title><link>https://example.org/abc</link>
<description>Não entra.</description></item>
</channel></rss>""".encode("utf-8")


def test_ler_feed_filtra_dominio_e_preserva_data():
    itens = rss.ler_feed(XML, ["boatos.org"])
    assert len(itens) == 1
    assert itens[0].titulo == "Checagem nova"
    assert itens[0].texto_feed == "Texto do feed com a explicação."
    assert itens[0].publicado_em == date(2026, 10, 15)


def test_dominio_sem_coleta_usa_apenas_feed(monkeypatch):
    monkeypatch.setattr(rss, "ler_pagina", lambda url: (_ for _ in ()).throw(AssertionError("baixou página")))
    item = rss.Item("https://www.agencialupa.org/jornalismo/checagem/", "Título",
                    "Texto fornecido pelo feed.", date(2026, 10, 15))
    doc = rss.montar_documento(item, "Agência Lupa", pausa_s=0)
    assert doc.origem == "rss_feed" and doc.texto == item.texto_feed

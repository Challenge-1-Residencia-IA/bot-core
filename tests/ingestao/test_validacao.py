from datetime import date

import pytest

from app.ingestao.faketrue import natureza_de, titulo_do_endereco
from app.ingestao.validacao import motivo_para_recusar, sinais_suspeitos, slug
from app.rag.busca import agencia_de

LUPA_ANTIGA = "https://piaui.folha.uol.com.br/lupa/2021/02/12/verificamos-whatsapp-atualizacao/amp/"
ALBUM = ("https://www.uol.com.br/universa/album/2013/11/09/"
         "conheca-os-lancamentos-de-cosmeticos-maquiagens-e-esmaltes-em-novembro.amp.htm")
MESSI = "https://www.torcedores.com/noticias/2017/06/messi-ri-de-fatura-de-r-137-mil-em-balada-com-suarez"


def test_lupa_no_dominio_antigo_e_checagem_com_titulo_do_endereco():
    assert agencia_de(LUPA_ANTIGA) == "Agência Lupa"
    assert agencia_de("https://piaui.uol.com.br/lupa/2020/01/14/verificamos-cedae/") == "Agência Lupa"
    assert agencia_de("https://piaui.folha.uol.com.br/materia/outra-coisa/") is None
    assert natureza_de(LUPA_ANTIGA, "texto qualquer") == "checagem"
    assert titulo_do_endereco(LUPA_ANTIGA) == "Verificamos whatsapp atualizacao"
    assert slug(ALBUM) == "conheca os lancamentos de cosmeticos maquiagens e esmaltes em novembro"


@pytest.mark.parametrize("pedida,final,recusa", [
    (MESSI, "https://www.torcedores.com/", True),
    (ALBUM, ALBUM.replace(".amp.htm", ".htm"), True),
    (LUPA_ANTIGA, "https://piaui.uol.com.br/lupa/2021/02/12/verificamos-whatsapp-atualizacao/amp/", False),
    ("https://g1.globo.com/fato-ou-fake/noticia/2022/08/25/e-fake.ghtml",
     "https://g1.globo.com/fato-ou-fake/noticia/2022/08/25/e-fake.ghtml", False),
])
def test_recusa_pagina_inicial_e_galeria(pedida, final, recusa):
    assert (motivo_para_recusar(pedida, final) is not None) is recusa


def test_sinais_suspeitos():
    texto_menu = "Home UOL Produtos " + " ".join(f"matéria de 15/10/2026 sobre {i}" for i in range(12))
    assert any("anos muito posteriores" in s for s in sinais_suspeitos(
        ALBUM, "Conheça os lançamentos de cosméticos", texto_menu, date(2013, 11, 9)))
    assert any("sem relação" in s for s in sinais_suspeitos(
        MESSI, "Torcedores | Notícias esportivas", "Rossi volta a enfrentar o Estudiantes", None))
    assert sinais_suspeitos(MESSI, "Messi ri de fatura de R$ 137 mil em balada com Suárez",
                            "Lionel Messi se manifestou sobre a fatura da balada em Ibiza.", date(2017, 6, 1)) == []


def test_galeria_com_texto_do_corpus_nao_e_suspeita():
    texto = "As marcas de cosméticos se preparam para o verão com lançamentos de maquiagens e esmaltes."
    assert sinais_suspeitos(ALBUM, "Conheca os lancamentos", texto, None) == ["galeria"]
    assert sinais_suspeitos(ALBUM, "Conheca os lancamentos", texto, None, texto_da_pagina=False) == []

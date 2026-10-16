"""Partes da busca híbrida que não dependem do banco."""

from datetime import date

from app.rag.base import Acerto, consulta_textual, para_evidencia, rrf


def test_rrf_premia_quem_aparece_nas_duas_listas():
    pontos = rrf([["a", "b", "c"], ["c", "d"]], k=60)
    assert max(pontos, key=pontos.get) == "c"
    assert pontos["a"] > pontos["b"] == pontos["d"]  # mesma posição, mesma pontuação
    assert pontos["c"] == 1 / 63 + 1 / 61


def test_consulta_textual_usa_ou_mantem_acento_e_ignora_stopwords():
    assert consulta_textual("Vacinação causa paralisia em idosos, diz isso") == "vacinação | causa | paralisia | idosos"


def test_consulta_textual_so_com_palavras_e_sem_repetir():
    assert consulta_textual("pix & 'taxação' !!! (taxação) | 2025 :*") == "taxação"
    assert consulta_textual("oi, é?") is None


def test_para_evidencia_usa_os_trechos_mais_parecidos_na_ordem_do_texto():
    acerto = Acerto(documento_id=1, url="https://www.boatos.org/x.html", titulo="Vacina #boato", agencia="Boatos.org",
                    dominio="boatos.org", natureza="checagem", publicado_em=date(2022, 3, 4), similaridade=0.91234,
                    pontuacao=0.03, via="boato",
                    trechos=[(0, "Introdução da vacina.", 0.80), (1, "A vacina não causa paralisia.", 0.95),
                             (2, "Outro trecho sobre vacina.", 0.85), (3, "Rodapé.", 0.70)])
    evidencia = para_evidencia(acerto, "vacina causa paralisia")
    assert evidencia.trecho == "A vacina não causa paralisia."
    assert evidencia.texto.split("\n\n") == ["Introdução da vacina.", "A vacina não causa paralisia.",
                                             "Outro trecho sobre vacina."]
    assert (evidencia.tipo, evidencia.fonte_busca, evidencia.data, evidencia.similaridade) == \
        ("checagem", "base", "2022-03-04", 0.9123)

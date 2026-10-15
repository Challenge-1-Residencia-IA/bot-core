import pytest

from app.rag.fatiamento import fatiar


def _frase(n: int, palavra: str = "palavra") -> str:
    return " ".join([palavra] * (n - 1)) + " fim."


def test_texto_curto_vira_um_trecho():
    assert fatiar("Uma frase só. Outra frase.") == ["Uma frase só. Outra frase."]


def test_trechos_sao_fatias_literais_e_respeitam_o_limite():
    texto = " ".join(_frase(30, f"p{i}") for i in range(20))
    trechos = fatiar(texto, max_palavras=100, sobreposicao=30)
    assert len(trechos) > 1
    for trecho in trechos:
        assert trecho in texto
        assert len(trecho.split()) <= 100


def test_trechos_vizinhos_se_sobrepoem_e_cobrem_o_texto():
    texto = " ".join(_frase(30, f"p{i}") for i in range(10))
    trechos = fatiar(texto, max_palavras=100, sobreposicao=30)
    for anterior, seguinte in zip(trechos, trechos[1:]):
        ultima_frase = anterior.rsplit(". ", 1)[-1]
        assert ultima_frase in seguinte
    assert trechos[0].startswith("p0") and trechos[-1].endswith(_frase(30, "p9"))


def test_texto_sem_pontuacao_e_dividido_por_palavras():
    texto = " ".join(f"w{i}" for i in range(500))
    trechos = fatiar(texto, max_palavras=180, sobreposicao=40)
    assert all(len(t.split()) <= 180 for t in trechos)
    assert {p for t in trechos for p in t.split()} == set(texto.split())


def test_texto_vazio_e_parametros_invalidos():
    assert fatiar("   ") == []
    with pytest.raises(ValueError):
        fatiar("x", max_palavras=10, sobreposicao=10)

from app.rag.trechos import e_literal, frases, relevancia, selecionar_trechos, termos

from tests.rag.conftest import TEXTO_CHECAGEM


def test_trecho_e_fatia_literal_do_texto():
    texto = "Primeira frase sobre futebol.  Segunda frase:\n a vacina contra a gripe não causa paralisia. Terceira."
    trechos = selecionar_trechos("vacina da gripe causa paralisia", texto, limite=2, max_palavras=12)
    assert trechos
    for trecho in trechos:
        assert trecho in texto
    assert "paralisia" in trechos[0]


def test_trechos_nao_se_sobrepoem_e_respeitam_limite():
    trechos = selecionar_trechos("vacina gripe paralisia", TEXTO_CHECAGEM, limite=3, max_palavras=20)
    assert 1 <= len(trechos) <= 3
    posicoes = sorted((TEXTO_CHECAGEM.index(t), TEXTO_CHECAGEM.index(t) + len(t)) for t in trechos)
    for (_, fim), (inicio, _) in zip(posicoes, posicoes[1:]):
        assert fim <= inicio


def test_texto_vazio_e_pontuador_injetado():
    assert selecionar_trechos("x", "") == []
    texto = "Alfa beta gama delta. Épsilon zeta eta teta."
    escolhido = selecionar_trechos("qualquer", texto, max_palavras=4,
                                   pontuador=lambda _a, ts: [1.0 if "zeta" in t else 0.0 for t in ts])
    assert escolhido == ["Épsilon zeta eta teta."]


def test_frase_longa_e_cortada_sem_inventar():
    texto = "palavra " * 400
    trecho = selecionar_trechos("palavra", texto, max_caracteres=100)[0]
    assert len(trecho) <= 100 and trecho in texto


def test_frases_e_termos():
    assert len(frases("Um. Dois!\nTrês?")) == 3
    assert termos("A Vacinação é obrigatória") == {"vacinacao", "obrigatoria"}


def test_relevancia_sem_exigir_titulo_identico():
    afirmacao = "vacina da gripe causa paralisia em idosos segundo médicos de Brasília"
    nivel, _ = relevancia(afirmacao, "Checagem: vacina e paralisia", TEXTO_CHECAGEM)
    assert nivel in {"direta", "relacionada"}
    assert relevancia(afirmacao, "Futebol", "Resultado do jogo de ontem")[0] == "nao_relacionada"


def test_e_literal():
    assert e_literal("não há registro de paralisia", TEXTO_CHECAGEM)
    assert e_literal("  NÃO  há registro\nde paralisia ", TEXTO_CHECAGEM)
    assert not e_literal("a vacina é perigosa e mata", TEXTO_CHECAGEM)
    assert not e_literal("vacina", TEXTO_CHECAGEM)

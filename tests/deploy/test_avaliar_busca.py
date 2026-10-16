"""Cálculo das métricas do portão do deploy (deploy/avaliar_busca.py), com uma busca falsa."""

from types import SimpleNamespace

import pytest

from deploy import avaliar_busca

VACINA, PIX, NOTICIA = "https://boatos.org/vacina.html", "https://boatos.org/pix.html", "https://g1.globo.com/vacina"


class BuscaFalsa:
    """Devolve resultados fixos por afirmação; com `excluir_urls`, tira a URL escondida."""

    def __init__(self, resultados):
        self.resultados = resultados
        self.chamadas = []

    def buscar(self, afirmacao, mensagem=None, *, limite=5, excluir_urls=()):
        self.chamadas.append((afirmacao, mensagem, tuple(excluir_urls)))
        urls = [u for u in self.resultados[afirmacao] if u not in excluir_urls][:limite]
        return [SimpleNamespace(url=u) for u in urls]


def item(item_id, afirmacao, esperada):
    return {"id": item_id, "texto": f"mensagem {item_id}", "fonte_esperada": esperada,
            "afirmacoes": [{"texto": "Compartilhem!", "natureza": "opinião"},
                           {"texto": afirmacao, "natureza": "fato verificável"}]}


def test_metricas(monkeypatch):
    monkeypatch.setattr(avaliar_busca, "no_holdout", lambda item_id: item_id in ("GS1", "GS2"))
    busca = BuscaFalsa({
        "vacina causa paralisia": [VACINA + "/", NOTICIA],  # barra no fim não importa
        "pix vai ser taxado": [NOTICIA, PIX],               # esperada em 2º lugar
        "chip rastreia": [],                                 # nada encontrado
    })
    itens = [item("GS1", "vacina causa paralisia", VACINA), item("GS2", "pix vai ser taxado", PIX),
             item("GS3", "chip rastreia", "https://nao.esta/na-base")]
    metricas = avaliar_busca.avaliar(itens, busca)
    assert metricas["itens"] == 3
    assert metricas["top1"] == pytest.approx(1 / 3, abs=1e-3)
    assert metricas["top5"] == pytest.approx(2 / 3, abs=1e-3)
    # Holdout (GS1 e GS2): com a esperada escondida, os dois ainda devolvem a notícia.
    assert metricas["ruido_fora_da_base"] == 1.0
    assert ("vacina causa paralisia", "mensagem GS1", (VACINA,)) in busca.chamadas


def test_afirmacao_usa_a_primeira_verificavel():
    assert avaliar_busca.afirmacao(item("X", "vacina causa paralisia", VACINA)) == "vacina causa paralisia"


def test_holdout_e_deterministico_e_perto_de_20_por_cento():
    ids = [f"GS{i:03d}" for i in range(1, 501)]
    escolhidos = [i for i in ids if avaliar_busca.no_holdout(i)]
    assert escolhidos == [i for i in ids if avaliar_busca.no_holdout(i)]
    assert 0.15 < len(escolhidos) / len(ids) < 0.25

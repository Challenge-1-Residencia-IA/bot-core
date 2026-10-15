import json

from app.ingestao.faketrue import GoldenSet, Par, ler_golden_set


def _par(texto, url="https://www.boatos.org/outro.html"):
    return Par(url_boato=url, texto_boato=texto, titulo_boato="", url_checagem="https://g1.globo.com/x",
               texto_checagem="")


def test_reconhece_variacoes_do_mesmo_boato(tmp_path):
    arquivo = tmp_path / "golden.jsonl"
    arquivo.write_text(json.dumps({
        "texto": "IMPORTANTE!! Divulgue antes que o governo tire essa informação do ar, o laboratório...",
        "fonte_esperada": "https://www.boatos.org/gs.html",
        "afirmacoes": [{"texto": "O laboratório de Wuhan pertence a uma empresa americana"}, {"texto": "curta"}],
    }) + "\n", encoding="utf-8")
    golden = ler_golden_set(arquivo)

    assert golden.contem(_par("qualquer texto", url="https://www.boatos.org/gs.html"))
    assert golden.contem(_par("versão 2: importante divulgue antes que o governo tire essa informação do ar!!"))
    assert golden.contem(_par("Dizem que o laboratório de wuhan pertence a uma empresa americana."))
    assert not golden.contem(_par("A vacina da gripe causa paralisia, compartilhe."))
    assert "curta" not in golden.trechos


def test_golden_set_vazio_nao_exclui_nada():
    assert not GoldenSet().contem(_par("texto"))

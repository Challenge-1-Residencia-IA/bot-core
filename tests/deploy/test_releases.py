"""Registro de releases do deploy (deploy/releases.py), num diretório temporário."""

import importlib.util
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture
def releases(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DEPLOY_BASE", str(tmp_path))
    spec = importlib.util.spec_from_file_location("releases", RAIZ / "deploy" / "releases.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    (tmp_path / "bot-core-releases").mkdir()
    return modulo


def no_disco(modulo, *releases):
    for release in releases:
        (modulo.RELEASES / release).mkdir()


def colocar_no_ar(modulo, release):
    modulo.ATUAL.unlink(missing_ok=True)
    modulo.ATUAL.symlink_to(modulo.RELEASES / release)


def test_anterior_e_a_ultima_release_no_ar_antes_da_atual(releases):
    no_disco(releases, "aaaaaaa", "bbbbbbb", "ccccccc")
    releases.registrar("aaaaaaa" + "0" * 33, "implantada")
    releases.registrar("bbbbbbb" + "0" * 33, "implantada", anterior="aaaaaaa")
    releases.registrar("ccccccc" + "0" * 33, "abortada", anterior="bbbbbbb")  # nunca esteve no ar
    colocar_no_ar(releases, "bbbbbbb")
    assert releases.anterior() == "aaaaaaa"


def test_anterior_ignora_release_apagada_do_disco(releases):
    no_disco(releases, "bbbbbbb", "ccccccc")
    for release in ("aaaaaaa", "bbbbbbb", "ccccccc"):
        releases.registrar(release + "0" * 33, "implantada")
    colocar_no_ar(releases, "ccccccc")
    assert releases.anterior() == "bbbbbbb"
    (releases.RELEASES / "bbbbbbb").rmdir()
    assert releases.anterior() == ""


def test_reversao_manual_conta_como_release_no_ar(releases):
    no_disco(releases, "aaaaaaa", "bbbbbbb")
    releases.registrar("aaaaaaa" + "0" * 33, "implantada")
    releases.registrar("bbbbbbb" + "0" * 33, "implantada")
    releases.registrar("aaaaaaa" + "0" * 33, "reversao_manual", anterior="bbbbbbb")
    colocar_no_ar(releases, "aaaaaaa")
    assert releases.anterior() == "bbbbbbb"


def test_listar_marca_a_release_no_ar_e_mostra_metricas(releases):
    no_disco(releases, "aaaaaaa")
    releases.registrar("aaaaaaa" + "0" * 33, "implantada", detalhe="ok", metricas={"top1": 0.85})
    colocar_no_ar(releases, "aaaaaaa")
    texto = releases.listar(10)
    assert "aaaaaaa" in texto and "85%" in texto and "← no ar" in texto


def test_status_invalido_e_recusado(releases):
    with pytest.raises(ValueError):
        releases.registrar("a" * 40, "qualquer")


def test_anterior_inclui_release_colocada_no_ar_antes_do_registro(releases):
    no_disco(releases, "aaaaaaa", "bbbbbbb")
    releases.registrar("bbbbbbb" + "0" * 33, "implantada", anterior="aaaaaaa")
    colocar_no_ar(releases, "bbbbbbb")
    assert releases.anterior() == "aaaaaaa"


# Portão champion/challenger ---------------------------------------------------------------------------

def implantar(modulo, release, top5, campea=False):
    (modulo.RELEASES / release).mkdir(exist_ok=True)
    modulo.registrar(release + "0" * 33, "implantada", metricas={"top1": top5 - 0.1, "top5": top5}, campea=campea)


def test_portao_sem_campea_aprova_e_promove_acima_do_piso(releases):
    decisao = releases.portao({"top5": 0.89}, piso=0.85, margem=0.03)
    assert decisao["aprovada"] and decisao["promover"] and decisao["campea"] is None


def test_portao_usa_top5_e_nao_barra_por_top1(releases):
    decisao = releases.portao({"top1": 0.70, "top5": 0.89}, piso=0.85, margem=0.03)
    assert decisao["aprovada"] and decisao["metrica"] == "top5"


def test_portao_barra_abaixo_do_piso(releases):
    decisao = releases.portao({"top5": 0.84}, piso=0.85, margem=0.03)
    assert not decisao["aprovada"] and "piso" in decisao["motivo"]


def test_portao_aprova_dentro_da_margem_sem_promover(releases):
    implantar(releases, "aaaaaaa", 0.90, campea=True)
    decisao = releases.portao({"top5": 0.88}, piso=0.85, margem=0.03)
    assert decisao["aprovada"] and not decisao["promover"] and decisao["campea"] == "aaaaaaa"


def test_portao_barra_mais_de_3_pontos_abaixo_da_campea(releases):
    implantar(releases, "aaaaaaa", 0.95, campea=True)
    decisao = releases.portao({"top5": 0.90}, piso=0.85, margem=0.03)
    assert not decisao["aprovada"] and "campeã aaaaaaa" in decisao["motivo"]


def test_portao_promove_empate_ou_melhora(releases):
    implantar(releases, "aaaaaaa", 0.89, campea=True)
    assert releases.portao({"top5": 0.89}, piso=0.85, margem=0.03)["promover"]


def test_portao_compara_com_a_campea_reavaliada_no_mesmo_banco(releases):
    # A nota registrada da campeã (95%) é de uma base antiga; reavaliada hoje, ela tem 88%.
    implantar(releases, "aaaaaaa", 0.95, campea=True)
    decisao = releases.portao({"top5": 0.89}, piso=0.85, margem=0.03, metricas_campea={"top5": 0.88})
    assert decisao["aprovada"] and decisao["promover"] and decisao["origem_nota_campea"] == "reavaliada agora"


def test_portao_barra_avaliacao_sem_a_metrica(releases):
    assert not releases.portao({"top1": 0.9}, piso=0.85, margem=0.03)["aprovada"]


def test_campea_ignora_release_apagada_e_usa_a_anterior(releases):
    implantar(releases, "aaaaaaa", 0.84, campea=True)
    implantar(releases, "bbbbbbb", 0.86, campea=True)
    implantar(releases, "ccccccc", 0.85)
    assert releases.campea()["release"] == "bbbbbbb"
    (releases.RELEASES / "bbbbbbb").rmdir()
    assert releases.campea()["release"] == "aaaaaaa"


def test_listar_marca_no_ar_uma_vez_e_a_campea(releases):
    implantar(releases, "aaaaaaa", 0.85, campea=True)
    implantar(releases, "bbbbbbb", 0.84)
    releases.registrar("aaaaaaa" + "0" * 33, "reversao_manual", anterior="bbbbbbb")
    colocar_no_ar(releases, "aaaaaaa")
    texto = releases.listar(10)
    assert texto.count("← no ar") == 1 and texto.count("★ campeã") == 1
    linha_no_ar = next(linha for linha in texto.splitlines() if "← no ar" in linha)
    assert "reversao_manual" in linha_no_ar

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

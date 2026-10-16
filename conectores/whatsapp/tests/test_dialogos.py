import asyncio

from conectores.whatsapp.src.utils import playwright_utils


class _Botao:
    def __init__(self, pagina, existe):
        self.pagina, self.existe = pagina, existe
        self.first = self

    async def count(self):
        return 1 if self.existe else 0

    async def click(self, timeout=None):
        self.pagina.dialogos = 0
        self.pagina.cliques += 1


class _Dialogos:
    def __init__(self, pagina):
        self.pagina = pagina
        self.first = self

    async def count(self):
        return self.pagina.dialogos

    def get_by_role(self, papel, name, exact):
        return _Botao(self.pagina, name == "Fechar")


class _Teclado:
    def __init__(self, pagina):
        self.pagina = pagina

    async def press(self, tecla):
        self.pagina.teclas.append(tecla)
        if self.pagina.escape_fecha:
            self.pagina.dialogos = 0


class PaginaFalsa:
    def __init__(self, dialogos, escape_fecha=True):
        self.dialogos, self.escape_fecha = dialogos, escape_fecha
        self.teclas, self.cliques = [], 0
        self.keyboard = _Teclado(self)

    def locator(self, seletor):
        assert seletor == '[role="dialog"]'
        return _Dialogos(self)


def test_sem_dialogo_nao_faz_nada():
    pagina = PaginaFalsa(0)
    assert asyncio.run(playwright_utils.fechar_dialogos(pagina)) == 0
    assert pagina.teclas == []


def test_escape_fecha_novidades():
    pagina = PaginaFalsa(1)
    assert asyncio.run(playwright_utils.fechar_dialogos(pagina)) == 1
    assert pagina.teclas == ["Escape"] and pagina.dialogos == 0 and pagina.cliques == 0


def test_clica_em_fechar_quando_escape_nao_basta():
    pagina = PaginaFalsa(1, escape_fecha=False)
    asyncio.run(playwright_utils.fechar_dialogos(pagina))
    assert pagina.cliques == 1 and pagina.dialogos == 0

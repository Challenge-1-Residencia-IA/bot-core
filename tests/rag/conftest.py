import pytest

from app.rag.busca import Pagina, ResultadoBusca

TEXTO_CHECAGEM = (
    "Circula no WhatsApp uma mensagem que diz que a vacina contra a gripe causa paralisia. "
    "O Ministério da Saúde informou que não há registro de paralisia associada à vacina contra a gripe. "
    "Especialistas ouvidos pela reportagem explicam que eventos adversos graves são raros. "
    "A mensagem pede que as pessoas compartilhem o alerta com todos os contatos."
)
TEXTO_WEB = (
    "A campanha nacional de vacinação contra a gripe começou nesta segunda-feira em todo o país. "
    "Postos de saúde atendem idosos, gestantes e crianças. "
    "Segundo a secretaria, a meta é vacinar 90% do público prioritário."
)


class ProvedorFalso:
    def __init__(self, resultados_por_consulta=None, erro=None):
        self.resultados = resultados_por_consulta or {}
        self.erro = erro
        self.consultas = []

    def __call__(self, consulta, max_resultados):
        self.consultas.append(consulta)
        if self.erro:
            raise self.erro
        for chave, valor in self.resultados.items():
            if chave in consulta:
                return list(valor)
        return []


class LeitorFalso:
    def __init__(self, paginas):
        self.paginas = paginas
        self.lidas = []

    def __call__(self, url):
        self.lidas.append(url)
        pagina = self.paginas.get(url)
        if pagina is None:
            raise ValueError("falha simulada")
        return pagina


@pytest.fixture
def resultados():
    return [
        ResultadoBusca("Vacina da gripe começa hoje", "https://www.exemplo.com.br/saude/campanha", "Campanha de vacinação"),
        ResultadoBusca("Vacina da gripe não causa paralisia", "https://www.boatos.org/saude/vacina-gripe-paralisia.html", "Boato antigo"),
        ResultadoBusca("Aos Fatos: vacina e paralisia", "https://www.aosfatos.org/noticias/vacina-gripe", "É falso que a vacina da gripe cause paralisia, diz o Aos Fatos."),
        ResultadoBusca("Outra página do mesmo site", "https://outra.exemplo.com.br/x", "Mesmo grupo de origem"),
    ]


@pytest.fixture
def paginas():
    return {
        "https://www.boatos.org/saude/vacina-gripe-paralisia.html": Pagina(
            url="https://www.boatos.org/saude/vacina-gripe-paralisia.html",
            titulo="Vacina da gripe causa paralisia #boato", texto=TEXTO_CHECAGEM, data="2024-04-02"),
        "https://www.exemplo.com.br/saude/campanha": Pagina(
            url="https://www.exemplo.com.br/saude/campanha", titulo="Campanha de vacinação começa",
            texto=TEXTO_WEB),
    }

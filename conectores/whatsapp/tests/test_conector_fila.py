"""Testes do fluxo scanner -> núcleo (ARQ) -> dispatcher, com fakes.

Rodar a partir da raiz do bot-core: ``python -m pytest conectores/whatsapp/tests``.
"""
import asyncio

import pytest

from app.contrato import FILA, JOB_ANALISAR, ResultadoAnalise
from conectores.whatsapp.src import dispatcher, scanner
from conectores.whatsapp.src.config import CONFIG, MENSAGEM_INDISPONIVEL
from conectores.whatsapp.src.delivery_store import DeliveryStore
from conectores.whatsapp.src.models import MensagemTarefa, RespostaTarefa


class JobFalso:
    def __init__(self, resultado=None, erro=None, atraso=0.0):
        self.resultado = resultado
        self.erro = erro
        self.atraso = atraso
        self.timeout_recebido = None

    async def result(self, timeout=None):
        self.timeout_recebido = timeout
        if self.atraso:
            await asyncio.wait_for(asyncio.sleep(self.atraso), timeout)
        if self.erro:
            raise self.erro
        return self.resultado


class PoolFalso:
    def __init__(self, job):
        self.job = job
        self.chamadas = []

    async def enqueue_job(self, funcao, *args, **kwargs):
        self.chamadas.append((funcao, args, kwargs))
        return self.job


def _tarefa():
    return MensagemTarefa(
        chat_nome="Maria",
        mensagem_id="false_5511999@c.us_ABC",
        texto="Vacina X causa Y?",
        metadata="[10:00, 14/10/2026] Maria: ",
        contexto=[{"role": "user", "content": "Vacina X causa Y?"}],
    )


def test_enfileira_com_nome_fila_e_payload_do_contrato():
    async def cenario():
        fila = asyncio.Queue()
        pool = PoolFalso(JobFalso(resultado=ResultadoAnalise(resposta_texto="ok").para_dict()))
        task = await scanner.enfileirar_analise(pool, _tarefa(), fila, timeout=5)
        await task
        return pool

    pool = asyncio.run(cenario())
    assert len(pool.chamadas) == 1
    funcao, args, kwargs = pool.chamadas[0]
    assert funcao == JOB_ANALISAR == "analisar_mensagem"
    assert kwargs["_queue_name"] == FILA
    assert kwargs["_job_id"] == "whatsapp:false_5511999@c.us_ABC"
    assert args == (
        {
            "chat_id": "Maria",
            "mensagem_id": "false_5511999@c.us_ABC",
            "texto": "Vacina X causa Y?",
            "contexto": [{"role": "user", "content": "Vacina X causa Y?"}],
        },
    )


def test_resultado_vai_para_fila_do_dispatcher():
    async def cenario():
        fila = asyncio.Queue()
        job = JobFalso(resultado=ResultadoAnalise(resposta_texto="Veja as fontes...", recusada=False).para_dict())
        await scanner.enfileirar_analise(PoolFalso(job), _tarefa(), fila, timeout=7)
        resposta = await asyncio.wait_for(fila.get(), 1)
        return resposta, job

    resposta, job = asyncio.run(cenario())
    assert resposta == RespostaTarefa(
        chat_nome="Maria",
        mensagem_id="false_5511999@c.us_ABC",
        resposta_texto="Veja as fontes...",
        falha=False,
    )
    assert job.timeout_recebido == 7


def test_enfileirar_nao_bloqueia_enquanto_o_nucleo_processa():
    async def cenario():
        fila = asyncio.Queue()
        job = JobFalso(resultado={"resposta_texto": "tarde"}, atraso=0.2)
        task = await asyncio.wait_for(scanner.enfileirar_analise(PoolFalso(job), _tarefa(), fila, timeout=5), 0.05)
        vazia_logo_apos = fila.empty()
        await task
        return vazia_logo_apos, fila.qsize()

    vazia_logo_apos, tamanho_final = asyncio.run(cenario())
    assert vazia_logo_apos
    assert tamanho_final == 1


@pytest.mark.parametrize(
    "job",
    [
        JobFalso(resultado={"resposta_texto": "x"}, atraso=1.0),  # timeout real
        JobFalso(erro=asyncio.TimeoutError()),
        JobFalso(erro=ConnectionError("redis caiu")),
        JobFalso(resultado={"resposta_texto": "  "}),
    ],
    ids=["timeout", "timeout-arq", "erro", "resposta-vazia"],
)
def test_timeout_ou_erro_gera_mensagem_de_indisponibilidade(job):
    async def cenario():
        fila = asyncio.Queue()
        await scanner.aguardar_resultado(job, _tarefa(), fila, timeout=0.05)
        return await asyncio.wait_for(fila.get(), 1)

    resposta = asyncio.run(cenario())
    assert resposta.falha is True
    assert resposta.resposta_texto == MENSAGEM_INDISPONIVEL
    assert resposta.chat_nome == "Maria"


def test_job_duplicado_nao_cria_tarefa():
    async def cenario():
        return await scanner.enfileirar_analise(PoolFalso(None), _tarefa(), asyncio.Queue(), timeout=1)

    assert asyncio.run(cenario()) is None


class _Chamadas:
    def __init__(self):
        self.enviadas = []
        self.fechou = 0


@pytest.fixture
def pagina_falsa(monkeypatch):
    chamadas = _Chamadas()

    async def enviar_mensagem(page, texto, *_args):
        chamadas.enviadas.append(texto)

    async def fechar_chat_ativo(page):
        chamadas.fechou += 1

    async def obter_nome_chat_ativo(page):
        return "Maria"

    async def abrir_chat_por_nome(page, nome):
        return True

    monkeypatch.setattr(dispatcher, "enviar_mensagem", enviar_mensagem)
    monkeypatch.setattr(dispatcher, "obter_nome_chat_ativo", obter_nome_chat_ativo)
    monkeypatch.setattr(dispatcher, "abrir_chat_por_nome", abrir_chat_por_nome)
    monkeypatch.setitem(CONFIG, "contatos_permitidos", ["Maria"])
    return chamadas


@pytest.fixture
def store(tmp_path):
    registro = DeliveryStore(tmp_path / "deliveries.db")
    yield registro
    registro.close()


def _rodar_dispatcher(resposta, store):
    async def cenario():
        fila = asyncio.Queue()
        await fila.put(resposta)
        loop_task = asyncio.create_task(dispatcher.dispatcher_loop(object(), asyncio.Lock(), fila, store))
        await asyncio.wait_for(fila.join(), 1)
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)

    asyncio.run(cenario())


def test_dispatcher_em_dry_run_nao_envia(monkeypatch, pagina_falsa, store):
    monkeypatch.setitem(CONFIG, "mode_dry_run", True)
    store.claim("k1", "ready")
    _rodar_dispatcher(RespostaTarefa("Maria", "m1", "resposta", chave="k1"), store)
    assert pagina_falsa.enviadas == []
    assert store.get("k1") == "dry_run"


def test_dispatcher_fora_do_dry_run_envia(monkeypatch, pagina_falsa, store):
    monkeypatch.setitem(CONFIG, "mode_dry_run", False)
    store.claim("k1", "ready")
    _rodar_dispatcher(RespostaTarefa("Maria", "m1", "resposta", chave="k1"), store)
    assert pagina_falsa.enviadas == ["resposta"]
    assert store.get("k1") == "sent"


def test_dry_run_e_padrao():
    import yaml
    from conectores.whatsapp.src.config import CONFIG_PATH

    with CONFIG_PATH.open(encoding="utf-8") as f:
        assert yaml.safe_load(f)["mode_dry_run"] is True

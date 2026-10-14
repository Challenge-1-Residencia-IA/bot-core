"""Testes das proteções do conector: registro de entregas, allowlist, baseline e mídia.

Rodar a partir da raiz do bot-core: ``python -m pytest conectores/whatsapp/tests``.
"""
import asyncio

import pytest

from app.contrato import JOB_ANALISAR
from conectores.whatsapp.src import dispatcher, scanner
from conectores.whatsapp.src.config import CONFIG, MENSAGEM_MIDIA, carregar_contatos
from conectores.whatsapp.src.delivery_store import DeliveryStore, message_key
from conectores.whatsapp.src.models import RespostaTarefa


@pytest.fixture
def store(tmp_path):
    registro = DeliveryStore(tmp_path / "deliveries.db")
    yield registro
    registro.close()


# ---------------------------------------------------------------------------
# Registro de entregas
# ---------------------------------------------------------------------------
def test_chave_nao_contem_nome_nem_id():
    chave = message_key("Maria", "msg-1")
    assert len(chave) == 64 and "Maria" not in chave and "msg-1" not in chave
    assert chave != message_key("Mari", "amsg-1")  # o separador \0 evita colisão por concatenação


def test_claim_e_idempotente(store):
    assert store.claim("k") is True
    assert store.claim("k") is False
    assert store.claim("k", "baseline") is False
    assert store.get("k") == "queued"


def test_estado_invalido_e_recusado(store):
    store.claim("k")
    with pytest.raises(ValueError):
        store.state("k", "enviado")


def test_reserve_send_so_de_ready_e_uma_vez(store):
    store.claim("k")
    assert store.reserve_send("k") is False  # ainda queued
    store.state("k", "ready")
    assert store.reserve_send("k") is True
    assert store.get("k") == "sending"
    assert store.reserve_send("k") is False


def test_reinicio_marca_intermediarios_como_interrupted(tmp_path):
    caminho = tmp_path / "deliveries.db"
    registro = DeliveryStore(caminho)
    for chave, estado in [("q", "queued"), ("a", "analyzing"), ("r", "ready"), ("s", "sending"),
                          ("b", "baseline"), ("ok", "sent"), ("d", "dry_run"), ("u", "uncertain")]:
        registro.claim(chave, estado)
    registro.close()

    registro = DeliveryStore(caminho)
    try:
        assert registro.interrompidas_no_inicio == 4
        for chave in ("q", "a", "r", "s"):
            assert registro.get(chave) == "interrupted"
            assert registro.reserve_send(chave) is False  # não reenvia
        assert registro.get("b") == "baseline"
        assert registro.get("ok") == "sent"
        assert registro.get("d") == "dry_run"
        assert registro.get("u") == "uncertain"
        assert registro.claim("q") is False  # a mensagem não volta para a fila
    finally:
        registro.close()


def test_lock_de_instancia_unica(tmp_path):
    caminho = tmp_path / "deliveries.db"
    primeiro = DeliveryStore(caminho)
    with pytest.raises(RuntimeError):
        DeliveryStore(caminho)
    primeiro.close()
    segundo = DeliveryStore(caminho)  # liberado após o close
    segundo.close()


def test_registro_nao_guarda_texto_nem_nome(tmp_path):
    caminho = tmp_path / "deliveries.db"
    registro = DeliveryStore(caminho)
    registro.claim(message_key("Contato Secreto", "id-123"))
    registro.close()
    conteudo = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert b"Contato Secreto" not in conteudo and b"id-123" not in conteudo


# ---------------------------------------------------------------------------
# Allowlist (config)
# ---------------------------------------------------------------------------
def test_contatos_env_json_tem_prioridade():
    assert carregar_contatos('["Ana", "Ana", " Bia "]', ["Outro"]) == ["Ana", "Bia"]


def test_contatos_vazio_e_valido_fail_closed():
    assert carregar_contatos(None, []) == []
    assert carregar_contatos(None, None) == []
    assert carregar_contatos("", []) == []


@pytest.mark.parametrize("env, cfg", [("nao-json", None), ('{"a": 1}', None), ('[""]', None), (None, "Ana"), (None, [1])])
def test_contatos_invalidos_sao_recusados(env, cfg):
    with pytest.raises(ValueError):
        carregar_contatos(env, cfg)


def test_config_padrao_nao_atende_ninguem():
    import yaml
    from conectores.whatsapp.src.config import CONFIG_PATH

    with CONFIG_PATH.open(encoding="utf-8") as f:
        assert yaml.safe_load(f)["contatos_permitidos"] == []


# ---------------------------------------------------------------------------
# Dispatcher: allowlist, limite, chat que não abre, falha de envio
# ---------------------------------------------------------------------------
class PaginaFalsa:
    def __init__(self, chat_ativo="Maria", abre=True, falha_envio=False):
        self.chat_ativo = chat_ativo
        self.abre = abre
        self.falha_envio = falha_envio
        self.enviadas = []
        self.aberturas = 0


@pytest.fixture
def pagina(monkeypatch):
    page = PaginaFalsa()

    async def enviar_mensagem(p, texto):
        if p.falha_envio:
            raise RuntimeError("send_unconfirmed")
        p.enviadas.append(texto)

    async def fechar_chat_ativo(p):
        return None

    async def obter_nome_chat_ativo(p):
        return p.chat_ativo

    async def abrir_chat_por_nome(p, nome):
        p.aberturas += 1
        if p.abre:
            p.chat_ativo = nome
        return p.abre

    monkeypatch.setattr(dispatcher, "enviar_mensagem", enviar_mensagem)
    monkeypatch.setattr(dispatcher, "fechar_chat_ativo", fechar_chat_ativo)
    monkeypatch.setattr(dispatcher, "obter_nome_chat_ativo", obter_nome_chat_ativo)
    monkeypatch.setattr(dispatcher, "abrir_chat_por_nome", abrir_chat_por_nome)
    monkeypatch.setitem(CONFIG, "mode_dry_run", False)
    monkeypatch.setitem(CONFIG, "contatos_permitidos", ["Maria", "Joao"])
    monkeypatch.setitem(CONFIG, "limite_resposta_chars", 50)
    return page


def _despachar(page, store, *respostas):
    async def cenario():
        fila = asyncio.Queue()
        for resposta in respostas:
            await fila.put(resposta)
        tarefa = asyncio.create_task(dispatcher.dispatcher_loop(page, asyncio.Lock(), fila, store))
        await asyncio.wait_for(fila.join(), 5)
        tarefa.cancel()
        await asyncio.gather(tarefa, return_exceptions=True)

    asyncio.run(cenario())


def _pronta(store, chat, texto, chave):
    store.claim(chave, "ready")
    return RespostaTarefa(chat, "m", texto, chave=chave)


def test_contato_fora_da_lista_e_bloqueado(pagina, store):
    _despachar(pagina, store, _pronta(store, "Estranho", "oi", "k"))
    assert pagina.enviadas == [] and store.get("k") == "blocked"


def test_resposta_acima_do_limite_e_descartada(pagina, store):
    _despachar(pagina, store, _pronta(store, "Maria", "x" * 51, "k"))
    assert pagina.enviadas == [] and store.get("k") == "blocked"


def test_chat_que_nao_abre_vira_blocked_sem_laco(pagina, store):
    pagina.abre = False
    _despachar(pagina, store, _pronta(store, "Joao", "oi", "k"))
    assert pagina.aberturas == 1
    assert pagina.enviadas == [] and store.get("k") == "blocked"


def test_falha_no_envio_vira_uncertain_sem_reenvio(pagina, store):
    pagina.falha_envio = True
    _despachar(pagina, store, _pronta(store, "Maria", "oi", "k"))
    assert store.get("k") == "uncertain"
    assert store.reserve_send("k") is False


def test_nao_envia_sem_reserva(pagina, store):
    store.claim("k", "interrupted")
    _despachar(pagina, store, RespostaTarefa("Maria", "m", "oi", chave="k"))
    assert pagina.enviadas == [] and store.get("k") == "interrupted"


# ---------------------------------------------------------------------------
# Scanner: baseline, mídia e allowlist
# ---------------------------------------------------------------------------
class JobFalso:
    async def result(self, timeout=None):
        return {"resposta_texto": "análise"}


class PoolFalso:
    def __init__(self):
        self.chamadas = []

    async def enqueue_job(self, funcao, *args, **kwargs):
        self.chamadas.append((funcao, args, kwargs))
        return JobFalso()


@pytest.fixture
def chat_falso(monkeypatch):
    estado = {"mensagens": []}

    async def obter_mensagens_recebidas(page):
        return list(estado["mensagens"])

    async def obter_contexto_conversa(page, limite=15):
        return []

    monkeypatch.setattr(scanner, "obter_mensagens_recebidas", obter_mensagens_recebidas)
    monkeypatch.setattr(scanner, "obter_contexto_conversa", obter_contexto_conversa)
    return estado


def _processar(nome, pool, fila, store, inicializados):
    async def cenario():
        novas = await scanner.processar_chat_aberto(object(), nome, pool, fila, store, CONFIG, inicializados)
        await asyncio.gather(*list(scanner._TAREFAS_PENDENTES))
        return novas

    return asyncio.run(cenario())


def test_baseline_ignora_historico_e_so_analisa_mensagem_nova(chat_falso, store):
    pool, fila, inicializados = PoolFalso(), asyncio.Queue(), set()
    chat_falso["mensagens"] = [{"id": "a", "texto": "antiga 1", "midia": False},
                               {"id": "b", "texto": "antiga 2", "midia": False}]
    assert _processar("Maria", pool, fila, store, inicializados) == 0
    assert pool.chamadas == [] and fila.empty()
    assert store.get(message_key("Maria", "a")) == "baseline"

    chat_falso["mensagens"].append({"id": "c", "texto": "nova", "midia": False})
    assert _processar("Maria", pool, fila, store, inicializados) == 1
    assert len(pool.chamadas) == 1
    funcao, args, _ = pool.chamadas[0]
    assert funcao == JOB_ANALISAR and args[0]["texto"] == "nova" and args[0]["mensagem_id"] == "c"
    resposta = fila.get_nowait()
    assert resposta.resposta_texto == "análise"
    assert resposta.chave == message_key("Maria", "c")
    assert store.get(resposta.chave) == "ready"

    # nova varredura: nada é reenfileirado
    assert _processar("Maria", pool, fila, store, inicializados) == 0
    assert len(pool.chamadas) == 1


def test_midia_sem_texto_responde_no_conector_sem_chamar_o_nucleo(chat_falso, store):
    pool, fila = PoolFalso(), asyncio.Queue()
    chat_falso["mensagens"] = [{"id": "audio", "texto": "", "midia": True},
                               {"id": "figurinha", "texto": "  ", "midia": True}]
    assert _processar("Maria", pool, fila, store, {"Maria"}) == 2
    assert pool.chamadas == []
    respostas = [fila.get_nowait(), fila.get_nowait()]
    assert [r.resposta_texto for r in respostas] == [MENSAGEM_MIDIA, MENSAGEM_MIDIA]
    assert all(store.get(r.chave) == "ready" for r in respostas)


def test_midia_com_legenda_vai_para_o_nucleo(chat_falso, store):
    pool, fila = PoolFalso(), asyncio.Queue()
    chat_falso["mensagens"] = [{"id": "img", "texto": "olha essa notícia", "midia": True}]
    _processar("Maria", pool, fila, store, {"Maria"})
    assert len(pool.chamadas) == 1


class ElementoFalso:
    def __init__(self, nome, cliques):
        self.nome = nome
        self.cliques = cliques

    async def click(self):
        self.cliques.append(self.nome)


def test_scanner_nem_abre_chat_fora_da_lista(monkeypatch, chat_falso, store):
    cliques = []

    async def encontrar_chats_nao_lidos(page):
        return [{"nome": n, "elemento": ElementoFalso(n, cliques)} for n in ("Estranho", "Maria")]

    async def falso(*_args, **_kwargs):
        return False

    async def sem_chat(page):
        return None

    monkeypatch.setattr(scanner, "encontrar_chats_nao_lidos", encontrar_chats_nao_lidos)
    monkeypatch.setattr(scanner, "chat_eh_grupo", falso)
    monkeypatch.setattr(scanner, "abrir_chat_por_nome", falso)
    monkeypatch.setattr(scanner, "obter_nome_chat_ativo", sem_chat)
    config = dict(CONFIG, contatos_permitidos=["Maria"], intervalo_scanner=0.01)

    async def cenario():
        tarefa = asyncio.create_task(
            scanner.scanner_loop(object(), PoolFalso(), asyncio.Lock(), config, asyncio.Queue(), store)
        )
        await asyncio.sleep(0.2)
        tarefa.cancel()
        await asyncio.gather(tarefa, return_exceptions=True)

    asyncio.run(cenario())
    assert "Estranho" not in cliques
    assert "Maria" in cliques


def test_scanner_com_lista_vazia_nao_abre_nada(monkeypatch, chat_falso, store):
    cliques = []

    async def encontrar_chats_nao_lidos(page):
        return [{"nome": "Maria", "elemento": ElementoFalso("Maria", cliques)}]

    async def sem_chat(page):
        return "Maria"

    monkeypatch.setattr(scanner, "encontrar_chats_nao_lidos", encontrar_chats_nao_lidos)
    monkeypatch.setattr(scanner, "obter_nome_chat_ativo", sem_chat)
    config = dict(CONFIG, contatos_permitidos=[], intervalo_scanner=0.01)
    pool = PoolFalso()

    async def cenario():
        tarefa = asyncio.create_task(
            scanner.scanner_loop(object(), pool, asyncio.Lock(), config, asyncio.Queue(), store)
        )
        await asyncio.sleep(0.1)
        tarefa.cancel()
        await asyncio.gather(tarefa, return_exceptions=True)

    asyncio.run(cenario())
    assert cliques == [] and pool.chamadas == []

"""Painel de controle do conector: estado publicado, comandos e bloqueio."""

import asyncio
import json

import pytest

from app.contrato import PAINEL_COMANDOS, PAINEL_ESTADO_CONECTOR, PAINEL_EVENTOS
from conectores.whatsapp.src import painel
from conectores.whatsapp.src.config import CONFIG
from conectores.whatsapp.src.control import pause_event
from conectores.whatsapp.src.delivery_store import message_key
from conectores.whatsapp.tests.test_conector_aberto import (  # noqa: F401 - fixtures
    AgendadorFalso, Relogio, _adicionar, _agendador, _coletar, chat_falso, store)


@pytest.fixture(autouse=True)
def estado_limpo(monkeypatch):
    monkeypatch.setitem(CONFIG, "mode_dry_run", True)
    monkeypatch.setitem(CONFIG, "modo_acesso", "aberto")
    pause_event.clear()
    yield
    pause_event.clear()


def test_instantaneo_mostra_sessoes_espera_e_pendencias_sem_nomes(store):  # noqa: F811
    relogio = Relogio()
    agendador, _, _ = _agendador(1, store, relogio)
    asyncio.run(_adicionar(agendador, store, "Zuleide Quintino", "m1"))
    asyncio.run(_adicionar(agendador, store, "+55 11 98888-7777", "m2"))  # vai para a espera (limite 1)
    relogio.agora = 30
    estado = painel.instantaneo(agendador)
    assert estado["limite"] == 1 and estado["simulacao"] is True and estado["pausado"] is False
    assert estado["ativas"] == [{"id": painel.id_chat("Zuleide Quintino"), "inativo_s": 30, "analisando": False,
                                 "pendentes": 1}]
    assert estado["espera"] == [{"id": painel.id_chat("+55 11 98888-7777"), "posicao": 1, "pendentes": 1}]
    texto = json.dumps(estado)
    assert "Zuleide" not in texto and "98888" not in texto


def test_pausar_retomar_e_simulacao():
    assert painel.aplicar({"acao": "pausar"}, None).startswith("ok") and pause_event.is_set()
    assert painel.aplicar({"acao": "retomar"}, None).startswith("ok") and not pause_event.is_set()
    painel.aplicar({"acao": "simulacao", "valor": False}, None)
    assert CONFIG["mode_dry_run"] is False


def test_limite_muda_na_hora_e_promove_da_espera(store):  # noqa: F811
    agendador, _, _ = _agendador(1, store)
    asyncio.run(_adicionar(agendador, store, "ana", "m1"))
    asyncio.run(_adicionar(agendador, store, "bia", "m2"))
    assert painel.aplicar({"acao": "limite", "valor": 2}, agendador).startswith("ok")
    assert agendador.sessoes.expirar() == ["bia"]
    assert painel.aplicar({"acao": "limite", "valor": 0}, agendador).startswith("erro")


def test_encerrar_libera_a_vaga_e_descarta_a_fila_do_contato(store):  # noqa: F811
    agendador, _, _ = _agendador(1, store)
    asyncio.run(_adicionar(agendador, store, "ana", "m1"))
    asyncio.run(_adicionar(agendador, store, "ana", "m2"))
    resultado = painel.aplicar({"acao": "encerrar", "chat": painel.id_chat("ana")}, agendador)
    assert resultado.startswith("ok") and "2 mensagem" in resultado
    assert not agendador.sessoes.esta_ativa("ana") and len(agendador.pendentes) == 0
    assert store.get(message_key("ana", "m1")) == "blocked"


def test_bloquear_e_desbloquear_por_prefixo_do_id(store):  # noqa: F811
    agendador, _, _ = _agendador(2, store)
    asyncio.run(_adicionar(agendador, store, "ana", "m1"))
    prefixo = painel.id_chat("ana")[:5]
    assert painel.aplicar({"acao": "bloquear", "chat": prefixo}, agendador).startswith("ok")
    assert "ana" in agendador.bloqueados
    assert painel.instantaneo(agendador)["bloqueados"] == [painel.id_chat("ana")]
    assert painel.aplicar({"acao": "desbloquear", "chat": prefixo}, agendador).startswith("ok")
    assert painel.aplicar({"acao": "encerrar", "chat": "ffffffff"}, agendador).startswith("erro")


def test_contato_bloqueado_e_ignorado_pelo_scanner(chat_falso, store):  # noqa: F811
    agendador, fila, inicializados = AgendadorFalso(), asyncio.Queue(), {"ana"}
    agendador.bloqueados.add("ana")
    chat_falso["mensagens"] = [{"id": "a", "texto": "URGENTE: vacina tem chip", "midia": False}]
    _coletar("ana", 0, agendador, fila, store, inicializados)
    assert agendador.recebidas == [] and fila.empty()
    assert store.get(message_key("ana", "a")) == "blocked"


def test_comandos_sem_modo_aberto_e_desconhecidos():
    assert painel.aplicar({"acao": "limite", "valor": 3}, None).startswith("erro")
    assert painel.aplicar({"acao": "explodir"}, None).startswith("erro")


class RedisFalso:
    def __init__(self):
        self.listas, self.valores = {}, {}

    async def lpop(self, chave):
        lista = self.listas.get(chave, [])
        return lista.pop(0) if lista else None

    async def lpush(self, chave, valor):
        self.listas.setdefault(chave, []).insert(0, valor)

    async def ltrim(self, chave, inicio, fim):
        self.listas[chave] = self.listas.get(chave, [])[inicio:fim + 1]

    async def set(self, chave, valor, ex=None):
        self.valores[chave] = valor


def test_loop_aplica_comandos_responde_e_publica_o_estado():
    redis = RedisFalso()
    redis.listas[PAINEL_COMANDOS] = [json.dumps({"acao": "pausar"}), "isso não é json"]

    async def cenario():
        tarefa = asyncio.create_task(painel.painel_loop(redis, None, intervalo=0.01))
        await asyncio.sleep(0.05)
        tarefa.cancel()
        await asyncio.gather(tarefa, return_exceptions=True)

    asyncio.run(cenario())
    resultados = [json.loads(e)["resultado"] for e in redis.listas[PAINEL_EVENTOS]]
    assert resultados[-1].startswith("ok: bot pausado") and resultados[0].startswith("erro")
    assert json.loads(redis.valores[PAINEL_ESTADO_CONECTOR])["pausado"] is True

"""Painel de controle do bot no terminal da VM: estado ao vivo e comandos.

Ao vivo (atualiza a cada 2 s; Ctrl+C sai):
    painel
Comandos:
    painel pausar | retomar
    painel simulacao on|off
    painel limite <n>
    painel encerrar <contato>      (o identificador de 8 caracteres mostrado no painel)
    painel bloquear <contato> | desbloquear <contato>

Mostra: serviços, release no ar, servidores do Ollama, fila do núcleo, sessões ativas e em espera,
últimas análises e últimos comandos. O conector publica o estado e aplica os comandos
(`conectores/whatsapp/src/painel.py`); o worker registra as análises (`app/worker.py`). Os contatos
aparecem só pelo hash do nome. Roda com as variáveis de `worker.env`, a partir da release no ar.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import redis

from app import config, llm
from app.contrato import FILA, PAINEL_ANALISES, PAINEL_COMANDOS, PAINEL_ESTADO_CONECTOR, PAINEL_EVENTOS

ATUAL = Path(os.getenv("BOT_DEPLOY_BASE", str(Path.home()))) / "bot-core-current"
LIMPAR = "\033[2J\033[H"
VERDE, AMARELO, VERMELHO, CINZA, NEGRITO, FIM = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"


def cor(texto: str, codigo: str) -> str:
    return f"{codigo}{texto}{FIM}" if sys.stdout.isatty() else texto


def hora(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def ler_lista(r, chave: str, n: int) -> list[dict]:
    return [json.loads(x) for x in r.lrange(chave, 0, n - 1)]


def servicos() -> dict[str, str]:
    nomes = ["bot-worker", "bot-conector"]
    try:
        saida = subprocess.run(["systemctl", "is-active", *nomes], capture_output=True, text=True, timeout=5).stdout
        return dict(zip(nomes, saida.split()))
    except (OSError, subprocess.SubprocessError):
        return {nome: "?" for nome in nomes}


def tela(r, ollama: list[dict], ollama_quando: float) -> str:
    linhas = []
    agora = time.time()
    release = ATUAL.resolve().name if ATUAL.exists() else "?"
    estado_servicos = "  ".join(f"{n}: {cor(s, VERDE if s == 'active' else VERMELHO)}" for n, s in servicos().items())
    linhas.append(cor(f"BOT-CORE · {hora(agora)} · release {release}", NEGRITO) + f"   {estado_servicos}")

    bruto = r.get(PAINEL_ESTADO_CONECTOR)
    conector = json.loads(bruto) if bruto else None
    if conector is None:
        linhas.append(cor("CONECTOR SEM SINAL (parado ou reiniciando)", VERMELHO))
    else:
        atraso = agora - conector["quando"]
        pausa = cor("PAUSADO", VERMELHO) if conector["pausado"] else cor("rodando", VERDE)
        envio = cor("SIMULAÇÃO (não envia)", AMARELO) if conector["simulacao"] else cor("envio real", VERDE)
        linhas.append(f"Conector: {pausa} · {envio} · acesso {conector['modo_acesso']}"
                      + (cor(f" · estado de {atraso:.0f} s atrás", AMARELO) if atraso > 5 else ""))

    linhas.append("")
    linhas.append(cor("Ollama", NEGRITO) + cor(f"  (verificado às {hora(ollama_quando)})", CINZA))
    for i, s in enumerate(ollama):
        papel = "principal" if i == 0 else "reserva"
        estado = cor("ok", VERDE) if s["ok"] else cor(f"fora ({s['erro']})", VERMELHO)
        linhas.append(f"  {papel:9} {s['url']:32} {s['modelo']:16} {estado} {cor(str(s['tempo_s']) + ' s', CINZA)}")

    fila = r.zcard(FILA)
    em_execucao = sum(1 for _ in r.scan_iter("arq:in-progress:*", count=100))
    linhas.append("")
    linhas.append(cor("Núcleo", NEGRITO) + f"  fila: {fila} job(s) · em análise: {em_execucao}")

    if conector and "ativas" in conector:
        linhas.append("")
        linhas.append(cor(f"Sessões {len(conector['ativas'])}/{conector['limite']}", NEGRITO)
                      + f" (encerram após {conector['inatividade_s'] / 60:.0f} min sem mensagem)"
                      + f" · fila do conector: {conector['pendentes_total']} · espera: {len(conector['espera'])}")
        for s in conector["ativas"]:
            situacao = cor("analisando", AMARELO) if s["analisando"] else f"inativa há {s['inativo_s']} s"
            linhas.append(f"  {s['id']}  {situacao}  pendentes: {s['pendentes']}")
        for s in conector["espera"]:
            espera = cor(f"espera #{s['posicao']}", CINZA)
            linhas.append(f"  {s['id']}  {espera}  pendentes: {s['pendentes']}")
        if conector["bloqueados"]:
            linhas.append(cor(f"  bloqueados: {', '.join(conector['bloqueados'])}", VERMELHO))

    linhas.append("")
    linhas.append(cor("Últimas análises", NEGRITO))
    analises = ler_lista(r, PAINEL_ANALISES, 8)
    if not analises:
        linhas.append(cor("  nenhuma ainda", CINZA))
    for a in analises:
        if a["llm_indisponivel"]:
            resultado = cor("modelo fora do ar", VERMELHO)
        elif a["recusada"]:
            resultado = cor(f"recusada ({a['motivo']})", AMARELO)
        else:
            resultado = (f"{a['fontes']} fonte(s), {a['fontes_da_base']} da base"
                         + (" + web" if a.get("web") else ""))
        servidor = (a.get("llm") or "-").replace("http://", "").split(":")[0]
        linhas.append(f"  {hora(a['quando'])}  {a['chat']}  {a['duracao_s']:5.1f} s  {resultado}  {cor(servidor, CINZA)}")

    eventos = ler_lista(r, PAINEL_EVENTOS, 4)
    if eventos:
        linhas.append("")
        linhas.append(cor("Últimos comandos", NEGRITO))
        for e in eventos:
            linhas.append(f"  {hora(e['quando'])}  {e['comando'].get('acao')}  {e['resultado']}")
    linhas.append("")
    linhas.append(cor("Comandos: painel pausar | retomar | simulacao on|off | limite N | encerrar ID | "
                      "bloquear ID | desbloquear ID", CINZA))
    return "\n".join(linhas)


def ao_vivo(r) -> None:
    ollama, ollama_quando = [], 0.0
    try:
        while True:
            if time.time() - ollama_quando > 15:  # a consulta ao Ollama é a parte lenta
                ollama, ollama_quando = llm.situacao(timeout=3), time.time()
            print(LIMPAR + tela(r, ollama, ollama_quando), flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        print()


def montar_comando(args: list[str]) -> dict:
    acao = args[0]
    if acao in ("pausar", "retomar"):
        return {"acao": acao}
    if acao == "simulacao" and len(args) == 2 and args[1] in ("on", "off"):
        return {"acao": acao, "valor": args[1] == "on"}
    if acao == "limite" and len(args) == 2 and args[1].isdigit():
        return {"acao": acao, "valor": int(args[1])}
    if acao in ("encerrar", "bloquear", "desbloquear") and len(args) == 2:
        return {"acao": acao, "chat": args[1]}
    raise SystemExit(__doc__)


def enviar(r, comando: dict) -> int:
    antes = r.lindex(PAINEL_EVENTOS, 0)
    r.rpush(PAINEL_COMANDOS, json.dumps(comando, ensure_ascii=False))
    for _ in range(20):  # o conector lê os comandos a cada segundo
        time.sleep(0.5)
        ultimo = r.lindex(PAINEL_EVENTOS, 0)
        if ultimo is not None and ultimo != antes:
            resultado = json.loads(ultimo)["resultado"]
            print(resultado)
            return 0 if resultado.startswith("ok") else 1
    print("sem resposta do conector em 10 s (ele está rodando?); o comando fica na fila até ele voltar")
    return 1


def main() -> int:
    r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
    if len(sys.argv) == 1:
        ao_vivo(r)
        return 0
    if sys.argv[1] in ("-h", "--help", "ajuda"):
        print(__doc__)
        return 0
    return enviar(r, montar_comando(sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())

"""Registro das releases implantadas na VM (`~/bot-core-runtime/releases.jsonl`).

Cada linha é um evento: implantada, revertida, abortada, barrada ou reversão manual, com o commit, a
release anterior, um detalhe e as métricas do portão de avaliação. O registro responde qual release
estava no ar antes da atual e qual é a campeã (para o rollback), aplica o portão e lista o histórico.

Portão (champion/challenger): a release nova é aprovada se a métrica do portão (`top5`: a checagem
esperada entre as 5 fontes) for de pelo menos `--piso` e não ficar mais de `--margem` abaixo da
campeã. A campeã é reavaliada no mesmo banco e no mesmo momento que a release nova
(`--avaliacao-campea`), porque o coletor RSS muda a base de hora em hora e notas antigas deixariam a
comparação injusta. Aprovada com nota igual ou maior que a da campeã, a release nova vira a campeã.
O `top1` é registrado, mas não barra: com mais agências na base, outra checagem correta do mesmo boato
pode vir antes da que o golden set aponta.

Uso:
    python deploy/releases.py registrar --commit <sha> --status implantada [--anterior <curto>] [--detalhe ...]
        [--metricas '<json>'] [--campea]
    python deploy/releases.py portao --avaliacao avaliacao.json [--avaliacao-campea campea.json]
        [--piso 0.85] [--margem 0.03]
    python deploy/releases.py listar [--limite 15]
    python deploy/releases.py anterior
    python deploy/releases.py campea
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(os.getenv("BOT_DEPLOY_BASE", str(Path.home())))
REGISTRO = BASE / "bot-core-runtime" / "releases.jsonl"
RELEASES = BASE / "bot-core-releases"
ATUAL = BASE / "bot-core-current"
STATUS = ("implantada", "revertida", "abortada", "barrada", "reversao_manual")
METRICA_PORTAO = "top5"


def ler() -> list[dict]:
    if not REGISTRO.exists():
        return []
    return [json.loads(linha) for linha in REGISTRO.read_text(encoding="utf-8").splitlines() if linha.strip()]


def registrar(commit: str, status: str, anterior: str = "", detalhe: str = "", metricas: dict | None = None,
              campea: bool = False) -> dict:
    if status not in STATUS:
        raise ValueError(f"status inválido: {status}")
    evento = {"data": datetime.now(timezone.utc).isoformat(timespec="seconds"), "commit": commit,
              "release": commit[:7], "status": status, "anterior": anterior, "detalhe": detalhe,
              "metricas": metricas or {}, "campea": campea}
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRO.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(evento, ensure_ascii=False) + "\n")
    return evento


def no_ar() -> str:
    return ATUAL.resolve().name if ATUAL.exists() else ""


def anterior() -> str:
    """Release que esteve no ar antes da atual e ainda existe no disco ("" se não houver)."""
    atual = no_ar()
    # Cada troca registra a release que saiu ("anterior") e a que entrou; a que saiu conta mesmo que
    # tenha sido colocada no ar antes de o registro existir.
    no_ar_antes = []
    for e in ler():
        if e["status"] in ("implantada", "reversao_manual"):
            no_ar_antes += [e["anterior"], e["release"]] if e["anterior"] else [e["release"]]
    for release in reversed(no_ar_antes):
        if release != atual and (RELEASES / release).is_dir():
            return release
    return ""


def campea() -> dict | None:
    """Último evento que promoveu uma release a campeã e cuja release ainda está no disco."""
    for e in reversed(ler()):
        if e.get("campea") and (RELEASES / e["release"]).is_dir():
            return e
    return None


def portao(metricas: dict, piso: float, margem: float, metricas_campea: dict | None = None) -> dict:
    """Decide se a release nova (challenger) pode ir ao ar, comparando com o piso e com a campeã.

    `metricas_campea` é a reavaliação da campeã no mesmo banco; sem ela, vale a nota registrada.
    """
    nome = METRICA_PORTAO
    nota = metricas.get(nome)
    atual = campea()
    if metricas_campea is not None:
        nota_campea, origem = metricas_campea.get(nome), "reavaliada agora"
    else:
        nota_campea, origem = (atual or {}).get("metricas", {}).get(nome), "nota registrada"
    decisao = {"metrica": nome, "nota": nota, "piso": piso, "margem": margem,
               "campea": (atual or {}).get("release"), "nota_campea": nota_campea, "origem_nota_campea": origem,
               "aprovada": False, "promover": False}
    if not isinstance(nota, (int, float)):
        decisao["motivo"] = f"avaliação sem {nome}"
    elif nota < piso:
        decisao["motivo"] = f"{nome} {nota:.0%} abaixo do piso {piso:.0%}"
    elif isinstance(nota_campea, (int, float)) and nota < nota_campea - margem:
        decisao["motivo"] = (f"{nome} {nota:.0%} mais de {margem:.0%} abaixo da campeã "
                             f"{atual['release'] if atual else ''} ({nota_campea:.0%}, {origem})")
    else:
        decisao["aprovada"] = True
        decisao["promover"] = not isinstance(nota_campea, (int, float)) or nota >= nota_campea
        decisao["motivo"] = ("nova campeã" if decisao["promover"]
                             else f"aprovada; a campeã continua {atual['release'] if atual else ''}")
    return decisao


def _pct(valor) -> str:
    return f"{valor:.0%}" if isinstance(valor, (int, float)) else "-"


def listar(limite: int) -> str:
    atual = no_ar()
    eventos = ler()
    # Só o último evento que pôs a release atual no ar recebe a marca.
    ultimo_no_ar = max((i for i, e in enumerate(eventos)
                        if e["release"] == atual and e["status"] in ("implantada", "reversao_manual")), default=-1)
    campea_atual = (campea() or {}).get("release")
    linhas = [f"{'data (UTC)':20} {'release':8} {'status':16} {'top1':>5} {'top5':>5}  detalhe"]
    for i, e in list(enumerate(eventos))[-limite:]:
        m = e.get("metricas", {})
        marca = " ← no ar" if i == ultimo_no_ar else ""
        marca += " ★ campeã" if e.get("campea") and e["release"] == campea_atual else ""
        linhas.append(f"{e['data'][:19]:20} {e['release']:8} {e['status']:16} {_pct(m.get('top1')):>5} "
                      f"{_pct(m.get('top5')):>5}  {e['detalhe']}{marca}")
    linhas.append(f"\nNo ar agora: {atual or '-'} | releases no disco: "
                  + ", ".join(sorted(p.name for p in RELEASES.iterdir() if p.is_dir() and not p.name.startswith(".")))
                  if RELEASES.exists() else "")
    return "\n".join(linhas)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="acao", required=True)
    reg = sub.add_parser("registrar")
    reg.add_argument("--commit", required=True)
    reg.add_argument("--status", required=True, choices=STATUS)
    reg.add_argument("--anterior", default="")
    reg.add_argument("--detalhe", default="")
    reg.add_argument("--metricas", default="", help="JSON com as métricas do portão")
    reg.add_argument("--campea", action="store_true")
    por = sub.add_parser("portao")
    por.add_argument("--avaliacao", required=True, type=Path)
    por.add_argument("--avaliacao-campea", type=Path)
    por.add_argument("--piso", type=float, default=0.85)
    por.add_argument("--margem", type=float, default=0.03)
    sub.add_parser("campea")
    lis = sub.add_parser("listar")
    lis.add_argument("--limite", type=int, default=15)
    sub.add_parser("anterior")
    args = parser.parse_args()

    if args.acao == "registrar":
        registrar(args.commit, args.status, args.anterior, args.detalhe,
                  json.loads(args.metricas) if args.metricas else None, args.campea)
    elif args.acao == "portao":
        avaliacao = json.loads(args.avaliacao.read_text(encoding="utf-8"))
        da_campea = (json.loads(args.avaliacao_campea.read_text(encoding="utf-8")).get("metricas", {})
                     if args.avaliacao_campea else None)
        decisao = portao(avaliacao.get("metricas", {}), args.piso, args.margem, da_campea)
        print(json.dumps(decisao, ensure_ascii=False))
        raise SystemExit(0 if decisao["aprovada"] else 3)
    elif args.acao == "listar":
        print(listar(args.limite))
    elif args.acao == "campea":
        print((campea() or {}).get("release", ""))
    else:
        print(anterior())


if __name__ == "__main__":
    main()

"""Registro das releases implantadas na VM (`~/bot-core-runtime/releases.jsonl`).

Cada linha é um evento: implantada, revertida, abortada, barrada ou reversão manual, com o commit, a
release anterior e um detalhe. O registro responde qual release estava no ar antes da atual (para o
rollback) e lista o histórico. As métricas do portão de avaliação entram no campo `metricas`.

Uso:
    python deploy/releases.py registrar --commit <sha> --status implantada [--anterior <curto>] [--detalhe ...]
    python deploy/releases.py listar [--limite 15]
    python deploy/releases.py anterior
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


def ler() -> list[dict]:
    if not REGISTRO.exists():
        return []
    return [json.loads(linha) for linha in REGISTRO.read_text(encoding="utf-8").splitlines() if linha.strip()]


def registrar(commit: str, status: str, anterior: str = "", detalhe: str = "", metricas: dict | None = None) -> dict:
    if status not in STATUS:
        raise ValueError(f"status inválido: {status}")
    evento = {"data": datetime.now(timezone.utc).isoformat(timespec="seconds"), "commit": commit,
              "release": commit[:7], "status": status, "anterior": anterior, "detalhe": detalhe,
              "metricas": metricas or {}}
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


def listar(limite: int) -> str:
    atual = no_ar()
    linhas = [f"{'data (UTC)':20} {'release':8} {'status':16} {'top1':>5}  detalhe"]
    for e in ler()[-limite:]:
        top1 = e.get("metricas", {}).get("top1")
        marca = " ← no ar" if e["release"] == atual and e["status"] in ("implantada", "reversao_manual") else ""
        linhas.append(f"{e['data'][:19]:20} {e['release']:8} {e['status']:16} "
                      f"{(f'{top1:.0%}' if isinstance(top1, (int, float)) else '-'):>5}  {e['detalhe']}{marca}")
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
    lis = sub.add_parser("listar")
    lis.add_argument("--limite", type=int, default=15)
    sub.add_parser("anterior")
    args = parser.parse_args()

    if args.acao == "registrar":
        registrar(args.commit, args.status, args.anterior, args.detalhe,
                  json.loads(args.metricas) if args.metricas else None)
    elif args.acao == "listar":
        print(listar(args.limite))
    else:
        print(anterior())


if __name__ == "__main__":
    main()

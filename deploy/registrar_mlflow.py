"""Envia a avaliação de uma release ao MLflow (DagsHub), para acompanhar as versões ao longo do tempo.

Cada deploy vira uma execução no experimento `bot-core-deploy`, com as métricas do portão, os parâmetros
da release (limiares, modelos, tamanho da base) e a decisão (implantada, barrada, campeã). O registro
local (`releases.jsonl`) continua sendo a fonte de verdade do deploy: se o MLflow estiver fora do ar ou
sem credencial, este script só avisa e termina com sucesso, sem barrar a implantação.

Credenciais: `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_USERNAME` e `MLFLOW_TRACKING_PASSWORD` (token do
DagsHub), lidas do ambiente (`~/bot-core-runtime/mlflow.env`).

Uso:
    python deploy/registrar_mlflow.py --avaliacao avaliacao.json --commit <sha> --status implantada
        [--decisao '<json do portão>']
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

EXPERIMENTO = os.getenv("MLFLOW_EXPERIMENT_NAME", "bot-core-deploy")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--avaliacao", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--decisao", default="{}")
    args = parser.parse_args()

    if not os.getenv("MLFLOW_TRACKING_URI"):
        print("AVISO: MLFLOW_TRACKING_URI não definida; avaliação não enviada ao MLflow")
        return 0
    try:
        import mlflow

        avaliacao = json.loads(args.avaliacao.read_text(encoding="utf-8"))
        decisao = json.loads(args.decisao or "{}")
        mlflow.set_experiment(EXPERIMENTO)
        with mlflow.start_run(run_name=f"{args.commit[:7]} ({args.status})"):
            mlflow.set_tags({"commit": args.commit, "release": args.commit[:7], "status": args.status,
                             "campea": str(bool(decisao.get("promover"))).lower(),
                             "motivo": decisao.get("motivo", "")})
            mlflow.log_params({**avaliacao.get("parametros", {}),
                               **{f"base_{k}": v for k, v in avaliacao.get("base", {}).items()}})
            mlflow.log_metrics({k: v for k, v in avaliacao.get("metricas", {}).items()
                                if isinstance(v, (int, float))})
        print(f"Avaliação enviada ao MLflow (experimento {EXPERIMENTO})")
    except Exception as erro:  # o MLflow é só visualização: falhar aqui não pode barrar o deploy
        print(f"AVISO: falha ao enviar ao MLflow ({type(erro).__name__}: {str(erro)[:200]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

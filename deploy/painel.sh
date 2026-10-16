#!/usr/bin/env bash
# Painel de controle do bot (deploy/painel.py), com o ambiente do worker e a release no ar.
# Instalado como /usr/local/bin/painel pelo deploy: `ssh -t bot-core painel` ou `ssh bot-core painel pausar`.
set -euo pipefail
BASE="${BOT_DEPLOY_BASE:-/home/ubuntu}"
RUNTIME="$BASE/bot-core-runtime"
set -a
# shellcheck source=/dev/null
. "$RUNTIME/worker.env"
set +a
cd "$BASE/bot-core-current"
exec "$RUNTIME/.venv/bin/python" -m deploy.painel "$@"

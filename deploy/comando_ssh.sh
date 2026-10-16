#!/usr/bin/env bash
# Comando forçado da chave SSH do deploy (authorized_keys: command="~/bot-core-runtime/bin/comando_ssh.sh").
#
# A chave do GitHub Actions só consegue executar estas três ações, com argumentos validados:
#   implantar <commit>        roda o deploy/implantar.sh do próprio commit (que precisa estar na main)
#   reverter anterior|campea|<commit>
#   listar
set -euo pipefail

BASE="${BOT_DEPLOY_BASE:-$HOME}"
RUNTIME="$BASE/bot-core-runtime"
ESPELHO="$RUNTIME/repo.git"
PY="$RUNTIME/.venv/bin/python"

read -r ACAO ARG EXTRA <<< "${SSH_ORIGINAL_COMMAND:-}" || true
[[ -z "${EXTRA:-}" ]] || { echo "argumentos demais"; exit 2; }

case "${ACAO:-}" in
    implantar)
        [[ "${ARG:-}" =~ ^[0-9a-f]{7,40}$ ]] || { echo "commit inválido"; exit 2; }
        git -C "$ESPELHO" fetch --quiet --prune origin
        SHA="$(git -C "$ESPELHO" rev-parse --verify --quiet "${ARG}^{commit}")" || { echo "commit não encontrado"; exit 2; }
        git -C "$ESPELHO" merge-base --is-ancestor "$SHA" refs/heads/main || { echo "o commit não está na main"; exit 2; }
        # O script de deploy vem do próprio commit: mudanças nele chegam junto com o código, revisadas no PR.
        SCRIPT="$(mktemp)"
        trap 'rm -f "$SCRIPT"' EXIT
        git -C "$ESPELHO" show "$SHA:deploy/implantar.sh" > "$SCRIPT"
        bash "$SCRIPT" "$SHA"
        ;;
    reverter)
        [[ "${ARG:-}" =~ ^(anterior|campea|[0-9a-f]{7,40})$ ]] || { echo "alvo inválido"; exit 2; }
        bash "$RUNTIME/bin/reverter.sh" "$ARG"
        ;;
    listar)
        "$PY" "$RUNTIME/bin/releases.py" listar
        ;;
    *)
        echo "ação não permitida"
        exit 2
        ;;
esac

#!/usr/bin/env bash
# Volta a VM para uma release que ainda está no disco, sem recriar nada: troca o link
# ~/bot-core-current, reinicia worker e conector e roda o teste de fumaça.
#
# Uso: deploy/reverter.sh anterior        (a release que estava no ar antes da atual)
#      deploy/reverter.sh campea          (a última release promovida a campeã pelo portão)
#      deploy/reverter.sh <commit>        (7 a 40 caracteres; ver deploy/releases.py listar)
#
# As migrações não são desfeitas: elas seguem o padrão expand/contract (ver deploy/systemd/README.md),
# então a release antiga continua funcionando com o esquema novo.
set -euo pipefail

ALVO="${1:?uso: reverter.sh anterior|campea|<commit>}"
BASE="${BOT_DEPLOY_BASE:-$HOME}"
RELEASES="$BASE/bot-core-releases"
ATUAL="$BASE/bot-core-current"
RUNTIME="$BASE/bot-core-runtime"
PY="$RUNTIME/.venv/bin/python"
UNIDADES=(bot-worker.service bot-conector.service)

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
falhar() { log "ERRO: $*"; exit 1; }

exec 9>"$RUNTIME/deploy.lock"
flock -n 9 || { log "Outro deploy ou reversão em andamento"; exit 75; }

ATUAL_DIR="$(readlink -f "$ATUAL")"
REGISTRO_PY="$RUNTIME/bin/releases.py"
if [[ "$ALVO" == "anterior" || "$ALVO" == "campea" ]]; then
    RELEASE="$("$PY" "$REGISTRO_PY" "$ALVO")"
    [[ -n "$RELEASE" ]] || falhar "não há release $ALVO no disco"
else
    [[ "$ALVO" =~ ^[0-9a-f]{7,40}$ ]] || falhar "commit inválido: $ALVO"
    RELEASE="${ALVO:0:7}"
fi
DESTINO="$RELEASES/$RELEASE"
[[ -d "$DESTINO" ]] || falhar "a release $RELEASE não está no disco"
[[ "$DESTINO" != "$ATUAL_DIR" ]] || { log "A release $RELEASE já está no ar"; exit 0; }
SHA="$(sed -n 's/^commit \([0-9a-f]*\).*/\1/p' "$DESTINO/RELEASE")"

log "Voltando de $(basename "$ATUAL_DIR") para $RELEASE"
ln -sfn "$DESTINO" "$ATUAL.novo" && mv -T "$ATUAL.novo" "$ATUAL"
sudo systemctl restart "${UNIDADES[@]}"
INICIO=$SECONDS
for unidade in "${UNIDADES[@]}"; do  # até 90 s: o systemd tenta de novo a cada 15 s se a partida falhar
    until systemctl is-active --quiet "$unidade"; do
        (( SECONDS - INICIO < 90 )) || falhar "$unidade não subiu em 90 s; conferir journalctl -u $unidade"
        sleep 3
    done
done
# shellcheck source=/dev/null
(set -a; . "$RUNTIME/worker.env"; set +a; cd "$ATUAL" && PYTHONPATH="$ATUAL" "$PY" "$RUNTIME/bin/fumaca.py" --timeout 300) \
    || falhar "o teste de fumaça falhou na release $RELEASE"

"$PY" "$REGISTRO_PY" registrar --commit "${SHA:-$RELEASE}" --status reversao_manual \
    --anterior "$(basename "$ATUAL_DIR")" --detalhe "reversão pedida: $ALVO"
log "Release $RELEASE no ar"

#!/usr/bin/env bash
# Implanta na VM um commit da main.
#
# Etapas: cria a release em ~/bot-core-releases/<commit>, instala dependências (só se mudaram),
# aplica as migrações, confere as dependências externas (Redis, banco, Ollama), troca o link
# ~/bot-core-current de forma atômica, reinicia worker e conector e roda o teste de fumaça. Uma falha
# antes da troca do link não muda nada no ar; uma falha depois volta sozinha para a release anterior.
#
# Uso: deploy/implantar.sh <commit>   (normalmente chamado pelo deploy/comando_ssh.sh)
set -euo pipefail

SHA_PEDIDO="${1:?uso: implantar.sh <commit>}"
BASE="${BOT_DEPLOY_BASE:-$HOME}"
RELEASES="$BASE/bot-core-releases"
ATUAL="$BASE/bot-core-current"
RUNTIME="$BASE/bot-core-runtime"
ESPELHO="$RUNTIME/repo.git"
VENV="$RUNTIME/.venv"
PY="$VENV/bin/python"
MANTER="${BOT_DEPLOY_MANTER:-5}"
UNIDADES=(bot-worker.service bot-conector.service)

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
falhar() { log "ERRO: $*"; exit 1; }

exec 9>"$RUNTIME/deploy.lock"
flock -n 9 || { log "Outro deploy ou reversão em andamento"; exit 75; }

# 1. Release -----------------------------------------------------------------------------------------
git -C "$ESPELHO" fetch --quiet --prune origin
SHA=$(git -C "$ESPELHO" rev-parse --verify --quiet "${SHA_PEDIDO}^{commit}") || falhar "commit não encontrado: $SHA_PEDIDO"
git -C "$ESPELHO" merge-base --is-ancestor "$SHA" refs/heads/main || falhar "o commit $SHA não está na main"
CURTO="${SHA:0:7}"
DESTINO="$RELEASES/$CURTO"
ANTERIOR_DIR="$(readlink -f "$ATUAL" 2>/dev/null || true)"
ANTERIOR="$(basename "${ANTERIOR_DIR:-}")"

if [[ "$ANTERIOR_DIR" == "$DESTINO" ]]; then
    log "A release $CURTO já está no ar; nada a fazer"
    exit 0
fi

if [[ ! -d "$DESTINO" ]]; then
    log "Criando a release $CURTO"
    TEMP="$(mktemp -d "$RELEASES/.tmp-XXXXXX")"
    git -C "$ESPELHO" archive "$SHA" | tar -x -C "$TEMP"
    printf 'commit %s\ncriada_em %s\n' "$SHA" "$(date -u +%FT%TZ)" > "$TEMP/RELEASE"
    mv "$TEMP" "$DESTINO"
fi

# Ferramentas de deploy num lugar estável (~/bot-core-runtime/bin): a verificação e a reversão não podem
# depender de arquivos da release, que numa release antiga podem nem existir.
FERRAMENTAS=(comando_ssh.sh reverter.sh releases.py fumaca.py)
for ferramenta in "${FERRAMENTAS[@]}"; do
    if [[ -f "$DESTINO/deploy/$ferramenta" ]] && ! cmp -s "$DESTINO/deploy/$ferramenta" "$RUNTIME/bin/$ferramenta"; then
        install -D -m 755 "$DESTINO/deploy/$ferramenta" "$RUNTIME/bin/$ferramenta"
        log "Ferramenta de deploy atualizada: $ferramenta"
    fi
done

registrar() {  # registrar <status> [detalhe] [métricas em JSON]
    local args=(registrar --commit "$SHA" --status "$1" --anterior "$ANTERIOR" --detalhe "${2:-}")
    [[ -n "${3:-}" ]] && args+=(--metricas "$3")
    "$PY" "$RUNTIME/bin/releases.py" "${args[@]}"
}
abortar() { registrar abortada "$1"; falhar "$1 (nada mudou no ar; continua a release $ANTERIOR)"; }

com_ambiente() {  # com_ambiente <arquivo .env> <comando...>: roda na release nova com aquele ambiente
    local arquivo="$1"; shift
    # shellcheck source=/dev/null
    (set -a; . "$arquivo"; set +a; cd "$DESTINO" && "$@")
}

# 2. Dependências (o venv é compartilhado entre releases) --------------------------------------------
HASH_REQ=$(cat "$DESTINO/requirements.txt" "$DESTINO/conectores/whatsapp/requirements.txt" | sha256sum | cut -c1-16)
if [[ "$(cat "$VENV/.requisitos" 2>/dev/null || true)" != "$HASH_REQ" ]]; then
    log "Instalando dependências (requirements mudou)"
    "$VENV/bin/pip" install --quiet -r "$DESTINO/requirements.txt" -r "$DESTINO/conectores/whatsapp/requirements.txt" \
        || abortar "falha ao instalar dependências"
    echo "$HASH_REQ" > "$VENV/.requisitos"
fi

# 3. Migrações (credencial própria; as do worker e do coletor não alteram o esquema) -----------------
log "Aplicando migrações"
com_ambiente "$RUNTIME/migracao.env" "$VENV/bin/alembic" upgrade head || abortar "falha nas migrações"

# 4. Verificações antes de trocar --------------------------------------------------------------------
log "Conferindo a release e as dependências externas"
com_ambiente "$RUNTIME/worker.env" "$PY" -c "import app.worker, conectores.whatsapp.src.main" \
    || abortar "a release não importa"
com_ambiente "$RUNTIME/worker.env" "$PY" -m deploy.preflight \
    || abortar "Redis ou banco indisponível"

# 5. Unidades do systemd que mudaram -----------------------------------------------------------------
RECARREGAR=0
for unidade in "${UNIDADES[@]}"; do
    if ! cmp -s "$DESTINO/deploy/systemd/$unidade" "/etc/systemd/system/$unidade"; then
        log "Atualizando a unidade $unidade"
        sudo install -m 644 "$DESTINO/deploy/systemd/$unidade" "/etc/systemd/system/$unidade"
        RECARREGAR=1
    fi
done
[[ $RECARREGAR == 1 ]] && sudo systemctl daemon-reload

# 6. Troca e verificação -----------------------------------------------------------------------------
apontar() { ln -sfn "$1" "$ATUAL.novo" && mv -T "$ATUAL.novo" "$ATUAL"; }

aguardar_ativos() {  # espera até 90 s: o systemd tenta de novo a cada 15 s se a partida falhar
    local unidade inicio=$SECONDS
    for unidade in "${UNIDADES[@]}"; do
        until systemctl is-active --quiet "$unidade"; do
            (( SECONDS - inicio < 90 )) || { log "$unidade não ficou ativo em 90 s"; return 1; }
            sleep 3
        done
    done
    sleep 5
    for unidade in "${UNIDADES[@]}"; do  # continua ativo depois de alguns segundos (não caiu na partida)
        systemctl is-active --quiet "$unidade" || { log "$unidade caiu logo depois de subir"; return 1; }
    done
}

verificar() {
    sudo systemctl restart "${UNIDADES[@]}"
    aguardar_ativos || return 1
    # shellcheck source=/dev/null
    (set -a; . "$RUNTIME/worker.env"; set +a; cd "$ATUAL" && PYTHONPATH="$ATUAL" "$PY" "$RUNTIME/bin/fumaca.py" --timeout 300)
}

log "Colocando a release $CURTO no ar (antes: ${ANTERIOR:-nenhuma})"
apontar "$DESTINO"
if ! verificar; then
    if [[ -n "$ANTERIOR_DIR" && -d "$ANTERIOR_DIR" ]]; then
        log "Verificação falhou; voltando para $ANTERIOR"
        apontar "$ANTERIOR_DIR"
        if verificar; then
            registrar revertida "verificação falhou depois da troca; voltou para $ANTERIOR"
            falhar "deploy revertido para $ANTERIOR"
        fi
        registrar revertida "verificação falhou e a release anterior $ANTERIOR também não passou"
        falhar "a release anterior também falhou na verificação; conferir journalctl -u bot-worker -u bot-conector"
    fi
    registrar revertida "verificação falhou e não há release anterior"
    falhar "verificação falhou e não há release anterior"
fi
registrar implantada "ok"
log "Release $CURTO no ar"

# 7. Limpeza: mantém as $MANTER releases mais recentes, a atual e a anterior -------------------------
mapfile -t ANTIGAS < <(ls -1dt "$RELEASES"/*/ 2>/dev/null | sed 's#/$##' | tail -n +"$((MANTER + 1))")
for dir in "${ANTIGAS[@]}"; do
    [[ "$dir" == "$DESTINO" || "$dir" == "$ANTERIOR_DIR" ]] && continue
    log "Removendo a release antiga $(basename "$dir")"
    rm -rf "$dir"
done

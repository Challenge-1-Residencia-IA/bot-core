#!/usr/bin/env bash
# Implanta na VM um commit da main.
#
# Etapas: cria a release em ~/bot-core-releases/<commit>, instala dependências (só se mudaram),
# aplica as migrações, confere as dependências externas (Redis e banco), avalia a busca com o golden
# set (portão champion/challenger), troca o link ~/bot-core-current de forma atômica, reinicia worker e
# conector e roda o teste de fumaça. Uma falha ou reprovação antes da troca do link não muda nada no
# ar; uma falha depois volta sozinha para a release anterior. As notas vão para releases.jsonl e para
# o MLflow (DagsHub), se ~/bot-core-runtime/mlflow.env existir.
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
GOLDEN_SET="${BOT_DEPLOY_GOLDEN_SET:-$BASE/dados-ingestao/golden_set_v1.jsonl}"
PISO="${BOT_DEPLOY_PISO:-0.85}"
MARGEM="${BOT_DEPLOY_MARGEM_CAMPEA:-0.03}"
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
FERRAMENTAS=(comando_ssh.sh reverter.sh releases.py fumaca.py registrar_mlflow.py painel.sh)
for ferramenta in "${FERRAMENTAS[@]}"; do
    if [[ -f "$DESTINO/deploy/$ferramenta" ]] && ! cmp -s "$DESTINO/deploy/$ferramenta" "$RUNTIME/bin/$ferramenta"; then
        install -D -m 755 "$DESTINO/deploy/$ferramenta" "$RUNTIME/bin/$ferramenta"
        log "Ferramenta de deploy atualizada: $ferramenta"
    fi
done

METRICAS=""
DECISAO="{}"

# Atalho `painel` no PATH (sudo sem senha na VM).
if [[ -x "$RUNTIME/bin/painel.sh" && "$(readlink /usr/local/bin/painel 2>/dev/null || true)" != "$RUNTIME/bin/painel.sh" ]]; then
    sudo ln -sfn "$RUNTIME/bin/painel.sh" /usr/local/bin/painel && log "Atalho /usr/local/bin/painel criado"
fi

registrar() {  # registrar <status> [detalhe] [campea=1]; usa as métricas da avaliação, se houver
    local args=(registrar --commit "$SHA" --status "$1" --anterior "$ANTERIOR" --detalhe "${2:-}")
    [[ -n "$METRICAS" ]] && args+=(--metricas "$METRICAS")
    [[ "${3:-}" == 1 ]] && args+=(--campea)
    "$PY" "$RUNTIME/bin/releases.py" "${args[@]}"
    if [[ -f "$DESTINO/avaliacao.json" ]]; then
        if [[ -f "$RUNTIME/mlflow.env" ]]; then
            # shellcheck source=/dev/null
            (set -a; . "$RUNTIME/mlflow.env"; set +a
             "$PY" "$RUNTIME/bin/registrar_mlflow.py" --avaliacao "$DESTINO/avaliacao.json" --commit "$SHA" \
                 --status "$1" --decisao "$DECISAO") || true
        else
            log "Sem $RUNTIME/mlflow.env: avaliação registrada só em releases.jsonl"
        fi
    fi
}
abortar() { registrar abortada "$1"; falhar "$1 (nada mudou no ar; continua a release $ANTERIOR)"; }

com_ambiente() {  # com_ambiente <arquivo .env> <comando...>: roda na release nova com aquele ambiente
    local arquivo="$1"; shift
    # shellcheck source=/dev/null
    (set -a; . "$arquivo"; set +a; cd "$DESTINO" && "$@")
}

# 2. Dependências (o venv é compartilhado entre releases) --------------------------------------------
REQUISITOS=("$DESTINO/requirements.txt" "$DESTINO/conectores/whatsapp/requirements.txt")
[[ -f "$DESTINO/deploy/requirements.txt" ]] && REQUISITOS+=("$DESTINO/deploy/requirements.txt")
HASH_REQ=$(cat "${REQUISITOS[@]}" | sha256sum | cut -c1-16)
if [[ "$(cat "$VENV/.requisitos" 2>/dev/null || true)" != "$HASH_REQ" ]]; then
    log "Instalando dependências (requirements mudou)"
    "$VENV/bin/pip" install --quiet "${REQUISITOS[@]/#/-r}" \
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

# 4b. Portão: avaliação da busca com o golden set, comparada com o piso e com a release campeã -------
[[ -f "$GOLDEN_SET" ]] || abortar "golden set não encontrado em $GOLDEN_SET"
log "Avaliando a busca com o golden set (portão)"
com_ambiente "$RUNTIME/worker.env" "$PY" -m deploy.avaliar_busca --golden-set "$GOLDEN_SET" \
    --saida "$DESTINO/avaliacao.json" > /dev/null || abortar "a avaliação da busca falhou"
METRICAS="$("$PY" -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1]))["metricas"]))' "$DESTINO/avaliacao.json")"
log "Métricas da release nova: $METRICAS"

# A campeã é reavaliada agora, no mesmo banco: o avaliador é o da release nova; o código, o da campeã.
PORTAO_ARGS=(portao --avaliacao "$DESTINO/avaliacao.json" --piso "$PISO" --margem "$MARGEM")
CAMPEA="$("$PY" "$RUNTIME/bin/releases.py" campea)"
if [[ -n "$CAMPEA" && "$RELEASES/$CAMPEA" != "$DESTINO" ]]; then
    log "Reavaliando a campeã $CAMPEA no mesmo banco"
    # shellcheck source=/dev/null
    if (set -a; . "$RUNTIME/worker.env"; set +a; cd "$RELEASES/$CAMPEA" && \
        PYTHONPATH="$RELEASES/$CAMPEA" "$PY" "$DESTINO/deploy/avaliar_busca.py" --golden-set "$GOLDEN_SET" \
            --saida "$DESTINO/avaliacao_campea.json" > /dev/null); then
        PORTAO_ARGS+=(--avaliacao-campea "$DESTINO/avaliacao_campea.json")
        log "Métricas da campeã agora: $("$PY" -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1]))["metricas"]))' "$DESTINO/avaliacao_campea.json")"
    else
        log "AVISO: a reavaliação da campeã falhou; o portão usa a nota registrada dela"
    fi
fi
APROVADA=1
DECISAO="$("$PY" "$RUNTIME/bin/releases.py" "${PORTAO_ARGS[@]}")" || APROVADA=0
campo() { "$PY" -c 'import json, sys; v = json.loads(sys.argv[1]).get(sys.argv[2]); print("" if v is None else v)' "$1" "$2"; }
log "Portão ($(campo "$DECISAO" metrica), piso $PISO, margem $MARGEM): $(campo "$DECISAO" motivo)"
if [[ $APROVADA == 0 ]]; then
    registrar barrada "$(campo "$DECISAO" motivo)"
    falhar "release barrada pelo portão (nada mudou no ar; continua a release $ANTERIOR)"
fi
PROMOVER=0
[[ "$(campo "$DECISAO" promover)" == "True" ]] && PROMOVER=1

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
registrar implantada "$(campo "$DECISAO" motivo)" "$PROMOVER"
log "Release $CURTO no ar"

# 7. Limpeza: mantém as $MANTER releases mais recentes, a atual, a anterior e a campeã ---------------
CAMPEA_DIR="$RELEASES/$("$PY" "$RUNTIME/bin/releases.py" campea)"
mapfile -t ANTIGAS < <(ls -1dt "$RELEASES"/*/ 2>/dev/null | sed 's#/$##' | tail -n +"$((MANTER + 1))")
for dir in "${ANTIGAS[@]}"; do
    [[ "$dir" == "$DESTINO" || "$dir" == "$ANTERIOR_DIR" || "$dir" == "$CAMPEA_DIR" ]] && continue
    log "Removendo a release antiga $(basename "$dir")"
    rm -rf "$dir"
done

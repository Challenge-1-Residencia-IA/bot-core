#!/usr/bin/env bash
# Preparação única da VM para o deploy automatizado. Pode ser rodado de novo sem efeito colateral.
#
# - Espelho do repositório em ~/bot-core-runtime/repo.git (de onde as releases são extraídas).
# - Ferramentas de deploy em ~/bot-core-runtime/bin (comando SSH, reversão e registro de releases).
# - Arquivo de migração ~/bot-core-runtime/migracao.env, com a credencial que altera o esquema; as
#   credenciais do worker e do coletor não têm essa permissão.
# - Chave pública do GitHub Actions em ~/.ssh/authorized_keys, presa ao comando forçado.
#
# Uso (na VM, a partir de uma release):
#   deploy/preparar_vm.sh [--url-migracao-de <arquivo .env com DATABASE_URL do dono>] [--chave <arquivo .pub>]
set -euo pipefail

BASE="${BOT_DEPLOY_BASE:-$HOME}"
RUNTIME="$BASE/bot-core-runtime"
REPO_URL="${BOT_DEPLOY_REPO:-https://github.com/Challenge-1-Residencia-IA/bot-core.git}"
AQUI="$(cd "$(dirname "$0")" && pwd)"
ORIGEM_MIGRACAO=""
CHAVE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url-migracao-de) ORIGEM_MIGRACAO="$2"; shift 2 ;;
        --chave) CHAVE="$2"; shift 2 ;;
        *) echo "opção desconhecida: $1"; exit 2 ;;
    esac
done

mkdir -p "$BASE/bot-core-releases" "$RUNTIME/bin"

if [[ ! -d "$RUNTIME/repo.git" ]]; then
    git clone --quiet --mirror "$REPO_URL" "$RUNTIME/repo.git"
    echo "Espelho criado em $RUNTIME/repo.git"
fi
git -C "$RUNTIME/repo.git" fetch --quiet --prune origin

for ferramenta in comando_ssh.sh reverter.sh releases.py fumaca.py painel.sh; do
    install -m 755 "$AQUI/$ferramenta" "$RUNTIME/bin/$ferramenta"
done
echo "Ferramentas instaladas em $RUNTIME/bin"

if [[ -n "$ORIGEM_MIGRACAO" ]]; then
    linha="$(grep -m1 '^DATABASE_URL=' "$ORIGEM_MIGRACAO")" || { echo "sem DATABASE_URL em $ORIGEM_MIGRACAO"; exit 1; }
    (umask 077; printf '%s\n' "$linha" > "$RUNTIME/migracao.env")
    echo "Credencial de migração gravada em $RUNTIME/migracao.env (modo 600)"
fi
[[ -f "$RUNTIME/migracao.env" ]] || echo "ATENÇÃO: falta $RUNTIME/migracao.env (use --url-migracao-de)"

if [[ -n "$CHAVE" ]]; then
    publica="$(tr -d '\n' < "$CHAVE")"
    restricoes="command=\"$RUNTIME/bin/comando_ssh.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty"
    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
    touch "$HOME/.ssh/authorized_keys" && chmod 600 "$HOME/.ssh/authorized_keys"
    if grep -qF "$publica" "$HOME/.ssh/authorized_keys"; then
        echo "A chave do deploy já está autorizada"
    else
        printf '%s %s\n' "$restricoes" "$publica" >> "$HOME/.ssh/authorized_keys"
        echo "Chave do deploy autorizada só para o comando forçado"
    fi
fi

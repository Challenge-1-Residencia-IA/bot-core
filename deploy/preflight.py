"""Verificação de dependências antes de iniciar o worker.

Redis e banco são obrigatórios: sem eles o worker não consegue receber jobs nem buscar evidências. O
Ollama roda no PC do mantenedor, acessado pelo Tailscale, e pode estar desligado ou lento; nesse caso
o worker sobe mesmo assim e responde que o modelo está fora do ar, enquanto as recusas dos guardrails
continuam funcionando. `--exigir-ollama` torna o Ollama obrigatório (uso manual).
"""
import argparse
import sys

import redis

from app import config, llm
from app.db.base_checagens import conectar


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exigir-ollama", action="store_true")
    args = parser.parse_args()
    redis.Redis.from_url(config.REDIS_URL).ping()
    with conectar() as conn:
        conn.execute("SELECT id FROM documentos LIMIT 1").fetchone()
    servidores = llm.situacao(timeout=15)
    for s in servidores:
        print(f"Ollama {s['url']} ({s['modelo']}): {'ok' if s['ok'] else 'indisponível'} {s['erro']}".rstrip())
    if any(s["ok"] for s in servidores):
        print("Redis, banco e Ollama disponíveis")
        return 0
    if args.exigir_ollama:
        print("Nenhum servidor do Ollama disponível")
        return 1
    print("Redis e banco disponíveis; AVISO: nenhum servidor do Ollama disponível "
          "(o worker sobe e responde que o modelo está fora do ar)")
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Verificação de dependências antes de iniciar o worker."""
from app import llm, config
from app.db.base_checagens import conectar
import redis


def main():
    redis.Redis.from_url(config.REDIS_URL).ping()
    with conectar() as conn:
        conn.execute("SELECT id FROM documentos LIMIT 1").fetchone()
    if not llm.disponivel():
        raise RuntimeError("Ollama ou modelo indisponível")
    print("Redis, banco e Ollama disponíveis")


if __name__ == '__main__':
    main()

"""Executar na VM com o venv administrativo. Não imprime credenciais."""
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote
import psycopg
from psycopg import sql
from dotenv import dotenv_values


def main():
    os.umask(0o077)
    root = Path.home()
    source = dotenv_values(root / 'bot-core/.env')
    original = source['DATABASE_URL']
    destination = root / 'bot-core-runtime'
    for role, filename, privileges in (
        ('bot_runtime', 'worker.env', 'SELECT'),
        ('bot_coletor', 'coletor.env', 'SELECT, INSERT, UPDATE, DELETE'),
    ):
        target = destination / filename
        if target.exists():
            raise RuntimeError(f'{filename} já existe; não substituído')
        password = secrets.token_urlsafe(36)
        with psycopg.connect(original) as conn:
            if conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                raise RuntimeError(f'Role {role} já existe; não alterada')
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(
                sql.Identifier(role), sql.Literal(password)))
            database = conn.execute('SELECT current_database()').fetchone()[0]
            conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database), sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT ' + privileges + ' ON documentos, trechos, boatos_conhecidos TO {}').format(sql.Identifier(role)))
            if role == 'bot_coletor':
                conn.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(role)))
        url = urlsplit(original)
        authority = quote(role) + ':' + quote(password) + '@' + url.netloc.rsplit('@', 1)[-1]
        database_url = urlunsplit((url.scheme, authority, url.path, url.query, url.fragment))
        values = {'DATABASE_URL': database_url,
                  'OLLAMA_URL': source.get('OLLAMA_URL', 'http://localhost:11434'),
                  'OLLAMA_MODEL': source.get('OLLAMA_MODEL', 'qwen2.5:latest'),
                  'REDIS_URL': 'redis://127.0.0.1:6379/0', 'PYTHONUNBUFFERED': '1'}
        if 'GUARDRAILS_NEMO' in source:
            values['GUARDRAILS_NEMO'] = source['GUARDRAILS_NEMO']
        target.write_text(''.join(f'{k}="{v}"\n' for k,v in values.items()))
        target.chmod(0o600)
        print(f'{role}: credencial criada e gravada em {filename}')


if __name__ == '__main__':
    main()

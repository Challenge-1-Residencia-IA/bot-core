from neon_serverless import neon

# Use a URL SEM o "-pooler" (a versão direta)
DATABASE_URL = os.getenv("DATABASE_URL")

print("1. Tentando conectar ao Neon via HTTPS...")

try:
    # O Neon gerencia a conexão via HTTPS (porta 443)
    sql = neon(DATABASE_URL)
    
    # Executa a query. Note a sintaxe: $1, $2 para parâmetros
    rows = sql("SELECT version();")
    
    print("2. Conectado com sucesso!")
    print(f"Versão do Postgres: {rows[0]['version'][:50]}...")
    
    # Testa o pgvector
    vector_check = sql("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
    print(f"pgvector ativo, versão: {vector_check[0]['extversion']}")
    
    print("\n[OK] Tudo funcionando!")
    
except Exception as e:
    print(f"ERRO: {e}")
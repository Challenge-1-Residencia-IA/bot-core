from sentence_transformers import SentenceTransformer
from neon_serverless import neon
import json

# ===================================================================
# CONFIGURACAO
# ===================================================================
DATABASE_URL = os.getenv("DATABASE_URL")
MODELO_EMBEDDING = "all-MiniLM-L6-v2"
# ===================================================================

print("1. Carregando o modelo de embeddings...")
modelo = SentenceTransformer(MODELO_EMBEDDING)

print("2. Conectando ao Neon...")
sql = neon(DATABASE_URL)
print("   Conectado!")

# Mensagem de teste
mensagem = "A Terra é plana."

print(f"\n3. Buscando textos similares para: '{mensagem}'")
embedding_mensagem = modelo.encode(mensagem).tolist()

# Buscar os 5 textos mais similares no banco
resultados = sql(
    """
    SELECT texto, label, embedding <=> $1::vector AS distancia
    FROM padroes_fake_news
    ORDER BY distancia
    LIMIT 5
    """,
    [json.dumps(embedding_mensagem)]
)

print(f"\n   Encontrados {len(resultados)} textos similares:\n")
for i, r in enumerate(resultados, 1):
    print(f"   [{i}] (distancia: {r['distancia']:.4f}, label: {r['label']})")
    print(f"       {r['texto'][:150]}...")
    print()
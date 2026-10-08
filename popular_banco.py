import pandas as pd
from sentence_transformers import SentenceTransformer
from neon_serverless import neon
import json

# ===================================================================
# CONFIGURACAO
# ===================================================================
DATABASE_URL = os.getenv("DATABASE_URL")
MODELO_EMBEDDING = "all-MiniLM-L6-v2"
ARQUIVO_CSV = "dataset_fake_br.csv"
# ===================================================================

print("1. Carregando o modelo de embeddings...")
modelo = SentenceTransformer(MODELO_EMBEDDING)

print("2. Lendo o dataset...")
df = pd.read_csv(ARQUIVO_CSV)
print(f"   Total de exemplos: {len(df)}")
print(f"   Colunas: {df.columns.tolist()}")
print(f"   Primeiras linhas:\n{df.head(2)}")

print("\n3. Conectando ao Neon...")
sql = neon(DATABASE_URL)
print("   Conectado!")

print("\n4. Gerando embeddings e inserindo no banco...")
print("   (Isso pode demorar alguns minutos)")

# Processar em lotes para não sobrecarregar
lote_tamanho = 100
total = len(df)

for i in range(0, total, lote_tamanho):
    lote = df.iloc[i:i+lote_tamanho]
    
    textos = lote["texto"].tolist() if "texto" in lote.columns else lote.iloc[:, 0].tolist()
    labels = lote["label"].tolist() if "label" in lote.columns else ["desconhecido"] * len(lote)
    
    # Gerar embeddings
    embeddings = modelo.encode(textos, show_progress_bar=False)
    
    # Inserir no banco
    for texto, label, embedding in zip(textos, labels, embeddings):
        try:
            sql(
                """
                INSERT INTO padroes_fake_news (texto, label, embedding)
                VALUES ($1, $2, $3)
                """,
                [
                    texto[:5000],  # Limitar tamanho
                    str(label),
                    json.dumps(embedding.tolist())
                ]
            )
        except Exception as e:
            print(f"   [ERRO] Falha ao inserir: {e}")
            continue
    
    print(f"   Inseridos {min(i+lote_tamanho, total)}/{total} exemplos")

print("\n[OK] Dataset inserido no banco com sucesso!")
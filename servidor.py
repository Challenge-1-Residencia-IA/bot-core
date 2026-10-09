import os
import json
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from busca_web import buscar_na_web, analisar_com_ollama
from neon_serverless import neon

# Carregar variáveis de ambiente do arquivo .env
load_dotenv()

app = FastAPI(
    title="Copiloto Investigativo",
    description="API de apoio à avaliação de informações com IA",
    version="0.1.0"
)

# ===================================================================
# CONFIGURACAO DO BANCO DE DADOS (NEON)
# ===================================================================
DATABASE_URL = os.getenv("DATABASE_URL")
sql = neon(DATABASE_URL)
# ===================================================================

class Mensagem(BaseModel):
    texto: str

@app.get("/")
def raiz():
    return {
        "status": "Backend do Copiloto funcionando!",
        "versao": "0.1.0"
    }

@app.post("/analisar")
def analisar(mensagem: Mensagem):
    print(f"\n[INFO] Recebida mensagem: {mensagem.texto}")
    
    # 1. Buscar evidências na web
    print("[INFO] Buscando evidencias na web...")
    evidencias = buscar_na_web(mensagem.texto)
    print(f"[INFO] Encontradas {len(evidencias)} evidencias")
    
    # 2. Analisar com IA
    print("[INFO] Analisando com IA...")
    analise = analisar_com_ollama(mensagem.texto, evidencias, modelo="qwen2.5:7b")
    
    # 3. Salvar no banco de dados
    print("[INFO] Salvando no banco de dados...")
    try:
        sql(
            """
            INSERT INTO analises (mensagem, evidencias, analise, modelo)
            VALUES ($1, $2, $3, $4)
            """,
            [
                mensagem.texto,
                json.dumps(evidencias),
                analise,
                "qwen2.5:7b"
            ]
        )
        print("[INFO] Salvo com sucesso!")
    except Exception as e:
        print(f"[ERRO] Falha ao salvar no banco: {e}")
    
    return {
        "mensagem": mensagem.texto,
        "total_evidencias": len(evidencias),
        "analise": analise
    }
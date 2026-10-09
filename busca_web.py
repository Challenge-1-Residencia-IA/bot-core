import requests
import json
from ddgs import DDGS
from bs4 import BeautifulSoup

# ===================================================================
# CONFIGURACAO DO BANCO VETORIAL
# ===================================================================
DATABASE_URL = os.getenv("DATABASE_URL")
MODELO_EMBEDDING = "all-MiniLM-L6-v2"

_modelo_embedding = None
_sql = None
# ===================================================================

def buscar_na_web(afirmacao, max_resultados=3):
    """Busca na web informacoes sobre uma afirmacao."""
    resultados = []
    
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(f"{afirmacao} fatos ciencia", max_results=max_resultados):
                resultados.append({
                    "titulo": r["title"],
                    "link": r["href"],
                    "trecho": extrair_texto_pagina(r["href"]),
                    "tipo": "geral"
                })
    except Exception as e:
        print(f"[AVISO] Busca 1 falhou: {e}")
    
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(f"{afirmacao} e falso", max_results=2):
                resultados.append({
                    "titulo": r["title"],
                    "link": r["href"],
                    "trecho": extrair_texto_pagina(r["href"]),
                    "tipo": "contraria"
                })
    except Exception as e:
        print(f"[AVISO] Busca 2 falhou: {e}")
    
    return resultados

def extrair_texto_pagina(url):
    """Extrai o texto principal de uma pagina web."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resposta = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resposta.text, "html.parser")
        
        for script in soup(["script", "style", "nav", "footer", "header", "aside"]):
            script.decompose()
        
        texto = soup.get_text()
        linhas = (linha.strip() for linha in texto.splitlines())
        texto_limpo = "\n".join(linha for linha in linhas if linha)
        
        return texto_limpo[:2000]
    except Exception as e:
        return f"Erro ao extrair: {e}"

def _inicializar_busca_vetorial():
    """Inicializa o modelo e a conexao (so uma vez)."""
    global _modelo_embedding, _sql
    if _modelo_embedding is None:
        _modelo_embedding = SentenceTransformer(MODELO_EMBEDDING)
    if _sql is None:
        _sql = neon(DATABASE_URL)

def buscar_padroes_similares(mensagem, limite=3):
    """Busca no banco vetorial os textos mais similares a mensagem."""
    _inicializar_busca_vetorial()
    
    embedding = _modelo_embedding.encode(mensagem).tolist()
    
    resultados = _sql(
        """
        SELECT texto, label, embedding <=> $1::vector AS distancia
        FROM padroes_fake_news
        ORDER BY distancia
        LIMIT $2
        """,
        [json.dumps(embedding), limite]
    )
    
    return resultados

def analisar_com_ollama(mensagem, evidencias, padroes_similares=None, modelo="qwen2.5:7b"):
    """Envia a mensagem, evidencias e padroes para o Ollama analisar."""
    
    with open("prompt-analise.txt", "r", encoding="utf-8") as f:
        prompt = f.read()
    
    if evidencias:
        texto_evidencias = "\n\n".join([
            f"[Evidencia {i+1} - {e['titulo']}]\n{e['trecho']}\nLink: {e['link']}"
            for i, e in enumerate(evidencias)
        ])
    else:
        texto_evidencias = "Nenhuma evidencia encontrada na busca."
    
    if padroes_similares:
        texto_padroes = "\n\n".join([
            f"[Padrao {i+1} - Similaridade: {p['distancia']:.2f}]\n{p['texto'][:300]}"
            for i, p in enumerate(padroes_similares)
        ])
    else:
        texto_padroes = "Nenhum padrao similar encontrado no banco."
    
    prompt_completo = f"""{prompt}

MENSAGEM:
{mensagem}

EVIDENCIAS ENCONTRADAS NA WEB:
{texto_evidencias}

PADROES SIMILARES NO BANCO DE FAKE NEWS:
{texto_padroes}
"""
    
    resposta = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": modelo,
            "prompt": prompt_completo,
            "stream": False
        }
    )
    
    return resposta.json()["response"]

if __name__ == "__main__":
    mensagem_teste = "A Terra e plana."
    
    print("Buscando evidencias na web...")
    evidencias = buscar_na_web(mensagem_teste)
    print(f"Encontradas {len(evidencias)} evidencias")
    
    print("\nBuscando padroes similares no banco...")
    padroes = buscar_padroes_similares(mensagem_teste)
    print(f"Encontrados {len(padroes)} padroes")
    
    print("\nAnalisando com IA...")
    analise = analisar_com_ollama(mensagem_teste, evidencias, padroes)
    
    print("\n=== ANALISE ===")
    print(analise)
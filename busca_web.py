import requests
from ddgs import DDGS
from bs4 import BeautifulSoup

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

def analisar_com_ollama(mensagem, evidencias, modelo="qwen2.5:7b"):
    """Envia a mensagem e evidencias para o Ollama analisar."""
    
    with open("prompt-analise.txt", "r", encoding="utf-8") as f:
        prompt = f.read()
    
    if evidencias:
        texto_evidencias = "\n\n".join([
            f"[Evidencia {i+1} - {e['titulo']}]\n{e['trecho']}\nLink: {e['link']}"
            for i, e in enumerate(evidencias)
        ])
    else:
        texto_evidencias = "Nenhuma evidencia encontrada na busca."
    
    prompt_completo = f"""{prompt}

MENSAGEM:
{mensagem}

EVIDENCIAS ENCONTRADAS NA WEB:
{texto_evidencias}
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
import json
from difflib import SequenceMatcher
from busca_web import buscar_na_web, analisar_com_ollama

# ===================================================================
# CONFIGURACAO
# ===================================================================
MODELOS = [
    "qwen2.5:7b",
    "qwen2.5:3b",
    "llama3.1:8b",
]
# ===================================================================

def similaridade(texto1, texto2):
    """Calcula a similaridade entre dois textos (0 a 1)."""
    return SequenceMatcher(None, texto1, texto2).ratio()

def contar_palavras_comuns(texto1, texto2):
    """Conta quantas palavras do texto1 aparecem no texto2."""
    palavras1 = set(texto1.lower().split())
    palavras2 = set(texto2.lower().split())
    return len(palavras1.intersection(palavras2))

# 1. Carregar o gabarito
with open("gabarito.json", "r", encoding="utf-8") as f:
    gabaritos = json.load(f)

# 2. Rodar os testes para cada mensagem
resultados = []

for item in gabaritos:
    mensagem = item["mensagem"]
    esperado = item["resposta_esperada"]
    
    print("=" * 70)
    print(f"MENSAGEM: {mensagem}")
    print("=" * 70)
    
    # Buscar evidencias UMA VEZ (mesmas evidencias para todos os modelos)
    evidencias = buscar_na_web(mensagem)
    
    # Gerar resposta com cada modelo
    respostas = {}
    for modelo in MODELOS:
        print(f"\nGerando resposta com {modelo}...")
        resposta = analisar_com_ollama(mensagem, evidencias, modelo=modelo)
        respostas[modelo] = resposta
    
    # Mostrar as respostas e comparar com o gabarito
    print("\n" + "-" * 70)
    print("RESPOSTA ESPERADA (GABARITO):")
    print("-" * 70)
    print(esperado)
    
    for modelo, resposta in respostas.items():
        sim = similaridade(esperado, resposta)
        palavras = contar_palavras_comuns(esperado, resposta)
        
        print("\n" + "-" * 70)
        print(f"RESPOSTA DO MODELO: {modelo}")
        print("-" * 70)
        print(resposta)
        print(f"\n[{modelo}] Similaridade: {sim*100:.2f}% | Palavras comuns: {palavras}")
        
        resultados.append({
            "mensagem": mensagem,
            "resposta_esperada": esperado,
            "modelo": modelo,
            "resposta": resposta,
            "similaridade": round(sim * 100, 2),
            "palavras_comuns": palavras,
        })

# 3. Salvar os resultados
with open("resultado_comparacao.json", "w", encoding="utf-8") as f:
    json.dump(resultados, f, ensure_ascii=False, indent=2)

print("\n\n[OK] Resultados salvos em 'resultado_comparacao.json'")
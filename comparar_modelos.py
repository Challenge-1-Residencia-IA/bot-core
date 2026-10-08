from busca_web import buscar_na_web, analisar_com_ollama

# ===================================================================
# CONFIGURAÇÃO
# ===================================================================
MENSAGEM_TESTE = "A Terra é plana?"
MODELO_A = "qwen2.5:7b"
MODELO_B = "qwen2.5:3b"
# ===================================================================

print("=" * 70)
print(f"MENSAGEM: {MENSAGEM_TESTE}")
print("=" * 70)

# Passo 1: Buscar evidencias UMA VEZ (mesmas evidencias para os dois)
print("\n[1/3] Buscando evidencias na web...")
evidencias = buscar_na_web(MENSAGEM_TESTE)
print(f"      Encontradas {len(evidencias)} evidencias")

# Passo 2: Gerar resposta com o modelo A (3B)
print(f"\n[2/3] Gerando resposta com {MODELO_A}...")
resposta_a = analisar_com_ollama(MENSAGEM_TESTE, evidencias, modelo=MODELO_A)

# Passo 3: Gerar resposta com o modelo B (7B)
print(f"\n[3/3] Gerando resposta com {MODELO_B}...")
resposta_b = analisar_com_ollama(MENSAGEM_TESTE, evidencias, modelo=MODELO_B)

# Passo 4: Mostrar os resultados
print("\n" + "=" * 70)
print(f"RESPOSTA DO MODELO: {MODELO_A}")
print("=" * 70)
print(resposta_a)

print("\n" + "=" * 70)
print(f"RESPOSTA DO MODELO: {MODELO_B}")
print("=" * 70)
print(resposta_b)

# Passo 5: Salvar em um arquivo para enviar aos colegas
with open("comparacao_modelos.txt", "w", encoding="utf-8") as f:
    f.write(f"COMPARACAO DE MODELOS\n")
    f.write(f"Mensagem testada: {MENSAGEM_TESTE}\n")
    f.write("=" * 70 + "\n\n")
    f.write(f"MODELO: {MODELO_A}\n")
    f.write("-" * 70 + "\n")
    f.write(resposta_a + "\n\n")
    f.write(f"MODELO: {MODELO_B}\n")
    f.write("-" * 70 + "\n")
    f.write(resposta_b + "\n")

        # Mostrar as respostas completas no terminal
    print("\n" + "=" * 70)
    print(f"RESPOSTA ESPERADA (GABARITO):")
    print("=" * 70)
    print(esperado)
    
    print("\n" + "=" * 70)
    print(f"RESPOSTA DO MODELO: {MODELO_A}")
    print("=" * 70)
    print(resposta_a)
    
    print("\n" + "=" * 70)
    print(f"RESPOSTA DO MODELO: {MODELO_B}")
    print("=" * 70)
    print(resposta_b)

print("\n\n[OK] Resultado salvo no arquivo 'comparacao_modelos.txt'")
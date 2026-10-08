from datasets import load_dataset

print("Baixando dataset Fake.br...")
dataset = load_dataset("vzani/corpus-fake-br", split="train")

print(f"\nDataset baixado! Total de exemplos: {len(dataset)}")
print(f"Colunas: {dataset.column_names}")
print(f"\nPrimeiro exemplo:")
print(dataset[0])

# Salvar localmente para não precisar baixar de novo
dataset.to_csv("dataset_fake_br.csv", index=False)
print("\n[OK] Dataset salvo em 'dataset_fake_br.csv'")
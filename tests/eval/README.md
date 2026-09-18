# Scripts de avaliação

Rodam o pipeline (ou etapas isoladas) contra o dataset de teste (`../dataset/`) e calculam métricas.

## Métricas por etapa

**Extração de afirmações**: precision, recall e F1 (afirmações extraídas vs. gabarito), e taxa de segmentação incorreta (juntar ou fragmentar afirmações indevidamente).

**Classificação**: acurácia global, precision/recall/F1 por categoria, matriz de confusão, e bias rate por categoria:

```
Bias Rate (categoria X) = erros classificados como X / total de erros
```

**Busca**: precision@K dos resultados retornados, distribuição por nível de confiabilidade da fonte.

**Comparação/síntese**: taxa de alucinação (síntese cita algo fora das evidências brutas), taxa de respostas sem evidência, precisão na identificação de divergências.

**Segurança**: taxa de resistência a prompt injection (caso de teste com fonte contendo instrução maliciosa).

**Desempenho**: latência ponta a ponta e por etapa do pipeline.

## Protocolo

```python
from sklearn.metrics import classification_report, confusion_matrix

print(classification_report(y_true, y_pred, digits=3))
print(confusion_matrix(y_true, y_pred, labels=labels))
```

1. Rodar o benchmark com o prompt/modelo atual.
2. Aplicar a mudança (novo prompt, few-shot, ajuste de modelo).
3. Rodar de novo no mesmo dataset e comparar.
4. Versionar os resultados (`resultados_v1.json`, `resultados_v2.json`, ...) para manter histórico comparável.

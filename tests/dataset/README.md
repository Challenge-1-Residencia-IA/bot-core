# Dataset de teste

Mensagens de teste com gabarito, usadas para avaliar cada etapa do pipeline isoladamente (ver `../eval/`).

## Fontes

O dataset interno (~30 mensagens, gabarito escrito pela equipe) é o ponto de partida, mas é pequeno e tem viés próprio. Datasets públicos recomendados para enriquecê-lo:

| Dataset | Descrição | Uso |
|---|---|---|
| **Fake.br Corpus** | 7.200 notícias em PT-BR (3.600 verdadeiras + 3.600 falsas) | Few-shot examples + teste de acurácia fato-vs-falso |
| **FACTCK.BR** | 1.309 checagens de fato de agências brasileiras (Aos Fatos, Agência Lupa, Truco) | Gabarito profissional real, independente do time |
| **Sátira dedicado (a construir)** | Veículos de humor declarado vs. desinformação disfarçada de piada | Corrige o viés de classificar desinformação absurda como sátira |
| **CLEF CheckThat! Lab** | Claim detection / check-worthiness, multilíngue | Validar extração de afirmações em mensagens com várias afirmações |

## Como usar

Datasets públicos **não são versionados aqui** (arquivos grandes, ver `.gitignore` na raiz) — baixe da fonte original e coloque localmente, ou aponte os scripts de `../eval/` para o caminho baixado.

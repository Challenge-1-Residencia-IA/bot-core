# search

Busca de evidências na web e extração de conteúdo das páginas retornadas.

## Provedor de busca: Tavily

Tavily é o provedor padrão (free tier: 1.000 créditos/mês; busca básica = 1 crédito, avançada = 2). Alternativas avaliadas:

- **DuckDuckGo (`ddgs`)**: gratuito, sem API key, mas é scraping não oficial (sujeito a bloqueio/instabilidade). Já usado no protótipo da Sprint 1.
- **SearXNG**: open source, self-hosted, ilimitado, mas não entrega ranking otimizado para LLM como a Tavily. Para equiparar, seria preciso: extrair conteúdo completo das páginas (trafilatura/BeautifulSoup), calcular score de relevância via embeddings + pgvector, e aplicar peso por confiabilidade de domínio — tudo isso já são peças previstas no pipeline, então a troca não exigiria novos componentes, só torná-los obrigatórios.

## Critério para decidir o que indexar no pgvector

Nem todo resultado retornado pela busca deve virar evidência indexada:

1. **Relevância semântica** — descarta resultados pouco relacionados à afirmação (similaridade de embedding).
2. **Hierarquia de fonte** — prioriza fontes primárias > institucionais > jornalísticas > secundárias > não verificadas. Um domínio conhecido não é, por si só, garantia de confiabilidade.

## Cache de evidências: nunca "pular a busca"

Reusar evidências antigas sem checar validade é arriscado — fatos mudam com o tempo (uma teoria pode se confirmar depois, uma informação verdadeira pode ficar desatualizada). Regras:

- **TTL diferenciado por tipo de afirmação**: fato histórico bem estabelecido tolera TTL longo; previsão, hipótese, alegação científica/médica/política/econômica e informação atual **nunca reusam cache puro**.
- Um "cache hit" dispara uma busca leve de "há algo novo?" antes de decidir reusar — nunca substitui a busca por completo.
- Evidências nunca são sobrescritas — cada verificação nova soma ao histórico com timestamp (ver `app/db/README.md`).
- A data da última verificação é sempre exposta ao usuário na resposta final.

# db

Models, migrations e queries do PostgreSQL + pgvector.

## O que é armazenado

**Dados relacionais**:
- Afirmações já processadas (texto, tipo, timestamp)
- Evidências: afirmação relacionada, fonte, data, trecho relevante, posição (favorável/contrária/inconclusiva), contexto, nível de confiança
- Estado da conversa por `chat_id` (usado pelo checkpointer do LangGraph, ver `app/graph/README.md`)
- Logs de rastreabilidade (quais fontes foram usadas em cada análise)

**Vetores semânticos (pgvector)**:
- Embeddings do conteúdo extraído das páginas retornadas pela busca (texto completo, não o snippet curto) — usados para ranking, comparação de evidências e detecção de fontes que reproduzem a mesma origem ("efeito eco")

## Privacidade

A mensagem original do usuário (pode conter dado pessoal) não deve ser retida além do necessário para processar a análise. O conteúdo de evidências (conteúdo público da web) não tem essa mesma restrição.

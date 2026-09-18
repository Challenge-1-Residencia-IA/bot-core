# graph

Orquestração do pipeline via LangGraph: `state.py` define o estado compartilhado entre os nodes (`nodes/`), que implementam cada etapa (extração, classificação, busca, ranking, comparação, síntese, crítica).

## Continuidade da conversa

Quando o usuário continua a conversa (pede explicação, envia nova fonte, contesta a análise), o grafo **não** deve rodar o pipeline inteiro de novo por padrão.

- Um node de classificação de intenção roda antes de qualquer outra etapa em toda mensagem nova, e decide via edge condicional qual caminho seguir:

| Intenção | Comportamento |
|---|---|
| Nova afirmação/mensagem não relacionada | Roda o pipeline completo do zero |
| Pedido de explicação | Não busca de novo — reusa evidências já no estado |
| Nova fonte enviada pelo usuário | Pula extração/busca, vai direto para comparação de evidências |
| Contestação da análise | Investiga o argumento específico do usuário, pode disparar busca adicional focada |

- Usar o **checkpointer do LangGraph**, associando o estado da conversa a um `thread_id` = `chat_id` do Telegram, para que o estado (afirmações, evidências, fontes já comparadas) persista entre mensagens em vez de recomeçar em branco.

Ainda não implementado — este README documenta a decisão para quem for implementar o node de intenção e o checkpointer.

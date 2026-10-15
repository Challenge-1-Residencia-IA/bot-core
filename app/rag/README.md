# rag

Busca de evidências, seleção de trechos literais e síntese da tutora. São funções puras, chamadas pelos nós `busca` e `sintese` do grafo (`app/graph/nodes/`). Rede, LLM e blindagem são injetáveis, então os testes (`tests/rag/`) rodam sem rede e sem Ollama.

| Módulo | Função principal | Papel |
|---|---|---|
| `base.py` | `buscar_na_base(afirmacao, mensagem=None, *, limite=5, excluir_urls=()) -> list[Evidencia]` | Busca híbrida na base de checagens (pgvector + `tsvector`, RRF): boato parente primeiro, depois trechos |
| `embeddings.py` | `modelo_padrao().consulta(texto)` / `.passagens(textos)` | Embeddings E5 (768 dim, CPU) da base |
| `fatiamento.py` | `fatiar(texto)` | Trechos de até 180 palavras para indexar |
| `busca.py` | `buscar_evidencias(afirmacao, *, max_resultados=6, provedor=None, leitor=None, ...) -> list[Evidencia]` | Checagens publicadas primeiro, depois a web geral (IA-03) |
| `trechos.py` | `selecionar_trechos(afirmacao, texto, *, limite=1, pontuador=None) -> list[str]` | Trechos literais de cada página |
| `sintese.py` | `sintetizar(afirmacao, evidencias, *, mensagem=None, gerar=None, blindar=None) -> RespostaTutora` | Técnicas de manipulação, citações literais, leitura lateral e lista de fontes |

## Busca na base de checagens (`base.py`)

O nó de busca consulta a base antes da web (`app/graph/nodes/busca.py`). A web entra quando a base não traz nenhuma checagem, quando a checagem mais recente encontrada tem mais de 6 meses (`BASE_CHECAGEM_RECENTE_MESES`) ou quando nenhuma checagem tem data. É a regra de *freshness*: um boato antigo pode voltar quando o assunto já mudou. A resposta sempre informa a data da checagem mais recente encontrada; se ela for antiga, o aviso diz que o assunto pode ter mudado, a base fica com até 3 vagas e a web completa o resto. Se `DATABASE_URL` estiver vazia ou o banco falhar, a busca segue só pela web.

- **Boato parente (claim matching).** A mensagem recebida é comparada com `boatos_conhecidos`; um boato parecido leva à checagem ligada a ele, que vem em primeiro lugar. O texto do boato nunca vira evidência.
- **Trechos.** A afirmação extraída é comparada com os trechos de checagens e notícias. Um documento aparece uma vez, com os 3 trechos mais parecidos (na ordem do texto) para a síntese escolher citações.
- **Busca híbrida** (*hybrid search*): cada busca junta a similaridade vetorial (HNSW, cosseno) e a busca textual em português (`tsvector`, palavras unidas por OU), fundidas pela Reciprocal Rank Fusion com `k = 60`. A busca iterativa do pgvector 0.8 (`hnsw.iterative_scan`) evita perder resultados por causa dos filtros.
- **Limiares** de similaridade vetorial: `BASE_LIMIAR_TRECHO` (padrão 0,82) e `BASE_LIMIAR_BOATO` (padrão 0,88). São provisórios e precisam ser calibrados com o golden set: o E5 concentra as similaridades entre 0,7 e 0,9.
- **`excluir_urls`** esconde documentos numa análise; a avaliação usa isso para simular checagens fora da base (IA-07) sem manter outro índice. No grafo, chega por `analisar(pedido, excluir_urls=[...])`.

## Origem

Código portado do repositório `experimentos` (branch `entrega/validacao-modelo`): `rag/web.py` (busca com `ddgs`, leitura de páginas com `trafilatura`, proteção contra SSRF), `rag/relevance.py` (relevância lexical) e `rag/tutor.py` (`validate_answer`, `safe_fallback`). O `splitter.py` não foi portado: os trechos agora são fatias contíguas do texto original, o que garante que a citação existe na página.

## Decisões

- **Provedor de busca injetável** (padrão Strategy / *ports and adapters*). `ProvedorDDGS` é o padrão porque é gratuito e já era usado no protótipo. Trocar por Tavily ou SearXNG é escrever outra classe com `__call__(consulta, max_resultados) -> list[ResultadoBusca]`. O leitor de páginas (`ler_pagina`) também é injetável.
- **Checagens primeiro.** São duas consultas: `"<afirmação> checagem de fatos"` e a afirmação pura. Resultados de agências de checagem (Lupa, Aos Fatos, Fato ou Fake, Boatos.org, E-farsas, Estadão Verifica, Comprova, AFP Checamos, UOL Confere, Reuters Fact Check) vêm antes e **não são descartados por relevância**. Publicadores que não autorizam coleta automatizada (Aos Fatos e Lupa, em `agencialupa.org`) não são baixados: entram com o resumo do buscador (`lida=False`). A capa da seção de checagem não conta como checagem.
- **Uma página por origem** (domínio registrável), no máximo 3 checagens e 6 evidências. Falhas do buscador ou de páginas vão para `diagnostico` e não interrompem a busca.
- **Sem veredito.** O experimento classificava as evidências como favoráveis ou contrárias e rotulava a avaliação ("evidências suficientes contrárias"). Aqui a tutora só aponta técnicas (a mesma taxonomia do golden set: urgência, pedido de compartilhamento, autoridade anônima, apelo emocional, outra), cita trechos literais e sugere passos de leitura lateral. Explicações e passos com juízo de veracidade escrito pela tutora (`VEREDITO`) são descartados. Citações literais das fontes podem conter a conclusão da própria agência, que aparece atribuída a ela.
- **Validação item a item.** Citação que não é cópia literal do trecho enviado (ignorando espaços e caixa) ou que cita fonte inexistente é descartada sozinha; o fallback extrativo (`safe_fallback`) só entra quando o JSON é inválido ou nada útil sobra. No experimento, qualquer erro descartava a resposta inteira.
- **Blindagem (GR-03).** Títulos, trechos e a mensagem passam por `app.guardrails.blindagem.blindar_trecho` antes de entrar no prompt; se o módulo não existir, um blindador mínimo remove só caracteres de controle. As citações são validadas contra o texto blindado.
- **Prompt versionado em arquivo** (`prompts/tutora_sintese.txt`); `VERSAO_PROMPT` é o SHA-256 truncado do arquivo e vai no `diagnostico`, para rastrear a versão nas avaliações.
- `LLMIndisponivel` não é capturada: o grafo decide como avisar o usuário.

## Problema conhecido do experimento (IA-03)

Na avaliação do golden set, a busca achou a checagem esperada em 7 de 65 itens, mas ela nunca foi citada: o tutor só mandava ao LLM fontes com relevância "direta" (todos os termos da mensagem no título ou numa frase), o que um boato longo quase nunca atinge, então o LLM nem era chamado. Aqui todas as fontes vão ao LLM (até 5, checagens primeiro), e a relevância lexical só ordena. Isso ainda não foi medido contra o golden set; a taxa de acerto da própria busca (11% a 20%) continua sendo a limitação principal. Consultas longas (boatos inteiros, cortados em 300 caracteres) rendem pouco no DuckDuckGo; extrair a afirmação antes de buscar (nó de extração) deve ajudar.

## Pendências

- Medir IA-03 com o golden set usando estes módulos.
- `pypdf` é opcional (não está no `requirements.txt`): sem ele, PDFs são ignorados.
- Pontuador semântico (embeddings, `sentence-transformers`) para `selecionar_trechos`, se a seleção lexical não bastar.
- Cache de buscas por tipo de afirmação (ver `app/search/README.md`).

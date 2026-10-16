# Implantação na VM

O código de cada versão fica em `~/bot-core-releases/<commit>`; `~/bot-core-current`
aponta para a versão ativa. O checkout antigo em `~/bot-core` é preservado.
O ambiente Python independente fica em `~/bot-core-runtime/.venv`, os arquivos
`worker.env`, `coletor.env` e `conector.env` no mesmo diretório, todos com modo 600.
O perfil WhatsApp e as entregas persistem em `~/bot-core-state` (modo 700).

## Dependências e credenciais

Instalar Redis do Ubuntu, restrito ao loopback, com AOF habilitado; instalar os
requisitos do núcleo e conector num venv separado, PyTorch CPU e
`playwright install --with-deps chromium`. `deploy/configurar_banco.py` usa a
credencial administrativa do `.env` antigo somente para criar `bot_runtime`
(SELECT) e `bot_coletor` (SELECT/INSERT/UPDATE/DELETE e sequências). Não substitui
roles ou arquivos existentes. Senhas aleatórias nunca são impressas.
Copiar `deploy/conector.env.example` para `~/bot-core-runtime/conector.env`.

## Serviços e login

Instalar as unidades desta pasta em `/etc/systemd/system` e executar
`systemctl daemon-reload`. Ativar primeiro `redis-server` e `bot-worker`.
O preflight do worker verifica Redis, banco e o modelo Ollama.

Com o conector parado e as variáveis de `conector.env` exportadas, executar da
release `python -m deploy.login_whatsapp`. O QR é atualizado em `WHATSAPP_QR_PATH`:
visualizá-lo pelo SSH e escanear com o número dedicado do bot. Não publicar o QR.
O comando confirma `LOGIN_CONFIRMADO`, remove a imagem e fecha o navegador.
Só então iniciar `bot-conector` em dry-run. Após validar, definir
`WHATSAPP_DRY_RUN=false` no arquivo privado e reiniciar o conector.

## Coletor RSS

`bot-coletor.timer` agenda a coleta horária. Aos Fatos e Lupa usam somente o texto
do feed. Boatos.org e E-farsas podem ter páginas baixadas conforme robots.txt.
Se a carga histórica antiga estiver ativa, aguardar seu fim antes de trocar a
unidade do coletor. O lock compartilhado em `~/bot-core/.rss.lock` impede sobreposição.
Retomar histórico com o ambiente do coletor e `flock -n -E 0 ~/bot-core/.rss.lock
python -m app.ingestao.rss --historico`. A cobertura depende do histórico que cada
feed disponibiliza; o limite padrão é 500 páginas por fonte, e a coleta para no
primeiro item anterior a 2024. Cada página do feed gera uma linha de log com a data
mais antiga, os documentos novos e o tamanho do banco.

Itens que não são checagens (por exemplo, as páginas de resultado da apuração
eleitoral do Boatos.org) são descartados pelo título, pela URL ou pela categoria,
sem download; `python -m app.ingestao.rss --remover-nao-checagens` apaga os que já
foram gravados. A coleta é interrompida quando o banco passa de `--limite-mb`
(padrão 450 MB), abaixo da cota de 512 MB do plano gratuito do Neon.

## Aceite e recuperação

Conferir logs com `journalctl -u bot-worker -u bot-conector -u bot-coletor`.
Validar um job real pelo Redis antes do envio WhatsApp. Para o teste com três
participantes, usar temporariamente `WHATSAPP_SESSOES_MAX_ATIVAS=2`, observar a
fila e liberação de vaga, então restaurar 10. Reinícios não devem duplicar envios.
Também conferir uma execução do coletor com a role restrita.

Para voltar uma versão, parar o conector e o worker, repontar `bot-core-current`
para a release anterior e reiniciar os serviços. Preservar o banco, arquivos de
ambiente, perfil WhatsApp e registro de entregas. Na primeira implantação,
parar os serviços novos mantém o checkout e a ingestão antigos disponíveis.

## Deploy automatizado

`deploy/implantar.sh <commit>` faz o processo acima sem passos manuais. Só aceita commits que estão
na `main`:

1. Cria a release em `~/bot-core-releases/<commit>` a partir do espelho `~/bot-core-runtime/repo.git`.
2. Instala as dependências no venv só quando `requirements.txt` muda.
3. Aplica as migrações com `~/bot-core-runtime/migracao.env`, a única credencial que altera o esquema.
4. Confere se a release importa e roda o preflight (Redis, banco e Ollama). Se algo falhar, a
   implantação é abortada e a release no ar continua a mesma.
5. Atualiza as unidades `bot-worker` e `bot-conector` que mudaram, troca o link `bot-core-current` de
   forma atômica e reinicia os dois serviços.
6. Confere se os serviços ficaram ativos e roda o teste de fumaça (`python -m deploy.fumaca`): uma
   saudação enviada pela fila, que o worker responde sem LLM. Se falhar, volta sozinho para a release
   anterior.
7. Mantém as 5 releases mais recentes, além da atual e da anterior.

Cada implantação, reversão ou aborto fica em `~/bot-core-runtime/releases.jsonl`. Para ver o
histórico: `~/bot-core-runtime/.venv/bin/python ~/bot-core-runtime/bin/releases.py listar`.

Para voltar a uma release que ainda está no disco: `~/bot-core-runtime/bin/reverter.sh anterior` ou
`~/bot-core-runtime/bin/reverter.sh <commit>`.

O coletor RSS não é reiniciado pelo deploy: o timer usa a release no ar a partir da próxima execução.

### Preparação única

```bash
deploy/preparar_vm.sh --url-migracao-de ~/bot-core/.env --chave /caminho/da/chave_deploy.pub
```

Cria o espelho do repositório, instala as ferramentas em `~/bot-core-runtime/bin`, grava a credencial de
migração e autoriza a chave do GitHub Actions apenas para `comando_ssh.sh` (implantar, reverter e
listar), sem shell, túnel ou encaminhamento.

### Migrações compatíveis (expand/contract)

A reversão troca só o código; o banco fica como está. Por isso cada migração precisa funcionar também
com a release anterior: primeiro acrescentar (colunas, tabelas e índices novos, sem remover nem
renomear nada em uso), e só numa implantação seguinte, quando nenhuma release no disco usar mais o
item antigo, remover.

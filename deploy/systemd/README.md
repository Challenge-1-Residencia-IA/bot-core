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

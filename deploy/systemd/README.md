# Coletor RSS na VM

O serviço `bot-coletor` consulta Boatos.org, E-farsas, Aos Fatos e Agência Lupa.
Para Aos Fatos e Lupa usa apenas o texto publicado no feed. Os outros dois
podem ter a página baixada quando o `robots.txt` permitir. A URL identifica o
documento no banco; uma nova execução ignora URLs já gravadas.

Instalação na VM `bot-core`:

```bash
sudo install -m 644 deploy/systemd/bot-coletor.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/bot-coletor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bot-coletor.timer
```

A carga inicial, de 2024 em diante, pode ser retomada após uma interrupção:

```bash
cd ~/bot-core
set -a; . ./.env; set +a
/usr/bin/flock -n .rss.lock .venv/bin/python -m app.ingestao.rss --historico
```

O limite padrão é 500 páginas por feed; a coleta para antes, no primeiro item
anterior a 2024. O feed pode oferecer menos histórico; nesse caso, a cobertura
começa no item mais antigo que ele publicar. Cada página do feed gera uma linha
de log com a data mais antiga, os documentos novos e o tamanho do banco.

Itens que não são checagens (por exemplo, as páginas de resultado da apuração
eleitoral do Boatos.org) são descartados pelo título, pela URL ou pela categoria,
sem download. Para apagar os que já foram gravados:

```bash
.venv/bin/python -m app.ingestao.rss --remover-nao-checagens
```

A coleta é interrompida quando o banco passa de `--limite-mb` (padrão 450 MB),
abaixo da cota de 512 MB do plano gratuito do Neon. Veja o
andamento com `journalctl -u bot-coletor-historico -f` e o timer com
`systemctl list-timers bot-coletor.timer`.

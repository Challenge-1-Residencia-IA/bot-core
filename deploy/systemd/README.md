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

O limite padrão é 200 páginas por feed. O feed pode oferecer menos histórico;
nesse caso, a cobertura começa no item mais antigo que ele publicar. Veja o
andamento com `journalctl -u bot-coletor-historico -f` e o timer com
`systemctl list-timers bot-coletor.timer`.

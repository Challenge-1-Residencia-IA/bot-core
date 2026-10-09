# WhatsApp AI Bot (Playwright + Groq)

## 📖 Visão Geral

Este repositório contém um **bot autônomo** para o WhatsApp Web, desenvolvido em Python usando **Playwright** para a automação do navegador e a API **Groq** (modelos `qwen/qwen3.8-27b` com fallback para `openai/gpt-oss-120b`) para geração de respostas em linguagem natural.

O objetivo principal é **monitorar conversas**, identificar mensagens não lidas e responder de forma inteligente, focado em combater desinformação (fake news). O bot pode operar em modo de simulação (**dry‑run**) para testes seguros, ou em modo de produção enviando respostas diretamente no WhatsApp.

---

## 🛠️ Funcionalidades Principais

- **Detecção de chats não lidos** – varre a lista lateral (`#pane-side`) e identifica conversas com mensagens pendentes.
- **Troca automática de chat** – abre o chat correspondente e coleta o contexto da conversa.
- **Contextualização** – mantém um histórico limitado das últimas *N* mensagens para fornecer contexto ao modelo de IA.
- **Integração Groq** – gera respostas usando modelos avançados, com fallback automático em caso de falha.
- **Modo Simulação (Dry‑Run)** – registra no terminal a mensagem recebida e a resposta gerada, sem enviar ao contato.
- **Persistência SQLite** – armazena mensagens recebidas e respostas enviadas localmente.
- **Escalabilidade** – suporte a múltiplos workers de IA (paralelismo assíncrono) e fila de tarefas.
- **Compatibilidade Windows** – código preparado para UTF‑8 no console e uso de PowerShell.

---

## 📁 Estrutura do Projeto

```
bot-test/
├─ .env               # Variáveis de ambiente (GROQ_API_KEY)
├─ .env.example       # Exemplo de .env
├─ botfinal.py        # Entrypoint – orquestra scanner, workers e dispatcher
├─ database.py        # SQLite helper (init, save_message)
├─ test_database.py   # Testes unitários para camada de persistência
├─ inspecionar.py     # Ferramentas auxiliares de inspeção (debug)
├─ requirements.txt   # Dependências Python
└─ README.md          # Este documento
```

---

## 🔧 Pré‑requisitos

- **Python 3.10+**
- **PowerShell** (para scripts de ativação do virtualenv)
- Conta Groq com chave de API válida
- Conexão estável com a internet (para chamadas ao modelo)

---

## 🚀 Instalação e Configuração

1. **Clonar o repositório**
   ```powershell
   git clone <repo-url>
   cd bot-test
   ```

2. **Criar e ativar o ambiente virtual** (opcional, porém recomendado)
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Instalar dependências**
   ```powershell
   pip install -r requirements.txt
   playwright install chromium   # instala o browser usado pelo Playwright
   ```

4. **Configurar variáveis de ambiente**
   ```powershell
   cp .env.example .env
   # Edite .env e insira sua chave Groq
   notepad .env
   ```

---

## 📐 Uso

```powershell
python botfinal.py
```

- **Modo Simulação** (padrão) – defina `MODO_DRY_RUN = True` no início de `botfinal.py` para apenas **logar** as mensagens e respostas.
- **Modo Produção** – altere para `MODO_DRY_RUN = False` para que o bot envie respostas reais via WhatsApp Web.

> **Dica:** mantenha o console aberto e monitore os logs. O scanner exibe “pulse” a cada 15 s indicando que está ativo.

---

## 🧪 Testes

Execute os testes de banco de dados com:
```powershell
python -m unittest discover -s . -p "test_*.py"
```

---

## 🤝 Contribuição

1. Fork este repositório
2. Crie uma branch para sua feature (`git checkout -b minha-feature`)
3. Submeta um Pull Request explicando a mudança

---

## 📄 Licença

Distribuído sob a licença MIT. Consulte o arquivo `LICENSE` para mais detalhes.

---

## 📞 Suporte / Troubleshooting

- **Problemas de login no WhatsApp Web** – certifique‑se de que o QR‑code foi escaneado e que a sessão está ativa.
- **Erros de taxa na Groq** – o código já inclui retries automáticos e fallback; caso persista, verifique seu plano de API.
- **Mensagens não enviadas** – confirme se `MODO_DRY_RUN` está `False` e que o navegador não está bloqueando pop‑ups.

---

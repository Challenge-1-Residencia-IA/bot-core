# WhatsApp AI Bot (Playwright + Groq)

Bot autônomo para WhatsApp Web com suporte a:
- **Monitoramento de Conversas**: Varre a lista lateral (`#pane-side`) detectando chats com mensagens não lidas.
- **Chat Switching**: Troca automaticamente para o chat que recebeu mensagem nova.
- **IA Conversacional**: Conectado à Groq (`qwen/qwen3.8-27b` com fallback para `openai/gpt-oss-120b`).
- **Modo Simulação (Dry-Run)**: Exibe a mensagem recebida e a resposta gerada apenas no console, sem enviar para os contatos (evitando envios acidentais durante testes).

---

## 🛠️ Instalação e Configuração

### 1. Criar e ativar o ambiente virtual (opcional se já tiver o `.venv`):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Instalar dependências:
```powershell
pip install -r requirements.txt
playwright install chromium
```

### 3. Configurar variáveis de ambiente:
Crie o arquivo `.env` a partir do `.env.example`:
```env
GROQ_API_KEY=sua_chave_groq_aqui
```

---

## 🚀 Execução

```powershell
python botfinal.py
```

### Alternar entre Modo Simulação e Produção:
No início do arquivo `botfinal.py`:
- `MODO_DRY_RUN = True` -> Apenas registra no terminal.
- `MODO_DRY_RUN = False` -> Digita e envia as respostas no WhatsApp Web.


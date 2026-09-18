from fastapi import FastAPI, Request

app = FastAPI(title="bot-core")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    # TODO: repassar a mensagem recebida ao grafo do LangGraph (app/graph)
    payload = await request.json()
    return {"received": True}

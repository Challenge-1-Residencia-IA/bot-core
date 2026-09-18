from fastapi import FastAPI, Request
from pydantic import BaseModel

app = FastAPI(title="bot-core")


class AnaliseRequest(BaseModel):
    texto: str


class AnaliseResponse(BaseModel):
    texto_recebido: str
    resposta: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analisar", response_model=AnaliseResponse)
def analisar(payload: AnaliseRequest) -> AnaliseResponse:
    # TODO: integrar com o pipeline do LangGraph (app/graph) quando estiver pronto
    return AnaliseResponse(
        texto_recebido=payload.texto,
        resposta="Endpoint funcionando - análise real ainda não implementada.",
    )


@app.post("/webhook")
async def telegram_webhook(request: Request):
    # TODO: repassar a mensagem recebida ao grafo do LangGraph (app/graph)
    payload = await request.json()
    return {"received": True}

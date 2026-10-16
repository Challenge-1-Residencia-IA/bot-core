"""Avaliação da busca na base de checagens com o golden set, usada como portão do deploy.

Roda a busca da release (código e limiares dela) contra o banco de produção, só com leitura e sem LLM:
para cada item do golden set, a mensagem vai para a busca de boato parente e a primeira afirmação
anotada como fato verificável vai para a busca de trechos, como na calibração dos limiares.

Métricas:
- `top1`: fração dos itens em que a checagem esperada vem em 1º lugar.
- `top5`: fração em que ela aparece entre os 5 resultados.
- `ruido_fora_da_base`: com a checagem esperada escondida (20% dos itens, escolhidos por hash do id,
  simulação do IA-07), fração dos itens em que a busca ainda devolve algo.
- `tempo_mediana_s`: tempo mediano de uma busca.

Os números são otimistas: o golden set é da mesma época da base e as afirmações são as anotadas, não
as extraídas pelo Qwen. Servem para comparar versões entre si, não como medida absoluta de qualidade.

Uso (com as variáveis de `worker.env`, na pasta da release):
    python -m deploy.avaliar_busca --golden-set ~/dados-ingestao/golden_set_v1.jsonl [--saida avaliacao.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

from app import config
from app.db.base_checagens import conectar
from app.rag import base as busca_base
from app.rag.embeddings import modelo_padrao

NATUREZAS_VERIFICAVEIS = ("fato verificável", "fato_verificavel", "fora de contexto", "fora_de_contexto")


def no_holdout(item_id: str) -> bool:
    """20% dos itens, sempre os mesmos (hash do id)."""
    return int(hashlib.sha256(item_id.encode()).hexdigest(), 16) % 5 == 0


def afirmacao(item: dict) -> str:
    for a in item.get("afirmacoes", []):
        if a.get("natureza") in NATUREZAS_VERIFICAVEIS:
            return a["texto"]
    return item["afirmacoes"][0]["texto"] if item.get("afirmacoes") else item["texto"][:300]


def mesma_url(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


def avaliar(itens: list[dict], base: busca_base.BaseChecagens) -> dict:
    tempos, top1, top5, ruido, n_holdout = [], 0, 0, 0, 0
    for item in itens:
        inicio = time.perf_counter()
        urls = [a.url for a in base.buscar(afirmacao(item), item["texto"], limite=5)]
        tempos.append(time.perf_counter() - inicio)
        top1 += bool(urls) and mesma_url(urls[0], item["fonte_esperada"])
        top5 += any(mesma_url(u, item["fonte_esperada"]) for u in urls)
        if no_holdout(item["id"]):
            n_holdout += 1
            ruido += bool(base.buscar(afirmacao(item), item["texto"], limite=5,
                                      excluir_urls=[item["fonte_esperada"]]))
    n = len(itens)
    return {"itens": n, "top1": round(top1 / n, 4), "top5": round(top5 / n, 4),
            "ruido_fora_da_base": round(ruido / n_holdout, 4) if n_holdout else None,
            "tempo_mediana_s": round(statistics.median(tempos), 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden-set", required=True, type=Path)
    parser.add_argument("--saida", type=Path)
    args = parser.parse_args()

    itens = [json.loads(linha) for linha in args.golden_set.read_text(encoding="utf-8").splitlines() if linha.strip()]
    with conectar() as conexao:
        documentos, trechos, boatos = conexao.execute(
            "SELECT (SELECT count(*) FROM documentos), (SELECT count(*) FROM trechos),"
            " (SELECT count(*) FROM boatos_conhecidos)").fetchone()
        metricas = avaliar(itens, busca_base.BaseChecagens(conexao, modelo_padrao()))
    resultado = {
        "metricas": metricas,
        "parametros": {"limiar_trecho": busca_base.LIMIAR_TRECHO, "limiar_boato": busca_base.LIMIAR_BOATO,
                       "modelo_embedding": config.EMBEDDING_MODEL, "modelo_llm": config.OLLAMA_MODEL,
                       "golden_set": args.golden_set.name},
        "base": {"documentos": documentos, "trechos": trechos, "boatos": boatos},
    }
    texto = json.dumps(resultado, ensure_ascii=False)
    if args.saida:
        args.saida.write_text(texto + "\n", encoding="utf-8")
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())

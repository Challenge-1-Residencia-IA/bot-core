"""Padrões usados pelas camadas de guardrails.

As expressões são aplicadas sobre o texto normalizado (minúsculas, sem acento), para resistir a
variações de escrita como "IGNORE", "ignóre" ou "instrucoes".
"""

import re
import unicodedata


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento.lower())


def _compilar(padroes: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in padroes))


# Instruções dirigidas ao assistente (OWASP LLM01, prompt injection). Os padrões exigem que a frase
# fale com o sistema ("suas instruções", "você agora é"), para não pegar boatos que dizem, por exemplo,
# "ignore o que a mídia diz".
INJECAO = _compilar([
    r"\b(ignore|ignora|ignorar|desconsidere|desconsiderar|esqueca|esquecer|descarte)\b.{0,40}\b(instruc\w*|regras?|orientac\w*|prompt|comandos?)\b"
    r".{0,30}\b(anterior\w*|acima|previas?|do sistema|originais?|recebid\w*)",
    r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|earlier|all)\b.{0,20}\b(instructions?|rules?|prompts?)\b",
    r"\b(a partir de agora|de agora em diante|agora)\s+voce\s+(e|sera|vai ser|deve agir como|atua como)\b",
    r"\b(voce agora e|finja (que e|ser)|aja como se fosse|act as|you are now|pretend to be)\b",
    r"\b(revele|mostre|exiba|repita|imprima|reveal|show|print|repeat)\b.{0,40}\b(prompt|instruc\w* (do|de) sistema|system prompt|suas instruc\w*|your instructions)\b",
    r"\b(system prompt|prompt do sistema|modo desenvolvedor|developer mode|jailbreak|\bdan\b mode)\b",
    r"<\|?\s*(im_start|im_end|system|endoftext)\s*\|?>|\[/?inst\]|<<\s*/?sys\s*>>|</?\s*system\s*>",
])

# Pedidos fora do escopo (topical rails): tarefas que não são verificação de informação. Os padrões só
# valem no início da mensagem, dirigidos ao assistente ("faz um script", "você pode escrever"), porque
# boatos também usam imperativos ("compartilhe", "o governo cria lista de devedores").
_ABERTURA = (r"^(?:(?:oi|ola|e ai|bom dia|boa tarde|boa noite)[,!. ]+)?(?:(?:por favor|pfv|pf|porfa)[,. ]+)?"
             r"(?:(?:voce|vc|tu|ce) )?(?:(?:pode|poderia|consegue|conseguiria|sabe|saberia|quero que|preciso que) )?"
             r"(?:me |pra mim |para mim )?")
_TAREFA = (r"(?:faca|faz|fazer|crie|cria|criar|escreva|escreve|escrever|gere|gera|gerar|desenvolva|desenvolve|"
           r"programe|programa|monte|monta|elabore|elabora|redija|redige|produza|implemente|implementa|codifique)")
_PRODUTO = (r"(?:script|codigo|programa|funcao|algoritmo|app|aplicativo|site|pagina web|sistema|bot|planilha|"
            r"formula|texto|redacao|poema|poesia|musica|letra|historia|conto|email|e-mail|carta|resumo|"
            r"trabalho|tcc|artigo|curriculo|post|legenda|piada|receita|roteiro|apresentacao|slide|query|consulta sql)")
FORA_DO_ESCOPO = _compilar([
    _ABERTURA + _TAREFA + r"\b.{0,60}\b" + _PRODUTO + r"\b",
    _ABERTURA + r"(?:traduza|traduz|traduzir|calcule|calcula|calcular|resolva|resolve|resolver|corrija|corrige|revise)\b",
    _ABERTURA + r"(?:me )?(?:ajud\w+|ensin\w+|explic\w+)\b.{0,40}\b(?:python|javascript|java|sql|html|css|excel|"
                r"programar|programacao|codigo|script|exercicio|tarefa|prova|dever de casa|matematica|redacao)\b",
    r"^como (?:eu )?(?:faco|fazer|crio|criar|programo|programar|instalo|instalar|configuro|configurar)\b.{0,50}\b"
    r"(?:python|codigo|script|programa|excel|planilha|site|app|api|banco de dados|bolo|receita)\b",
    r"^quanto (?:e|eh|da|sao|fica) (?:[\d(]|o dobro|a metade|a raiz)",
    r"^(?:qual|como) (?:e |eh |esta |sera |vai ser |fica )?(?:a )?previsao do tempo\b",
])

# Saudação ou pergunta sobre o próprio assistente, sem conteúdo para analisar.
APRESENTACAO = _compilar([
    r"^(?:oi|ola|opa|e ai|eai|hello|hi|hey|bom dia|boa tarde|boa noite|tudo bem|tudo bom|salve)(?:[ ,]+\w+)?[ !?.]*$",
    r"^(?:quem (?:e|eh) voce|o que voce (?:faz|e)|como voce funciona|para que (?:voce )?serve|ajuda|help|menu)[ !?.]*$",
    r"^(?:qual|que) (?:e |eh )?(?:o )?(?:seu|teu) modelo\b|^que (?:modelo|ia|llm|inteligencia artificial) (?:e|eh) voce\b",
])

# Afirmação categórica do sistema sobre a mensagem (GR-01). Aplicada fora de trechos citados.
VEREDITO = _compilar([
    r"\b(isso|isto|a mensagem|a afirmacao|a noticia|a alegacao|essa informacao|esse boato|o conteudo)?\s*"
    r"(e|sao)\s+(totalmente\s+|completamente\s+|comprovadamente\s+)?(fals[oa]s?|verdadeir[oa]s?|verdade|mentira|fake)\b",
    r"\b(trata-se de|isso e uma?|e uma?)\s+(fake news|noticia falsa|mentira|boato falso)\b",
])

# Linguagem que julga ou atribui intenção ao usuário (GR-04).
JULGAMENTO_USUARIO = _compilar([
    r"\bvoce (foi enganad[oa]|caiu (em|no|na)|e ingenu[oa]|esta espalhando|quer enganar|acredita em qualquer)\b",
    r"\b(ingenu[oa]|burr[oa]|idiota|ignorante|alienad[oa]|gado|bolsominion|petralha|esquerdopata)\b",
    r"\bvoce deveria (saber|ter verificado|ter vergonha)\b",
])

CITACAO = re.compile(r"[“\"][^”\"]*[”\"]")

# Temas de risco (GR-06) e o aviso correspondente.
TEMAS_DE_RISCO = {
    "saude": (
        _compilar([r"\bvacin\w*", r"\bremedio", r"\bmedicament\w*", r"\bcura\w*\b", r"\btratament\w*",
                   r"\bdoenca", r"\bcovid", r"\bcoronavirus", r"\bcancer", r"\bivermectina", r"\bcloroquina",
                   r"\bdiabet\w*", r"\bhospita\w*", r"\bsus\b", r"\bvirus\b", r"\binfecc\w*", r"\bdose\b"]),
        "Este tema envolve saúde. A análise não substitui orientação médica: consulte um profissional de "
        "saúde e fontes oficiais, como o Ministério da Saúde e a Anvisa.",
    ),
    "eleicao": (
        _compilar([r"\burnas?\b", r"\beleic\w*", r"\beleitor\w*", r"\bvotar\b", r"\bvoto (impresso|auditavel|nulo|em branco)\b",
                   r"\btse\b", r"\btre\b", r"\bsegundo turno\b", r"\bjustica eleitoral\b", r"\bseccao eleitoral\b"]),
        "Este tema envolve o processo eleitoral. Para datas, locais e regras de votação, consulte as "
        "fontes oficiais da Justiça Eleitoral (TSE e TREs).",
    ),
    "golpe": (
        _compilar([r"\bpix\b", r"\bcpf\b", r"\bsenha\b", r"\bcadastr\w*", r"\bpremio\b", r"\bsaque\b",
                   r"\bfgts\b", r"\binss\b", r"\bauxilio\b", r"\bbeneficio\b", r"\bclique no link\b",
                   r"\bdados (pessoais|bancarios)\b"]),
        "Mensagens que pedem cadastro, dados pessoais ou pagamentos são usadas em golpes. Não informe "
        "dados e confira a informação nos canais oficiais do órgão ou da empresa citada.",
    ),
    "seguranca": (
        _compilar([r"\bsequestr\w*", r"\bataque\b", r"\bbomba\b", r"\btiroteio", r"\benchente",
                   r"\bdesabament\w*", r"\bterremoto", r"\btsunami", r"\bevacu\w*", r"\bdefesa civil\b"]),
        "Em situações de risco à segurança, siga as orientações das autoridades oficiais, como a Defesa "
        "Civil (199) e a polícia (190).",
    ),
}

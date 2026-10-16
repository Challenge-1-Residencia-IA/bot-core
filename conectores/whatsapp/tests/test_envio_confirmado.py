from conectores.whatsapp.src.utils.playwright_utils import envio_confirmado, texto_comparavel

ENVIADO = ("Não encontrei fontes que tratem desta mensagem nesta busca. Isso não confirma nem desmente o conteúdo.\n\n"
           "*Como conferir por conta própria*\n1. Consultar o relatório oficial da empresa.\n\n"
           "_Não digo se a mensagem é verdadeira ou falsa: mostro o que encontrei para você decidir._")
# Como o WhatsApp Web mostrou essa mensagem: sem marcas de formatação, lista nativa e texto repetido no DOM.
NA_TELA = ("Não encontrei fontes que tratem desta mensagem nesta busca. Isso não confirma nem desmente o conteúdo.\n\n\n\n"
           "Como conferir por conta própria\n\nConsultar o relatório oficial da empresa.\n\n\n"
           "Não digo se a mensagem é verdadeira ou falsa: mostro o que encontrei para você decidir.\n"
           "Como conferir por conta própria\nConsultar o relatório oficial da empresa.\n"
           "Não digo se a mensagem é verdadeira ou falsa: mostro o que encontrei para você decidir.")


def test_confirma_mensagem_formatada_como_o_whatsapp_mostra():
    assert envio_confirmado(ENVIADO, ["Recebi!", NA_TELA])


def test_lista_com_marcadores_e_negrito():
    assert envio_confirmado("*Teste*\n• linha um\n• linha _dois_\n\n1. fim",
                            ["Teste\n\nlinha um\nlinha dois\n\n\nfim\nTeste\nlinha um\nlinha dois\ndois\nfim"])


def test_nao_confirma_outra_mensagem_nem_vazio():
    assert not envio_confirmado(ENVIADO, ["Recebi! Estou analisando agora."])
    assert not envio_confirmado("", [NA_TELA])
    assert not envio_confirmado(ENVIADO, [])


def test_texto_comparavel_preserva_numeros_no_meio_da_frase():
    assert texto_comparavel("O Pix acima de 5 mil.\n2. Ver o site") == "opixacimade5mil.verosite"


def test_normalizar_nome_ignora_seletor_de_variacao_e_espacos():
    from conectores.whatsapp.src.utils.playwright_utils import normalizar_nome

    assert normalizar_nome("Maria❤️") == normalizar_nome("Maria❤") == "Maria❤"
    assert normalizar_nome("  Maria   Silva ") == "Maria Silva"
    assert normalizar_nome(None) == ""

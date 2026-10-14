import os
import shutil
import tempfile
import unittest

from database import (
    init_database,
    save_message,
    message_exists,
    get_recent_messages,
    get_connection,
)


class TestDatabaseLayer(unittest.TestCase):

    def setUp(self):
        # Cria um diretório temporário para isolar os testes
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "data", "test_bot.db")

    def tearDown(self):
        # Remove a pasta temporária de testes
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_database(self):
        """Testa se a inicialização cria a pasta e o arquivo de banco de dados com a estrutura correta."""
        self.assertFalse(os.path.exists(self.db_path))
        sucesso = init_database(self.db_path)
        self.assertTrue(sucesso)
        self.assertTrue(os.path.exists(self.db_path))

        # Verifica se a tabela 'mensagens' foi criada
        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mensagens';")
            tabela = cursor.fetchone()
            self.assertIsNotNone(tabela)

    def test_save_and_message_exists(self):
        """Testa salvar uma mensagem e verificar se ela existe."""
        init_database(self.db_path)

        msg_id = "meta_test|Olá bot"
        chat_nome = "Contato Teste"
        texto = "Olá bot"

        # Mensagem não deve existir antes de salvar
        self.assertFalse(message_exists(msg_id, self.db_path))

        # Salva a mensagem
        salvo = save_message(
            chat_nome=chat_nome,
            mensagem_id=msg_id,
            role="user",
            texto=texto,
            metadata="meta_test",
            db_path=self.db_path
        )
        self.assertTrue(salvo)

        # Mensagem deve existir após salvar
        self.assertTrue(message_exists(msg_id, self.db_path))

    def test_prevent_duplication(self):
        """Testa se a duplicação de mensagem_id é ignorada."""
        init_database(self.db_path)

        msg_id = "dup_id|123"
        chat_nome = "Contato Teste"

        # Primeira inserção: Sucesso
        res1 = save_message(
            chat_nome=chat_nome,
            mensagem_id=msg_id,
            role="user",
            texto="Primeiro envio",
            db_path=self.db_path
        )
        self.assertTrue(res1)

        # Segunda inserção com o mesmo mensagem_id: Deve ser ignorada (retorna False)
        res2 = save_message(
            chat_nome=chat_nome,
            mensagem_id=msg_id,
            role="user",
            texto="Primeiro envio duplicado",
            db_path=self.db_path
        )
        self.assertFalse(res2)

        # Garante que continua havendo apenas 1 mensagem no banco
        msgs = get_recent_messages(chat_nome, limit=10, db_path=self.db_path)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["texto"], "Primeiro envio")

    def test_get_recent_messages(self):
        """Testa recuperar mensagens recentes em ordem cronológica."""
        init_database(self.db_path)

        chat_nome = "Contato Contexto"

        # Insere 3 mensagens do usuário e do assistente
        save_message(chat_nome, "msg_1", "user", "Oi", db_path=self.db_path)
        save_message(chat_nome, "msg_2", "assistant", "Olá! Como posso ajudar?", db_path=self.db_path)
        save_message(chat_nome, "msg_3", "user", "Qual o seu nome?", db_path=self.db_path)

        msgs = get_recent_messages(chat_nome, limit=15, db_path=self.db_path)
        self.assertEqual(len(msgs), 3)

        # Verifica ordem cronológica (msg_1 -> msg_2 -> msg_3)
        self.assertEqual(msgs[0]["mensagem_id"], "msg_1")
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[1]["mensagem_id"], "msg_2")
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[2]["mensagem_id"], "msg_3")
        self.assertEqual(msgs[2]["role"], "user")

    def test_error_resilience(self):
        """Testa se falhas de parâmetro ou caminho inválido não travam a execução."""
        # Salvar com parâmetros inválidos
        self.assertFalse(save_message("", "", "user", "", db_path=self.db_path))
        self.assertFalse(message_exists("", db_path=self.db_path))
        self.assertEqual(get_recent_messages("", db_path=self.db_path), [])


if __name__ == "__main__":
    unittest.main()

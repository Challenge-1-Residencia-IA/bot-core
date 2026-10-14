import os
import sqlite3
from typing import List, Dict, Any, Optional

DEFAULT_DB_PATH = os.path.join("data", "bot.db")


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Retorna uma conexão para o banco SQLite."""
    dir_path = os.path.dirname(db_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_database(db_path: str = DEFAULT_DB_PATH) -> bool:
    """
    Inicializa o banco de dados SQLite e cria a tabela de mensagens se não existir.
    Garante que o diretório data/ seja criado automaticamente.
    """
    try:
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mensagens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT DEFAULT '',
                    chat_nome TEXT NOT NULL,
                    mensagem_id TEXT UNIQUE NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    texto TEXT NOT NULL,
                    metadata TEXT DEFAULT '',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_mensagens_chat_nome ON mensagens(chat_nome);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_mensagens_mensagem_id ON mensagens(mensagem_id);
            """)
            conn.commit()
        return True
    except Exception as e:
        print(f"⚠️ Erro ao inicializar banco de dados SQLite ({db_path}): {e}")
        return False


def save_message(
    chat_nome: str,
    mensagem_id: str,
    role: str,
    texto: str,
    metadata: str = "",
    chat_id: str = "",
    db_path: str = DEFAULT_DB_PATH
) -> bool:
    """
    Salva uma mensagem no banco de dados.
    Impede duplicação usando INSERT OR IGNORE no mensagem_id.
    """
    if not mensagem_id or not chat_nome or not texto:
        return False

    try:
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO mensagens (chat_id, chat_nome, mensagem_id, role, texto, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (chat_id or "", chat_nome, mensagem_id, role, texto, metadata or ""))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        print(f"⚠️ Erro ao salvar mensagem no SQLite ({mensagem_id}): {e}")
        return False


def message_exists(mensagem_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    """
    Verifica se uma mensagem já existe no banco pelo mensagem_id.
    """
    if not mensagem_id:
        return False

    try:
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM mensagens WHERE mensagem_id = ? LIMIT 1", (mensagem_id,))
            return cursor.fetchone() is not None
    except Exception as e:
        print(f"⚠️ Erro ao verificar existência da mensagem no SQLite ({mensagem_id}): {e}")
        return False


def get_recent_messages(
    chat_nome: str,
    limit: int = 15,
    db_path: str = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """
    Recupera as mensagens mais recentes de um chat em ordem cronológica (mais antigas para mais recentes).
    """
    if not chat_nome:
        return []

    try:
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, chat_id, chat_nome, mensagem_id, role, texto, metadata, timestamp
                FROM mensagens
                WHERE chat_nome = ?
                ORDER BY id DESC
                LIMIT ?
            """, (chat_nome, limit))
            rows = cursor.fetchall()

            resultado = []
            for row in reversed(rows):
                resultado.append({
                    "id": row["id"],
                    "chat_id": row["chat_id"],
                    "chat_nome": row["chat_nome"],
                    "mensagem_id": row["mensagem_id"],
                    "role": row["role"],
                    "texto": row["texto"],
                    "metadata": row["metadata"],
                    "timestamp": row["timestamp"]
                })
            return resultado
    except Exception as e:
        print(f"⚠️ Erro ao buscar mensagens recentes no SQLite para ({chat_nome}): {e}")
        return []

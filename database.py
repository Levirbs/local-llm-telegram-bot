import hashlib
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Tuple
import aiosqlite
import config

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """Gera o hash SHA-256 para armazenamento e comparação segura de senhas."""
    return hashlib.sha256(password.strip().encode("utf-8")).hexdigest()


@asynccontextmanager
async def get_connection():
    """Context manager assíncrono para conexão com SQLite e suporte a chaves estrangeiras ativado."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        db.row_factory = aiosqlite.Row
        yield db


async def init_db() -> None:
    """Inicializa as tabelas do banco de dados SQLite e aplica migrações se necessário."""
    async with get_connection() as db:
        # 1. Cria tabelas base
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                active_session_id INTEGER,
                is_authenticated INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (active_session_id) REFERENCES sessions(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, session_name),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant')),
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            """
        )
        await db.commit()

        # 2. Migração automática: checa se 'is_authenticated' existe na tabela users
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [row["name"] for row in await cursor.fetchall()]
        if "is_authenticated" not in columns:
            logger.info("Aplicando migração: adicionando coluna 'is_authenticated' à tabela users...")
            await db.execute("ALTER TABLE users ADD COLUMN is_authenticated INTEGER DEFAULT 0;")
            await db.commit()

        # 3. Inicializa senha master no banco de dados se ainda não existir
        cursor = await db.execute("SELECT value FROM system_config WHERE key = 'master_password_hash'")
        row = await cursor.fetchone()
        if not row:
            initial_hash = hash_password(config.BOT_MASTER_PASSWORD)
            await db.execute(
                "INSERT INTO system_config (key, value) VALUES ('master_password_hash', ?)",
                (initial_hash,),
            )
            await db.commit()
            logger.info("Senha master inicial registrada no banco de dados com sucesso.")


# =====================================================================
# GERENCIAMENTO DE AUTENTICAÇÃO E SEGURANÇA
# =====================================================================

async def get_master_password_hash() -> str:
    """Recupera o hash da senha master ativa gravado no banco de dados."""
    async with get_connection() as db:
        cursor = await db.execute("SELECT value FROM system_config WHERE key = 'master_password_hash'")
        row = await cursor.fetchone()
        if row:
            return row["value"]
        # Fallback para o valor configurado no ambiente
        return hash_password(config.BOT_MASTER_PASSWORD)


async def is_user_authenticated(user_id: int) -> bool:
    """Verifica se determinado telegram user_id já está autenticado no banco."""
    async with get_connection() as db:
        cursor = await db.execute("SELECT is_authenticated FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row and row["is_authenticated"] == 1:
            return True
        return False


async def authenticate_user(user_id: int, password: str) -> Tuple[bool, str]:
    """Valida a senha digitada pelo usuário e atualiza seu status no banco se correta."""
    stored_hash = await get_master_password_hash()
    input_hash = hash_password(password)

    if input_hash == stored_hash:
        async with get_connection() as db:
            await db.execute(
                "INSERT INTO users (user_id, is_authenticated) VALUES (?, 1) "
                "ON CONFLICT(user_id) DO UPDATE SET is_authenticated = 1",
                (user_id,),
            )
            await db.commit()
        return True, "🔓 <b>Autenticação realizada com sucesso!</b>\nAcesso liberado às suas sessões e à IA."
    else:
        return False, "❌ <b>Senha incorreta.</b>\nPor favor, digite a senha master configurada para acessar o bot."


async def change_master_password(user_id: int, new_password: str) -> Tuple[bool, str]:
    """
    Altera a senha master no banco de dados.
    Restrito exclusivamente a usuários previamente validados.
    """
    authenticated = await is_user_authenticated(user_id)
    if not authenticated:
        return False, "⛔ Acesso negado. Apenas usuários autenticados podem alterar a senha."

    new_password = new_password.strip()
    if len(new_password) < 4:
        return False, "⚠️ A nova senha deve ter no mínimo 4 caracteres."

    new_hash = hash_password(new_password)
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO system_config (key, value, updated_at) 
            VALUES ('master_password_hash', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (new_hash,),
        )
        await db.commit()

    return True, "🔑 <b>Senha master alterada com sucesso!</b>\nAs próximas autenticações exigirão a nova senha."


# =====================================================================
# GERENCIAMENTO DE USUÁRIOS E SESSÕES
# =====================================================================

async def get_or_create_user(user_id: int) -> Dict[str, Any]:
    """
    Garante que o usuário exista na tabela de usuários e possua ao menos uma sessão ativa.
    Se não possuir nenhuma, cria a sessão 'geral' automaticamente.
    """
    async with get_connection() as db:
        cursor = await db.execute(
            "SELECT user_id, active_session_id, is_authenticated FROM users WHERE user_id = ?",
            (user_id,),
        )
        user_row = await cursor.fetchone()

        if not user_row:
            await db.execute("INSERT INTO users (user_id, is_authenticated) VALUES (?, 0)", (user_id,))
            await db.commit()
            active_session_id = None
            is_authenticated = 0
        else:
            active_session_id = user_row["active_session_id"]
            is_authenticated = user_row["is_authenticated"]

        # Se não há sessão ativa vinculada ou ela foi removida, verifica sessões existentes
        if not active_session_id:
            cursor = await db.execute(
                "SELECT id, session_name FROM sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                (user_id,),
            )
            session_row = await cursor.fetchone()

            if session_row:
                active_session_id = session_row["id"]
                await db.execute(
                    "UPDATE users SET active_session_id = ? WHERE user_id = ?",
                    (active_session_id, user_id),
                )
                await db.commit()
                return {
                    "user_id": user_id,
                    "active_session_id": active_session_id,
                    "session_name": session_row["session_name"],
                    "is_authenticated": bool(is_authenticated),
                }
            else:
                # Cria sessão padrão 'geral'
                cursor = await db.execute(
                    "INSERT INTO sessions (user_id, session_name) VALUES (?, ?)",
                    (user_id, "geral"),
                )
                new_session_id = cursor.lastrowid
                await db.execute(
                    "UPDATE users SET active_session_id = ? WHERE user_id = ?",
                    (new_session_id, user_id),
                )
                await db.commit()
                return {
                    "user_id": user_id,
                    "active_session_id": new_session_id,
                    "session_name": "geral",
                    "is_authenticated": bool(is_authenticated),
                }

        # Busca o nome da sessão ativa
        cursor = await db.execute(
            "SELECT id, session_name FROM sessions WHERE id = ? AND user_id = ?",
            (active_session_id, user_id),
        )
        session_row = await cursor.fetchone()
        if session_row:
            return {
                "user_id": user_id,
                "active_session_id": active_session_id,
                "session_name": session_row["session_name"],
                "is_authenticated": bool(is_authenticated),
            }

        # Se o ID apontava para algo inconsistente, restabelece
        await db.execute("UPDATE users SET active_session_id = NULL WHERE user_id = ?", (user_id,))
        await db.commit()

    return await get_or_create_user(user_id)


async def create_session(user_id: int, session_name: str) -> Tuple[bool, str]:
    """Cria uma nova sessão e define como ativa."""
    session_name = session_name.strip()
    if not session_name:
        return False, "O nome da sessão não pode ser vazio."

    await get_or_create_user(user_id)

    async with get_connection() as db:
        cursor = await db.execute(
            "SELECT id FROM sessions WHERE user_id = ? AND LOWER(session_name) = LOWER(?)",
            (user_id, session_name),
        )
        if await cursor.fetchone():
            return False, f"⚠️ Você já possui uma sessão chamada '{session_name}'."

        cursor = await db.execute(
            "INSERT INTO sessions (user_id, session_name) VALUES (?, ?)",
            (user_id, session_name),
        )
        new_session_id = cursor.lastrowid

        await db.execute(
            "UPDATE users SET active_session_id = ? WHERE user_id = ?",
            (new_session_id, user_id),
        )
        await db.commit()
        return True, f"✅ Sessão '<b>{session_name}</b>' criada e ativada com sucesso!"


async def list_sessions(user_id: int) -> List[Dict[str, Any]]:
    """Lista todas as sessões do usuário com contagem de mensagens e status de ativação."""
    await get_or_create_user(user_id)
    async with get_connection() as db:
        query = """
            SELECT 
                s.id,
                s.session_name,
                s.created_at,
                s.updated_at,
                (s.id = u.active_session_id) AS is_active,
                (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS msg_count
            FROM sessions s
            JOIN users u ON s.user_id = u.user_id
            WHERE s.user_id = ?
            ORDER BY s.updated_at DESC
        """
        cursor = await db.execute(query, (user_id,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def switch_session(user_id: int, session_name: str) -> Tuple[bool, str]:
    """Altera a sessão ativa do usuário."""
    session_name = session_name.strip()
    if not session_name:
        return False, "Informe o nome da sessão: <code>/trocar [nome]</code>"

    await get_or_create_user(user_id)

    async with get_connection() as db:
        cursor = await db.execute(
            "SELECT id, session_name FROM sessions WHERE user_id = ? AND LOWER(session_name) = LOWER(?)",
            (user_id, session_name),
        )
        session_row = await cursor.fetchone()
        if not session_row:
            return False, f"❌ Sessão '<b>{session_name}</b>' não encontrada. Use <code>/listar</code> para ver as existentes."

        session_id = session_row["id"]
        actual_name = session_row["session_name"]

        await db.execute(
            "UPDATE users SET active_session_id = ? WHERE user_id = ?",
            (session_id, user_id),
        )
        await db.commit()
        return True, f"🔄 Sessão ativa alterada para '<b>{actual_name}</b>'."


async def delete_session(user_id: int, session_name: str) -> Tuple[bool, str]:
    """Deleta uma sessão e todas as mensagens associadas a ela."""
    session_name = session_name.strip()
    if not session_name:
        return False, "Informe o nome da sessão a ser apagada: <code>/apagar [nome]</code>"

    await get_or_create_user(user_id)

    async with get_connection() as db:
        cursor = await db.execute(
            "SELECT id, session_name FROM sessions WHERE user_id = ? AND LOWER(session_name) = LOWER(?)",
            (user_id, session_name),
        )
        session_row = await cursor.fetchone()
        if not session_row:
            return False, f"❌ Sessão '<b>{session_name}</b>' não encontrada."

        session_id = session_row["id"]
        actual_name = session_row["session_name"]

        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

        cursor = await db.execute(
            "SELECT id FROM sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        )
        next_session = await cursor.fetchone()
        next_id = next_session["id"] if next_session else None

        await db.execute(
            "UPDATE users SET active_session_id = ? WHERE user_id = ?",
            (next_id, user_id),
        )
        await db.commit()

        return True, f"🗑️ Sessão '<b>{actual_name}</b>' e todo o seu histórico foram apagados com sucesso."


async def rename_session(user_id: int, new_name: str) -> Tuple[bool, str]:
    """Renomeia a sessão ativa atual, validando se o novo nome já está em uso pelo usuário."""
    new_name = new_name.strip()
    if not new_name:
        return False, "Informe o novo nome: <code>/renomear [novo_nome]</code>"

    user_info = await get_or_create_user(user_id)
    active_session_id = user_info.get("active_session_id")
    old_name = user_info.get("session_name", "")

    if not active_session_id:
        return False, "❌ Nenhuma sessão ativa encontrada para renomear."

    if old_name.lower() == new_name.lower():
        return False, "⚠️ O novo nome é idêntico ao nome atual da sessão ativa."

    async with get_connection() as db:
        cursor = await db.execute(
            "SELECT id FROM sessions WHERE user_id = ? AND LOWER(session_name) = LOWER(?) AND id != ?",
            (user_id, new_name, active_session_id),
        )
        if await cursor.fetchone():
            return False, f"⚠️ O nome '<b>{new_name}</b>' já está em uso em outra sessão sua. Escolha outro nome."

        await db.execute(
            "UPDATE sessions SET session_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_name, active_session_id),
        )
        await db.commit()
        return True, f"✏️ Sessão renomeada de '<b>{old_name}</b>' para '<b>{new_name}</b>'."


async def get_active_session(user_id: int) -> Optional[Dict[str, Any]]:
    """Recupera informações da sessão ativa do usuário."""
    return await get_or_create_user(user_id)


async def add_message(session_id: int, role: str, content: str) -> None:
    """Insere uma mensagem no histórico da sessão e atualiza o timestamp."""
    async with get_connection() as db:
        await db.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        await db.execute(
            "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        await db.commit()


async def get_session_messages(session_id: int, limit: int = 20) -> List[Dict[str, str]]:
    """
    Recupera as últimas `limit` mensagens da sessão em ordem cronológica (antigas primeiro)
    para alimentar o contexto da LLM.
    """
    async with get_connection() as db:
        query = """
            SELECT role, content 
            FROM messages 
            WHERE session_id = ? 
            ORDER BY id DESC 
            LIMIT ?
        """
        cursor = await db.execute(query, (session_id, limit))
        rows = await cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

import os
import base64
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import config
import database
from utils import extract_code_blocks, save_code_to_file, safe_remove_file, split_message
from router import detect_semantic_intent
from image_generator import generate_image_file
import audio_service


@pytest.mark.asyncio
async def test_authentication_and_password_change(tmp_path, monkeypatch):
    """Testa a camada de autenticação, verificação de senha master e comando /trocarsenha."""
    test_db = tmp_path / "test_auth.db"
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)
    monkeypatch.setattr(config, "BOT_MASTER_PASSWORD", "master_pass_123")

    await database.init_db()

    user_id = 888777

    # 1. Usuário começa não autenticado
    is_auth = await database.is_user_authenticated(user_id)
    assert is_auth is False

    # 2. Tentativa com senha errada falha
    success, msg = await database.authenticate_user(user_id, "senha_errada")
    assert success is False
    assert "Senha incorreta" in msg
    assert await database.is_user_authenticated(user_id) is False

    # 3. Tentativa de trocar senha antes de se autenticar deve ser barrada
    can_change, msg_change = await database.change_master_password(user_id, "nova_senha_456")
    assert can_change is False
    assert "Acesso negado" in msg_change

    # 4. Autenticação com a senha master correta
    success, msg = await database.authenticate_user(user_id, "master_pass_123")
    assert success is True
    assert "Autenticação realizada com sucesso" in msg
    assert await database.is_user_authenticated(user_id) is True

    # 5. Usuário autenticado altera a senha master
    can_change, msg_change = await database.change_master_password(user_id, "nova_senha_456")
    assert can_change is True
    assert "Senha master alterada com sucesso" in msg_change

    # 6. Novo usuário tenta entrar com a senha antiga -> deve falhar
    new_user = 999111
    success_old, _ = await database.authenticate_user(new_user, "master_pass_123")
    assert success_old is False

    # 7. Novo usuário entra com a nova senha -> deve passar
    success_new, _ = await database.authenticate_user(new_user, "nova_senha_456")
    assert success_new is True
    assert await database.is_user_authenticated(new_user) is True


@pytest.mark.asyncio
async def test_database_lifecycle_and_sessions(tmp_path, monkeypatch):
    """Testa o isolamento de sessões e integridade referencial no SQLite."""
    test_db = tmp_path / "test_sessions.db"
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)

    await database.init_db()
    user_id = 123456789

    # 1. Cria usuário e sessão padrão
    user_info = await database.get_or_create_user(user_id)
    assert user_info["user_id"] == user_id
    assert user_info["session_name"] == "geral"

    # 2. Cria nova sessão
    ok, msg = await database.create_session(user_id, "projeto_ia")
    assert ok is True

    # 3. Impede duplicidade case-insensitive
    ok_dup, _ = await database.create_session(user_id, "PROJETO_IA")
    assert ok_dup is False

    # 4. Alterna de sessão
    ok_sw, _ = await database.switch_session(user_id, "geral")
    assert ok_sw is True
    current = await database.get_active_session(user_id)
    assert current["session_name"] == "geral"

    # 5. Renomeia sessão ativa com validação de unicidade
    ok_ren_dup, _ = await database.rename_session(user_id, "projeto_ia")
    assert ok_ren_dup is False  # Nome já em uso por outra sessão

    ok_ren, _ = await database.rename_session(user_id, "principal")
    assert ok_ren is True
    current = await database.get_active_session(user_id)
    assert current["session_name"] == "principal"

    # 6. Adiciona e recupera mensagens
    await database.add_message(current["active_session_id"], "user", "Olá!")
    await database.add_message(current["active_session_id"], "assistant", "Como posso ajudar?")
    msgs = await database.get_session_messages(current["active_session_id"])
    assert len(msgs) == 2


def test_semantic_intent_router():
    """Valida o roteador semântico de pré-processamento para comandos sem / explícito."""
    # Teste de Geração de Imagem
    intent1, params1 = detect_semantic_intent("Gere uma imagem de um robô pintando em aquarela")
    assert intent1 == "generate_image"
    assert "robô pintando em aquarela" in params1["prompt"]

    intent2, params2 = detect_semantic_intent("desenhe um gato astronauta na lua")
    assert intent2 == "generate_image"
    assert "gato astronauta" in params2["prompt"]

    # Teste de Criação de Rotina
    intent3, params3 = detect_semantic_intent("Crie uma rotina de estudos para concurso público")
    assert intent3 == "create_routine"
    assert "estudos para concurso público" in params3["details"]

    intent4, params4 = detect_semantic_intent("planeje uma rotina de treino matinal")
    assert intent4 == "create_routine"
    assert "treino matinal" in params4["details"]

    # Teste de Conversação Geral
    intent5, params5 = detect_semantic_intent("Como funciona o algoritmo de busca binária em Python?")
    assert intent5 == "text_chat"


def test_code_extraction_and_compulsory_file_cleanup(tmp_path, monkeypatch):
    """
    Testa a extração de código e a limpeza compulsória (Bug Fix):
    Garante que arquivos temporários salvos no disco são fisicamente removidos.
    """
    monkeypatch.setattr(config, "TEMP_FILES_DIR", tmp_path)

    sample_text = """
    Aqui está a solução:
    ```python
    def somar(a, b):
        return a + b
    ```
    E o teste em shell:
    ```bash
    pytest
    ```
    """

    blocks = extract_code_blocks(sample_text)
    assert len(blocks) == 2

    lang, code, ext = blocks[0]
    assert lang == "python"
    assert ext == ".py"

    # Simula o fluxo de criação e limpeza compulsória em bloco try...finally
    file_path = None
    try:
        file_path = save_code_to_file(code, ext, 1, "test_prefix")
        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == code
    finally:
        # Diretiva compulsória
        if file_path:
            safe_remove_file(file_path)

    # Verifica que o arquivo foi compulsoriamente destruído fisicamente do disco
    assert not file_path.exists()


def test_image_generation_and_compulsory_cleanup(tmp_path, monkeypatch):
    """Testa a geração local de imagem via Pillow e a remoção obrigatória no finally."""
    monkeypatch.setattr(config, "TEMP_FILES_DIR", tmp_path)

    prompt = "Um jardim cibernético com flores bioluminescentes"
    img_path = None
    try:
        img_path = generate_image_file(prompt)
        assert img_path.exists()
        assert img_path.stat().st_size > 0
    finally:
        if img_path:
            safe_remove_file(img_path)

    # Verifica que o rastro físico foi eliminado
    assert not img_path.exists()


def test_photo_processing_and_compulsory_cleanup(tmp_path, monkeypatch):
    """Testa o processamento de fotos recebidas, codificação base64 e limpeza compulsória."""
    monkeypatch.setattr(config, "TEMP_FILES_DIR", tmp_path)

    test_photo = tmp_path / "incoming_photo.jpg"
    test_photo.write_bytes(b"dummy image bytes for test")

    assert test_photo.exists()
    try:
        b64 = base64.b64encode(test_photo.read_bytes()).decode("utf-8")
        assert len(b64) > 0
    finally:
        safe_remove_file(test_photo)

    assert not test_photo.exists()


@pytest.mark.asyncio
async def test_audio_download_and_compulsory_cleanup(tmp_path, monkeypatch):
    """Testa o ciclo de download e remoção física obrigatória de áudio temporário."""
    monkeypatch.setattr(config, "TEMP_AUDIO_DIR", tmp_path)

    fake_bot = AsyncMock()
    fake_file = AsyncMock()

    async def fake_download(custom_path):
        Path(custom_path).write_bytes(b"dummy audio content")

    fake_file.download_to_drive = fake_download
    fake_bot.get_file.return_value = fake_file

    audio_path = None
    try:
        audio_path = await audio_service.download_telegram_audio(fake_bot, "file_id_123", ".ogg")
        assert audio_path.exists()
        assert audio_path.read_bytes() == b"dummy audio content"
    finally:
        if audio_path:
            safe_remove_file(audio_path)

    # Verifica que o arquivo de áudio temporário foi fisicamente apagado
    assert not audio_path.exists()


def test_split_message():
    """Testa particionamento de mensagens grandes sem quebra de linhas."""
    curta = "Mensagem normal"
    assert split_message(curta, 100) == [curta]

    longa = "Linha A\n" + ("X" * 90) + "\nLinha B\n" + ("Y" * 90)
    partes = split_message(longa, max_length=100)
    assert len(partes) >= 2
    assert all(len(p) <= 100 for p in partes)

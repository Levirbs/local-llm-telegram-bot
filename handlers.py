import os
import base64
import asyncio
import logging
import datetime
from functools import wraps
from pathlib import Path
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

import database
import audio_service
import router
import ollama_client
from utils import safe_remove_file
from config import TEMP_FILES_DIR, MAX_CONTEXT_MESSAGES

logger = logging.getLogger(__name__)


# =====================================================================
# DECORATOR E MIDDLEWARE DE AUTENTICAÇÃO
# =====================================================================

def require_auth(handler_func):
    """Decorator para proteger comandos e rotinas, exigindo autenticação prévia no SQLite."""
    @wraps(handler_func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user:
            return

        is_authenticated = await database.is_user_authenticated(user.id)
        if not is_authenticated:
            await update.message.reply_text(
                "🔒 <b>Acesso Negado:</b> Este bot é privado e requer autenticação.\n"
                "Por favor, <b>digite a senha master</b> no chat para liberar o acesso.",
                parse_mode=ParseMode.HTML,
            )
            return

        return await handler_func(update, context, *args, **kwargs)
    return wrapper


async def send_typing_loop(bot, chat_id: int, stop_event: asyncio.Event):
    """Mantém o status de 'digitando...' ativo enquanto a LLM ou transcrição processa."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.wait_for(stop_event.wait(), timeout=4.5)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break


# =====================================================================
# COMANDOS DE AUTENTICAÇÃO E CONTROLE
# =====================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /start: Boas-vindas e verificação de autenticação."""
    user = update.effective_user
    if not user:
        return

    is_auth = await database.is_user_authenticated(user.id)
    if not is_auth:
        await update.message.reply_text(
            f"👋 Olá, <b>{user.first_name}</b>!\n\n"
            f"🔒 <b>Acesso Restrito:</b> Este bot de inteligência artificial é privado.\n"
            f"Para começar a utilizá-lo, por favor <b>envie a senha master</b> de segurança.",
            parse_mode=ParseMode.HTML,
        )
        return

    user_info = await database.get_or_create_user(user.id)
    active_session = user_info.get("session_name", "geral")

    welcome_text = (
        f"👋 Bem-vindo de volta, <b>{user.first_name}</b>!\n\n"
        f"📍 <b>Sessão ativa:</b> <code>{active_session}</code>\n\n"
        f"<b>Funcionalidades Ativas:</b>\n"
        f"🧠 <b>Roteamento Semântico:</b> Converse naturalmente. Peça para gerar imagens ou rotinas sem comandos estritos!\n"
        f"👁️ <b>Visão Computacional:</b> Envie fotos ou imagens e o modelo as analisará em detalhes.\n"
        f"🎙️ <b>Voz Offline:</b> Envie áudios ou notas de voz que serão transcritos com faster-whisper.\n"
        f"📁 <b>Código Automático:</b> Códigos gerados são enviados como arquivos com limpeza compulsória do disco.\n\n"
        f"<b>Comandos de Sessão e Segurança:</b>\n"
        f"🔹 <code>/novo [nome]</code> - Cria e ativa nova sessão isolada.\n"
        f"🔹 <code>/listar</code> - Lista suas sessões cadastradas.\n"
        f"🔹 <code>/trocar [nome]</code> - Alterna a sessão ativa.\n"
        f"🔹 <code>/apagar [nome]</code> - Deleta uma sessão e seu histórico.\n"
        f"🔹 <code>/renomear [novo_nome]</code> - Renomeia a sessão atual.\n"
        f"🔹 <code>/trocarsenha [nova_senha]</code> - Atualiza a senha master no banco.\n"
        f"🔹 <code>/ajuda</code> - Menu de instruções."
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /ajuda: Instruções de uso."""
    help_text = (
        "📖 <b>Guia do Assistente Pessoal:</b>\n\n"
        "• <b>Conversação & Roteamento Semântico:</b> Apenas converse! Diga 'gere uma imagem de...', 'crie uma rotina...', ou tire dúvidas técnicas.\n"
        "• <b>Envio de Imagens:</b> Envie qualquer foto ou imagem com uma legenda/pergunta e o modelo multimodal responderá.\n"
        "• <b>Mensagens de Voz:</b> Grave um áudio e o modelo responderá ao conteúdo transcrito localmente.\n"
        "• <code>/novo &lt;nome&gt;</code>: Cria uma sessão com contexto isolado.\n"
        "• <code>/listar</code>: Exibe suas sessões salvas no SQLite.\n"
        "• <code>/trocar &lt;nome&gt;</code>: Alterna a sessão de trabalho ativa.\n"
        "• <code>/apagar &lt;nome&gt;</code>: Remove a sessão e suas mensagens.\n"
        "• <code>/renomear &lt;novo_nome&gt;</code>: Renomeia a sessão atual (com validação de duplicidade).\n"
        "• <code>/trocarsenha &lt;nova_senha&gt;</code>: Altera a senha master de autenticação."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


@require_auth
async def trocarsenha_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /trocarsenha [nova_senha]: Atualiza a senha master no SQLite."""
    user = update.effective_user
    if not user:
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Por favor, informe a nova senha.\nExemplo: <code>/trocarsenha nova_senha_forte123</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    new_password = " ".join(context.args).strip()
    success, msg = await database.change_master_password(user.id, new_password)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


# =====================================================================
# COMANDOS DE GERENCIAMENTO DE SESSÕES (PROTEGIDOS POR AUTENTICAÇÃO)
# =====================================================================

@require_auth
async def novo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /novo [nome]: Cria uma nova sessão."""
    user = update.effective_user
    if not user:
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Por favor, informe o nome da nova sessão.\nExemplo: <code>/novo backend_python</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    session_name = " ".join(context.args).strip()
    success, message = await database.create_session(user.id, session_name)
    await update.message.reply_text(message, parse_mode=ParseMode.HTML)


@require_auth
async def listar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /listar: Lista todas as sessões do usuário."""
    user = update.effective_user
    if not user:
        return

    sessions = await database.list_sessions(user.id)
    if not sessions:
        await update.message.reply_text("Você ainda não possui nenhuma sessão cadastrada.")
        return

    lines = ["📂 <b>Suas Sessões de Conversa:</b>\n"]
    for s in sessions:
        active_badge = " 🟢 <b>[ATIVA]</b>" if s["is_active"] else ""
        msg_count = s["msg_count"]
        lines.append(f"• <b>{s['session_name']}</b>{active_badge}\n  ↳ <i>{msg_count} mensagens</i>")

    lines.append("\n💡 <i>Para alternar: <code>/trocar [nome]</code></i>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@require_auth
async def trocar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /trocar [nome]: Altera a sessão ativa."""
    user = update.effective_user
    if not user:
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Por favor, informe o nome da sessão para a qual deseja mudar.\nExemplo: <code>/trocar estudos</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    session_name = " ".join(context.args).strip()
    success, message = await database.switch_session(user.id, session_name)
    await update.message.reply_text(message, parse_mode=ParseMode.HTML)


@require_auth
async def apagar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /apagar [nome]: Deleta a sessão e seu histórico."""
    user = update.effective_user
    if not user:
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Por favor, informe o nome da sessão a ser apagada.\nExemplo: <code>/apagar teste</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    session_name = " ".join(context.args).strip()
    success, message = await database.delete_session(user.id, session_name)
    await update.message.reply_text(message, parse_mode=ParseMode.HTML)


@require_auth
async def renomear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /renomear [novo_nome]: Renomeia a sessão atual."""
    user = update.effective_user
    if not user:
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Por favor, informe o novo nome para a sessão atual.\nExemplo: <code>/renomear projeto_ia</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    new_name = " ".join(context.args).strip()
    success, message = await database.rename_session(user.id, new_name)
    await update.message.reply_text(message, parse_mode=ParseMode.HTML)


# =====================================================================
# HANDLERS DE PROCESSAMENTO: VISÃO (IMAGENS), VOZ & TEXTO
# =====================================================================

async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler para processamento de fotos e imagens enviadas pelo usuário.
    Baixa o arquivo, converte para Base64, submete ao modelo multimodal (Ollama)
    e garante a destruição física do arquivo temporário em try...finally.
    """
    user = update.effective_user
    message = update.message
    if not user or not message:
        return

    # 1. Validação de autenticação de segurança
    is_authenticated = await database.is_user_authenticated(user.id)
    if not is_authenticated:
        await message.reply_text(
            "🔒 <b>Acesso Negado:</b> Autentique-se enviando a senha master por texto antes de enviar imagens.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Obtém o arquivo de maior resolução
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id = message.document.file_id

    if not file_id:
        return

    caption = (message.caption or "").strip()
    prompt_text = caption if caption else "Descreva e analise detalhadamente o que você observa nesta imagem."

    chat_id = message.chat_id
    temp_photo_path: Path | None = None
    b64_image = ""

    # Feedback de digitação
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(send_typing_loop(context.bot, chat_id, stop_typing))

    try:
        # Baixa a foto do Telegram para o disco
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        temp_photo_path = TEMP_FILES_DIR / f"input_photo_{timestamp}.jpg"

        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=temp_photo_path)

        # Converte a imagem para Base64
        with open(temp_photo_path, "rb") as f:
            b64_image = base64.b64encode(f.read()).decode("utf-8")

        # Recupera sessão ativa
        user_info = await database.get_or_create_user(user.id)
        session_id = user_info["active_session_id"]
        session_name = user_info["session_name"]

        # Registra no SQLite
        display_caption = f"[Imagem enviada] {caption}" if caption else "[Imagem enviada para análise]"
        await database.add_message(session_id=session_id, role="user", content=display_caption)

        # Monta histórico incluindo a imagem no payload
        history = await database.get_session_messages(session_id=session_id, limit=MAX_CONTEXT_MESSAGES)
        
        # Injeta o campo 'images' no último item do payload enviado ao Ollama
        messages_payload = []
        for msg in history[:-1]:
            messages_payload.append({"role": msg["role"], "content": msg["content"]})

        messages_payload.append({
            "role": "user",
            "content": prompt_text,
            "images": [b64_image],
        })

        # Consulta o modelo multimodal
        response_data = await ollama_client.chat_completion(messages=messages_payload)
        ai_response = response_data.get("content", "")

        if not ai_response:
            ai_response = "Não foi possível obter uma resposta do modelo para esta imagem."

        # Responde e gerencia códigos com limpeza compulsória
        await router.handle_text_chat(
            update=update,
            context=context,
            session_id=session_id,
            session_name=session_name,
            ai_response=ai_response,
        )

    except Exception as e:
        logger.exception(f"Erro ao processar imagem multimodal: {e}")
        await message.reply_text(f"⚠️ Ocorreu um erro ao analisar a imagem: {e}")
    finally:
        # Limpeza física compulsória do arquivo da imagem baixada
        if temp_photo_path:
            safe_remove_file(temp_photo_path)
            logger.debug(f"Arquivo temporário de imagem destruído: {temp_photo_path}")

        stop_typing.set()
        await typing_task


async def handle_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler assíncrono para mensagens de voz e arquivos de áudio (filters.VOICE e filters.AUDIO).
    Baixa o arquivo .ogg, transcreve com faster-whisper, destrói o arquivo temporário
    em bloco try...finally e injeta o texto transcrito no fluxo normal da sessão.
    """
    user = update.effective_user
    message = update.message
    if not user or not message:
        return

    # 1. Validação de autenticação de segurança
    is_authenticated = await database.is_user_authenticated(user.id)
    if not is_authenticated:
        await message.reply_text(
            "🔒 <b>Acesso Negado:</b> Autentique-se enviando a senha master por texto antes de enviar áudios.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Identifica o arquivo de áudio ou nota de voz
    tg_audio = message.voice or message.audio
    if not tg_audio:
        return

    chat_id = message.chat_id
    audio_path: Path | None = None
    transcription_text = ""

    # Feedback imediato de transcrição
    status_msg = await message.reply_text("🎙️ <i>Transcrevendo áudio localmente (faster-whisper)...</i>", parse_mode=ParseMode.HTML)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    try:
        # Baixa o áudio .ogg/.oga do Telegram para o disco
        ext = ".ogg" if message.voice else ".mp3"
        audio_path = await audio_service.download_telegram_audio(context.bot, tg_audio.file_id, extension=ext)

        # Transcrição assíncrona com faster-whisper
        transcription_text = await audio_service.transcribe_audio(audio_path)

    except Exception as e:
        logger.exception(f"Erro ao processar transcrição de áudio: {e}")
        await status_msg.edit_text(f"❌ Falha ao processar o áudio localmente: {e}")
        return
    finally:
        # Limpeza compulsória garantida do arquivo de áudio temporário baixado
        if audio_path:
            safe_remove_file(audio_path)
            logger.debug(f"Arquivo temporário de voz destruído do disco: {audio_path}")

    # Validação do texto obtido
    if not transcription_text:
        await status_msg.edit_text("🔇 Não foi possível identificar fala compreensível no áudio enviado.")
        return

    await status_msg.edit_text(
        f"🗣️ <b>Áudio transcrito:</b>\n\"<i>{transcription_text}</i>\"",
        parse_mode=ParseMode.HTML,
    )

    # Injeta a transcrição no fluxo normal como se o usuário tivesse digitado!
    await process_user_text_input(update, context, user, transcription_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler de mensagens de texto com autenticação integrada e roteamento semântico."""
    user = update.effective_user
    message = update.message
    if not user or not message or not message.text:
        return

    user_text = message.text.strip()

    # 1. Checagem de Autenticação
    is_authenticated = await database.is_user_authenticated(user.id)
    if not is_authenticated:
        # Tenta autenticar utilizando o texto enviado como senha
        success, auth_msg = await database.authenticate_user(user.id, user_text)
        await message.reply_text(auth_msg, parse_mode=ParseMode.HTML)
        if success:
            # Mostra menu de boas-vindas logo após o login bem-sucedido
            await start_command(update, context)
        return

    # 2. Usuário autenticado: segue para o processamento da mensagem
    await process_user_text_input(update, context, user, user_text)


async def process_user_text_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user,
    user_text: str,
) -> None:
    """Centraliza o processamento de texto (seja digitado ou transcrito de voz)."""
    chat_id = update.effective_chat.id

    # 1. Recupera ou inicializa a sessão ativa do usuário
    user_info = await database.get_or_create_user(user.id)
    session_id = user_info["active_session_id"]
    session_name = user_info["session_name"]

    # 2. Registra a mensagem do usuário no banco de dados SQLite
    await database.add_message(session_id=session_id, role="user", content=user_text)

    # 3. Inicia indicador de 'digitando...' em segundo plano
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(send_typing_loop(context.bot, chat_id, stop_typing))

    try:
        # 4. Despacha para o roteador semântico de intenções
        await router.dispatch_user_request(
            update=update,
            context=context,
            session_id=session_id,
            session_name=session_name,
            user_text=user_text,
        )
    except Exception as e:
        logger.exception(f"Erro no processamento da requisição do usuário: {e}")
        await update.message.reply_text(f"⚠️ Ocorreu um erro no processamento: {e}")
    finally:
        stop_typing.set()
        await typing_task

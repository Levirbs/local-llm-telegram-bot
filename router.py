import re
import os
import json
import logging
from pathlib import Path
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ContextTypes

import database
import ollama_client
from utils import extract_code_blocks, save_code_to_file, split_message, safe_remove_file
from config import MAX_CONTEXT_MESSAGES
from image_generator import generate_image_file

logger = logging.getLogger(__name__)


# =====================================================================
# ROTEADOR SEMÂNTICO DE PRÉ-PROCESSAMENTO & RECONHECIMENTO DE PADRÕES
# =====================================================================

IMAGE_PATTERNS = [
    # Ex: "gere uma imagem de...", "crie uma foto de...", "faça um desenho..."
    r"^(?:por favor,?\s*)?(?:gere|gerar|crie|criar|fa[çc]a|fazer|produza|produzir)\s+(?:uma\s+)?(?:imagem|foto|desenho|ilustra[çc][ãa]o|arte|wallpaper|retrato)\s+(?:de|do|da|com|sobre)?\s*(.*)$",
    # Ex: "desenhe um gato astronauta", "ilustre uma paisagem", "pinte um pôr do sol"
    r"^(?:por favor,?\s*)?(?:desenhe|desenhar|ilustre|ilustrar|pinte|pintar)\s+(?:uma?\s+|o\s+|a\s+)?(.*)$",
    # Ex: "imagem de...", "foto de..."
    r"^(?:imagem|foto|desenho|ilustra[çc][ãa]o)\s+(?:de|do|da|com)?\s*(.*)$",
]

ROUTINE_PATTERNS = [
    # Ex: "crie uma rotina de...", "planeje uma rotina para..."
    r"^(?:por favor,?\s*)?(?:crie|criar|monte|montar|planeje|planejar|organize|organizar|estabele[çc]a)\s+(?:uma\s+)?rotina\s+(?:de|para)?\s*(.*)$",
    # Ex: "rotina de estudos", "cronograma de..."
    r"^(?:rotina|h[áa]bito|cronograma)\s+(?:de|para)?\s*(.*)$",
]


def detect_semantic_intent(text: str) -> tuple[str, dict]:
    """
    Roteador semântico de pré-processamento que analisa a linguagem natural
    e detecta a intenção sem exigir comandos estritos (ex: /gerar).
    
    Returns:
        tuple: (intent_name, intent_params)
        intents possíveis: "generate_image", "create_routine", "text_chat"
    """
    clean_text = text.strip()

    # 1. Verifica intenção de Geração de Imagem
    for pattern in IMAGE_PATTERNS:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            prompt = match.group(1).strip()
            if not prompt:
                prompt = clean_text
            return "generate_image", {"prompt": prompt}

    # 2. Verifica intenção de Criação de Rotina
    for pattern in ROUTINE_PATTERNS:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            details = match.group(1).strip()
            return "create_routine", {"details": details or clean_text}

    # 3. Padrão: Conversação e chat geral
    return "text_chat", {"text": clean_text}


# =====================================================================
# HANDLERS ESPECÍFICOS DE EXECUÇÃO
# =====================================================================

async def handle_image_generation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_id: int,
    prompt: str,
) -> None:
    """
    Função executora para a intenção de geração de imagem.
    Gera o arquivo localmente e assegura a remoção física compulsória no bloco finally.
    """
    message = update.message
    chat_id = message.chat_id
    image_path: Path | None = None

    await message.reply_text(
        f"🎨 <b>Intenção detectada:</b> Geração de Imagem\n"
        f"🖌️ <i>Processando:</i> \"<code>{prompt}</code>\"...",
        parse_mode=ParseMode.HTML,
    )
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

    try:
        # Gera a imagem fisicamente no diretório temporário
        image_path = generate_image_file(prompt)

        # Envia a foto gerada para o chat do Telegram
        with open(image_path, "rb") as photo_file:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo_file,
                caption=f"🖼️ <b>Imagem gerada com sucesso!</b>\n<i>Prompt:</i> {prompt}",
                parse_mode=ParseMode.HTML,
            )

        # Registra a interação no histórico da sessão no banco
        await database.add_message(
            session_id=session_id,
            role="assistant",
            content=f"[Imagem gerada para o prompt: '{prompt}']",
        )

    except Exception as e:
        logger.exception(f"Erro na geração ou envio de imagem: {e}")
        await message.reply_text(f"❌ Não foi possível gerar a imagem: {e}")
    finally:
        # Limpeza compulsória física do arquivo no disco (Bug Fix 4)
        if image_path:
            safe_remove_file(image_path)
            logger.info(f"Limpeza compulsória de imagem efetuada: {image_path}")


async def handle_routine_creation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_id: int,
    details: str,
) -> None:
    """
    Função executora para a intenção de planejamento e criação de rotinas.
    Injeta diretrizes especializadas no modelo para estruturar passos, metas e horários.
    """
    message = update.message
    chat_id = message.chat_id

    await message.reply_text(
        f"📅 <b>Intenção detectada:</b> Planejamento de Rotina\n"
        f"Organizando um cronograma prático para: <i>{details}</i>...",
        parse_mode=ParseMode.HTML,
    )
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Contexto direcionado para estruturação de hábitos e rotinas
    system_prompt = (
        "Você é um especialista em produtividade e organização pessoal. "
        "Estruture uma rotina detalhada, realista e com horários bem definidos, "
        "incluindo dicas práticas e divisões por blocos de tempo."
    )

    history = await database.get_session_messages(session_id=session_id, limit=MAX_CONTEXT_MESSAGES)
    prompt_messages = [{"role": "system", "content": system_prompt}] + history

    try:
        response_data = await ollama_client.chat_completion(prompt_messages)
        ai_response = response_data.get("content", "")

        if not ai_response:
            ai_response = "Aqui está a estrutura de rotina recomendada para você."

        await database.add_message(session_id=session_id, role="assistant", content=ai_response)

        chunks = split_message(ai_response, max_length=4000)
        for chunk in chunks:
            try:
                await message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await message.reply_text(chunk)

    except Exception as e:
        logger.exception(f"Erro ao criar rotina: {e}")
        await message.reply_text(f"⚠️ Erro ao gerar plano de rotina: {e}")


async def handle_text_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_id: int,
    session_name: str,
    ai_response: str,
) -> None:
    """
    Função executora para conversação comum e assistência em programação.
    Salva histórico, quebra mensagens longas, extrai blocos de código e
    garante a destruição física compulsória de cada arquivo em try...finally.
    """
    message = update.message
    chat_id = message.chat_id

    # 1. Salva a resposta no banco de dados SQLite
    await database.add_message(session_id=session_id, role="assistant", content=ai_response)

    # 2. Envia a resposta dividida em pedaços se exceder o limite do Telegram
    chunks = split_message(ai_response, max_length=4000)
    for chunk in chunks:
        try:
            await message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await message.reply_text(chunk)

    # 3. Extrai blocos de código e envia como arquivo com LIMPEZA COMPULSÓRIA (Bug Fix 4)
    code_blocks = extract_code_blocks(ai_response)
    if code_blocks:
        for idx, (lang, code, ext) in enumerate(code_blocks, start=1):
            file_path: Path | None = None
            try:
                # Salva o arquivo temporariamente no disco
                file_path = save_code_to_file(
                    code=code,
                    extension=ext,
                    index=idx,
                    prefix=f"{session_name}_code",
                )

                # Envia documento anexo para o chat do Telegram
                with open(file_path, "rb") as doc:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=doc,
                        filename=file_path.name,
                        caption=f"📁 <b>Código extraído #{idx}</b> ({lang.upper()})",
                        parse_mode=ParseMode.HTML,
                    )
            except Exception as e:
                logger.error(f"Erro ao enviar arquivo de código {file_path}: {e}")
                filename_display = file_path.name if file_path else "código"
                await message.reply_text(f"⚠️ Falha no envio do anexo `{filename_display}`: {e}")
            finally:
                # Destruição física obrigatória via os.remove() independentemente de sucesso ou falha
                if file_path:
                    safe_remove_file(file_path)
                    logger.debug(f"Arquivo temporário de código destruído: {file_path}")


# =====================================================================
# ROTEADOR PRINCIPAL (DISPATCHER)
# =====================================================================

async def dispatch_user_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_id: int,
    session_name: str,
    user_text: str,
) -> None:
    """
    Roteador semântico de intenções: combina pré-processamento semântico
    e Tool Calling nativo do Ollama, despachando para as funções Python específicas.
    """
    # 1. Pré-processamento semântico em linguagem natural
    intent, params = detect_semantic_intent(user_text)

    if intent == "generate_image":
        prompt = params.get("prompt", user_text)
        await handle_image_generation(update, context, session_id, prompt)
        return

    if intent == "create_routine":
        details = params.get("details", user_text)
        await handle_routine_creation(update, context, session_id, details)
        return

    # 2. Se não caiu em regra direta de pré-processamento, consulta o Ollama com Tool Calling
    history = await database.get_session_messages(session_id=session_id, limit=MAX_CONTEXT_MESSAGES)

    response_data = await ollama_client.chat_completion(
        messages=history,
        tools=ollama_client.AVAILABLE_TOOLS,
    )

    tool_calls = response_data.get("tool_calls", [])

    # Se o LLM ativou Tool Calling nativo:
    if tool_calls:
        for call in tool_calls:
            func = call.get("function", {})
            name = func.get("name")
            args = func.get("arguments", {})

            # Trata se os argumentos vierem serializados em string JSON
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            if name == "generate_image":
                prompt = args.get("prompt", user_text)
                await handle_image_generation(update, context, session_id, prompt)
                return

            if name == "create_routine":
                details = args.get("details", user_text)
                await handle_routine_creation(update, context, session_id, details)
                return

    # 3. Caso normal de conversação / chat
    ai_content = response_data.get("content", "")
    await handle_text_chat(
        update=update,
        context=context,
        session_id=session_id,
        session_name=session_name,
        ai_response=ai_content,
    )

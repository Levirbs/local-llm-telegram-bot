import sys
import logging
from telegram import BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    Application,
)

import config
import database
from handlers import (
    start_command,
    help_command,
    novo_command,
    listar_command,
    trocar_command,
    apagar_command,
    renomear_command,
    trocarsenha_command,
    handle_audio_message,
    handle_photo_message,
    handle_message,
)

# Configuração de Logging estruturado
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telegram_bot")


async def post_init(application: Application) -> None:
    """Configurações executadas após a inicialização do bot."""
    # 1. Inicializa o banco de dados SQLite, migrações e senha master
    await database.init_db()
    logger.info("Banco de dados SQLite inicializado e verificado com sucesso.")

    # 2. Registra os comandos no menu nativo do aplicativo Telegram
    commands = [
        BotCommand("novo", "Cria e ativa uma nova sessão"),
        BotCommand("listar", "Lista todas as sessões salvas"),
        BotCommand("trocar", "Troca a sessão ativa"),
        BotCommand("apagar", "Deleta uma sessão e seu histórico"),
        BotCommand("renomear", "Renomeia a sessão atual"),
        BotCommand("trocarsenha", "Altera a senha master de acesso"),
        BotCommand("ajuda", "Exibe ajuda e lista de funcionalidades"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Menu de comandos nativos do Telegram configurado com sucesso.")


def main() -> None:
    """Ponto de entrada principal da aplicação."""
    token = config.TELEGRAM_BOT_TOKEN
    if not token or token == "seu_token_aqui":
        logger.error(
            "ERRO: TELEGRAM_BOT_TOKEN não foi configurado!\n"
            "Por favor, configure seu token no arquivo .env."
        )
        sys.exit(1)

    logger.info(
        f"Iniciando bot Telegram integrado ao Ollama ({config.OLLAMA_MODEL} em {config.OLLAMA_BASE_URL}) "
        f"e Whisper Offline ({config.WHISPER_MODEL_SIZE})..."
    )

    # Inicialização do ApplicationBuilder (python-telegram-bot v20+)
    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    # Registro de Handlers de Comandos
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ajuda", help_command))
    app.add_handler(CommandHandler("novo", novo_command))
    app.add_handler(CommandHandler("listar", listar_command))
    app.add_handler(CommandHandler("trocar", trocar_command))
    app.add_handler(CommandHandler("apagar", apagar_command))
    app.add_handler(CommandHandler("renomear", renomear_command))
    app.add_handler(CommandHandler("trocarsenha", trocarsenha_command))

    # Registro de Handler para Fotos e Imagens (Visão Multimodal com Ollama)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo_message))

    # Registro de Handler para Mensagens de Voz e Áudio (Offline Whisper)
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio_message))

    # Registro do Handler para Mensagens de Texto (Roteamento Semântico e Autenticação)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Inicia o polling de mensagens
    logger.info("Bot está no ar! Aguardando interações...")
    app.run_polling()


if __name__ == "__main__":
    main()

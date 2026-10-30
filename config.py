import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env se existir
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# Configurações do Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Senha Master de Autenticação
BOT_MASTER_PASSWORD = os.getenv("BOT_MASTER_PASSWORD", "admin123").strip()

# Configurações do Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:latest").strip()
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "180"))

# Configurações do Banco SQLite
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "sessions.db")))

# Configurações do Whisper (Offline Speech-to-Text)
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base").strip()
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip()
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip()

# Histórico e arquivos temporários
MAX_CONTEXT_MESSAGES = int(os.getenv("MAX_CONTEXT_MESSAGES", "20"))
TEMP_FILES_DIR = Path(os.getenv("TEMP_FILES_DIR", str(BASE_DIR / "extracted_code")))
TEMP_AUDIO_DIR = Path(os.getenv("TEMP_AUDIO_DIR", str(BASE_DIR / "temp_audio")))

# Garante que os diretórios temporários existam
TEMP_FILES_DIR.mkdir(parents=True, exist_ok=True)
TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

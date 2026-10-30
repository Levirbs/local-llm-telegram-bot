import os
import asyncio
import logging
import datetime
from pathlib import Path
from typing import Optional, Union

# Desativa alertas irrelevantes de symlinks do HuggingFace no Windows
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# =====================================================================
# HOTFIX DE COMPATIBILIDADE: PyAV >= 15 / 19 com faster-whisper
# No PyAV recente, o parâmetro 'metadata_errors' foi descontinuado em av.open().
# Esse patch intercepta a chamada de forma transparente sem quebrar nada.
# =====================================================================
try:
    import av
    _original_av_open = av.open

    def _patched_av_open(*args, **kwargs):
        kwargs.pop("metadata_errors", None)
        return _original_av_open(*args, **kwargs)

    av.open = _patched_av_open
except Exception as _e:
    pass

from config import (
    WHISPER_MODEL_SIZE,
    WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE,
    TEMP_AUDIO_DIR,
)

logger = logging.getLogger(__name__)

# Instância Singleton do modelo faster-whisper
_whisper_model = None


def get_whisper_model():
    """
    Carrega o modelo faster-whisper de maneira lazy e singleton para não onerar
    memória antes do primeiro uso. Utiliza o cache local já baixado.
    """
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        logger.info(
            f"Carregando faster-whisper (model={WHISPER_MODEL_SIZE}, "
            f"device={WHISPER_DEVICE}, compute_type={WHISPER_COMPUTE_TYPE})..."
        )
        try:
            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
            logger.info("Modelo faster-whisper carregado com sucesso.")
        except Exception as e:
            logger.warning(
                f"Falha ao carregar faster-whisper com compute_type={WHISPER_COMPUTE_TYPE} no device={WHISPER_DEVICE}. "
                f"Tentando fallback para CPU/int8: {e}"
            )
            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device="cpu",
                compute_type="int8",
            )
            logger.info("Modelo faster-whisper carregado via fallback CPU.")
    return _whisper_model


def _transcribe_sync(audio_path: str) -> str:
    """Execução síncrona da inferência de transcrição pelo faster-whisper."""
    model = get_whisper_model()
    # segments é um gerador de segmentos transcritos
    segments, info = model.transcribe(
        audio_path,
        beam_size=5,
        vad_filter=True,  # Voice Activity Detection para descartar silêncio
    )
    logger.info(f"Áudio transcrito. Idioma detectado: {info.language} (probabilidade: {info.language_probability:.2f})")

    transcribed_text = " ".join([seg.text.strip() for seg in segments if seg.text.strip()]).strip()
    return transcribed_text


async def transcribe_audio(audio_path: Union[str, Path]) -> str:
    """
    Transcreve um arquivo de áudio (.ogg, .mp3, etc.) de forma assíncrona,
    despachando o processamento pesado de CPU/GPU para uma thread separada.
    """
    audio_str = str(audio_path)
    if not os.path.exists(audio_str):
        raise FileNotFoundError(f"Arquivo de áudio não encontrado: {audio_str}")

    return await asyncio.to_thread(_transcribe_sync, audio_str)


async def download_telegram_audio(bot, file_id: str, extension: str = ".ogg") -> Path:
    """
    Baixa um arquivo de áudio ou voz do Telegram e salva no diretório temporário configurado.
    
    Returns:
        Path: Caminho do arquivo de áudio temporário salvo no disco.
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    temp_path = TEMP_AUDIO_DIR / f"voice_{timestamp}{extension}"

    tg_file = await bot.get_file(file_id)
    await tg_file.download_to_drive(custom_path=temp_path)
    logger.debug(f"Arquivo de áudio baixado: {temp_path}")
    return temp_path

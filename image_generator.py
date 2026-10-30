import os
import re
import datetime
import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config import TEMP_FILES_DIR

logger = logging.getLogger(__name__)


def generate_image_file(prompt: str) -> Path:
    """
    Gera uma imagem ilustrativa estilizada localmente via Pillow baseada no prompt solicitado.
    Cria a imagem no diretório temporário para ser enviada e compulsoriamente excluída após o upload.
    """
    width, height = 1024, 600
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_path = TEMP_FILES_DIR / f"generated_img_{timestamp}.png"

    # Cria canvas com fundo em degradê moderno (dark gradient)
    image = Image.new("RGB", (width, height), color=(20, 24, 33))
    draw = ImageDraw.Draw(image)

    # Efeito de gradiente de fundo suave
    for y in range(height):
        r = int(20 + (y / height) * 25)
        g = int(24 + (y / height) * 35)
        b = int(38 + (y / height) * 55)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Moldura decorativa
    draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(60, 110, 180), width=2)
    draw.rectangle([(26, 26), (width - 26, height - 26)], outline=(40, 70, 120), width=1)

    # Elementos visuais e textos
    badge_text = "AI IMAGE GENERATOR • QWEN / OLLAMA"
    draw.text((45, 45), badge_text, fill=(100, 200, 255))

    title_text = "🎨 Imagem Gerada por Intenção Semântica"
    draw.text((45, 75), title_text, fill=(240, 245, 255))

    draw.line([(45, 115), (width - 45, 115)], fill=(70, 90, 130), width=1)

    # Quebra de linhas do prompt do usuário
    clean_prompt = prompt.strip()
    words = clean_prompt.split()
    lines = []
    current_line = []

    for word in words:
        current_line.append(word)
        if len(" ".join(current_line)) > 55:
            lines.append(" ".join(current_line[:-1]))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))

    # Desenha o prompt
    draw.text((45, 140), "Prompt Solicitado:", fill=(180, 200, 220))
    y_pos = 180
    for line in lines[:8]:
        draw.text((45, y_pos), f'"{line}"', fill=(255, 255, 255))
        y_pos += 36

    # Rodapé informativo
    footer_text = f"Gerado em: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')} | Processamento Local Seguro"
    draw.text((45, height - 55), footer_text, fill=(120, 140, 170))

    image.save(output_path, "PNG")
    logger.info(f"Imagem local gerada com sucesso: {output_path}")
    return output_path

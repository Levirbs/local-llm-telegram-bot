import os
import re
import datetime
import logging
from pathlib import Path
from typing import List, Tuple, Optional, Union
from config import TEMP_FILES_DIR

logger = logging.getLogger(__name__)

# Mapeamento extensivo de tags de linguagens para extensões de arquivos
LANGUAGE_EXTENSIONS = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "html": ".html",
    "css": ".css",
    "json": ".json",
    "sql": ".sql",
    "bash": ".sh",
    "sh": ".sh",
    "shell": ".sh",
    "powershell": ".ps1",
    "ps1": ".ps1",
    "batch": ".bat",
    "bat": ".bat",
    "cmd": ".cmd",
    "c": ".c",
    "cpp": ".cpp",
    "c++": ".cpp",
    "csharp": ".cs",
    "cs": ".cs",
    "java": ".java",
    "rust": ".rs",
    "rs": ".rs",
    "go": ".go",
    "golang": ".go",
    "ruby": ".rb",
    "rb": ".rb",
    "php": ".php",
    "yaml": ".yaml",
    "yml": ".yml",
    "dockerfile": ".dockerfile",
    "markdown": ".md",
    "md": ".md",
    "xml": ".xml",
    "r": ".r",
    "swift": ".swift",
    "kotlin": ".kt",
    "kt": ".kt",
    "dart": ".dart",
    "lua": ".lua",
    "text": ".txt",
    "txt": ".txt",
}


def extract_code_blocks(text: str) -> List[Tuple[str, str, str]]:
    """
    Varre o texto da resposta procurando blocos de código formatados em Markdown.
    
    Retorna uma lista de tuplas: (linguagem_detectada, codigo_puro, extensao_do_arquivo)
    """
    pattern = r"```([a-zA-Z0-9_\-+]*)\n?(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)

    extracted = []
    for lang_tag, code_content in matches:
        code_clean = code_content.strip()
        if not code_clean:
            continue

        lang_tag_clean = lang_tag.strip().lower()
        ext = LANGUAGE_EXTENSIONS.get(lang_tag_clean, ".txt")
        display_lang = lang_tag_clean if lang_tag_clean else "código"

        extracted.append((display_lang, code_clean, ext))

    return extracted


def save_code_to_file(code: str, extension: str, index: int, prefix: str = "snippet") -> Path:
    """
    Salva o código extraído em um arquivo local dentro do diretório TEMP_FILES_DIR.
    
    Returns:
        Path: Caminho do arquivo gerado
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_prefix = re.sub(r'[^a-zA-Z0-9_-]', '_', prefix)
    filename = f"{safe_prefix}_{index}_{timestamp}{extension}"
    file_path = TEMP_FILES_DIR / filename

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)

    return file_path


def safe_remove_file(file_path: Optional[Union[str, Path]]) -> bool:
    """
    Remove compulsoriamente um arquivo do disco garantindo destruição física sem levantar exceção.
    
    Returns:
        bool: True se o arquivo foi removido com sucesso, False caso contrário.
    """
    if not file_path:
        return False
    try:
        path_obj = Path(file_path)
        if path_obj.exists():
            os.remove(path_obj)
            logger.debug(f"Arquivo temporário removido com sucesso: {path_obj}")
            return True
        return False
    except Exception as e:
        logger.warning(f"Falha ao remover arquivo temporário {file_path}: {e}")
        return False


def split_message(text: str, max_length: int = 4000) -> List[str]:
    """
    Divide mensagens longas em pedaços de no máximo `max_length` caracteres,
    respeitando quebras de linha para não quebrar frases ao meio.
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_length = 0

    for line in lines:
        line_len = len(line) + 1  # +1 por conta da quebra de linha '\n'
        if current_length + line_len > max_length:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_length = 0

            # Caso uma única linha seja maior do que o limite permitido
            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]

            if line:
                current_chunk.append(line)
                current_length = len(line)
        else:
            current_chunk.append(line)
            current_length += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks

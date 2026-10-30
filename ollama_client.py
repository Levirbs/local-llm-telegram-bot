import logging
from typing import List, Dict, Any, Optional
import httpx
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """Exceção base para erros de comunicação com o Ollama."""
    pass


# Definição das ferramentas (Tool Calling Schema - compatível com Ollama / OpenAI)
AVAILABLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Acione quando o usuário pedir para gerar, criar, desenhar ou ilustrar uma imagem, foto, arte visual ou wallpaper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Descrição detalhada e visual da imagem que deve ser gerada.",
                    }
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_routine",
            "description": "Acione quando o usuário solicitar para criar, montar, planejar ou organizar uma rotina (de estudos, treinos, hábitos, trabalho, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "routine_title": {
                        "type": "string",
                        "description": "Título ou objetivo principal da rotina.",
                    },
                    "details": {
                        "type": "string",
                        "description": "Preferências, horários ou restrições mencionadas pelo usuário.",
                    },
                },
                "required": ["routine_title"],
            },
        },
    },
]


async def chat_completion(
    messages: List[Dict[str, Any]],
    model: str = OLLAMA_MODEL,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Envia o histórico de mensagens (incluindo imagens base64 se fornecidas)
    para a API do Ollama e retorna a resposta estruturada.
    
    Returns:
        dict: {"content": str, "tool_calls": list}
    """
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.post(url, json=payload)

            # Caso o modelo instalado no Ollama não suporte o parâmetro 'tools'
            if response.status_code == 400 and tools and "does not support tools" in response.text.lower():
                logger.warning(f"Modelo '{model}' não suporta tool calling nativo. Executando chat comum...")
                payload.pop("tools", None)
                response = await client.post(url, json=payload)

            if response.status_code == 404:
                raise OllamaError(
                    f"Modelo '{model}' não encontrado no Ollama. Execute 'ollama pull {model}' no terminal ou confira o .env."
                )

            if response.status_code == 500:
                raise OllamaError(f"Erro no serviço do Ollama: 500 - {response.text}")

            response.raise_for_status()
            data = response.json()
            message_data = data.get("message", {})
            return {
                "content": message_data.get("content", "") or "",
                "tool_calls": message_data.get("tool_calls", []) or [],
            }

    except httpx.ConnectError:
        logger.error(f"Falha de conexão com o Ollama em {OLLAMA_BASE_URL}")
        raise OllamaError(
            f"Não foi possível conectar ao Ollama em {OLLAMA_BASE_URL}. Verifique se o serviço está rodando (`ollama serve`)."
        )
    except httpx.TimeoutException:
        logger.error(f"Timeout ao aguardar resposta do Ollama ({OLLAMA_TIMEOUT}s)")
        raise OllamaError(
            f"O modelo demorou mais de {int(OLLAMA_TIMEOUT)}s para responder. Verifique os recursos da máquina."
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"Erro HTTP do Ollama: {e.response.text}")
        raise OllamaError(f"Erro no serviço do Ollama: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        if isinstance(e, OllamaError):
            raise
        logger.exception("Erro inesperado ao consultar o Ollama")
        raise OllamaError(f"Erro inesperado: {str(e)}")

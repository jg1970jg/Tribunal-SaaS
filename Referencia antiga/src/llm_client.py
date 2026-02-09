# -*- coding: utf-8 -*-
"""
Cliente LLM UNIFICADO - Dual API System
═══════════════════════════════════════════════════════════════════════════

FUNCIONALIDADES:
1. ✅ API OpenAI directa (para modelos OpenAI - usa saldo OpenAI)
2. ✅ API OpenRouter (para modelos outros - Anthropic, Google, etc.)
3. ✅ Fallback automático (se OpenAI falhar → OpenRouter)
4. ✅ Detecção automática de modelo
5. ✅ Logging detalhado
"""

import base64
import httpx
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Union
from dataclasses import dataclass, field
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    retry_if_exception,
)

import os


def _is_retryable_http_error(exception: BaseException) -> bool:
    """
    Verifica se um erro HTTP deve ser tentado novamente.

    NÃO faz retry em erros de cliente (400, 401, 403, 404) pois nunca vão funcionar.
    Faz retry em erros de servidor (429, 500, 502, 503, 504) e timeouts.
    """
    if isinstance(exception, httpx.TimeoutException):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        status = exception.response.status_code
        # Retry apenas em rate limit (429) e erros de servidor (5xx)
        return status == 429 or status >= 500
    return False

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# MODELOS OPENAI QUE USAM RESPONSES API
# =============================================================================
# GPT-5.2 e GPT-5.2-pro usam Responses API (/v1/responses) em vez de Chat API
# Implementamos suporte nativo para Responses API
OPENAI_MODELS_USE_RESPONSES_API = [
    "gpt-5.2",
    "gpt-5.2-pro",
    "gpt-5.2-2025-12-11",
    "gpt-5.2-pro-2025-12-11",
]

# Modelos de reasoning que NÃO suportam o parâmetro temperature
OPENAI_MODELS_NO_TEMPERATURE = [
    "gpt-5.2-pro",
    "gpt-5.2-pro-2025-12-11",
    "o1",
    "o1-pro",
    "o3",
    "o3-pro",
]


@dataclass
class LLMResponse:
    """Resposta de uma chamada LLM."""
    content: str
    model: str
    role: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    raw_response: Optional[Dict] = None
    error: Optional[str] = None
    success: bool = True
    api_used: str = ""  # "openai" ou "openrouter"

    def to_dict(self) -> Dict:
        return {
            "content": self.content,
            "model": self.model,
            "role": self.role,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "timestamp": self.timestamp.isoformat(),
            "error": self.error,
            "success": self.success,
            "api_used": self.api_used,
        }


def is_openai_model(model_name: str) -> bool:
    """
    Detecta se um modelo é da OpenAI.
    
    Args:
        model_name: Nome do modelo (ex: "openai/gpt-5.2-pro" ou "gpt-5.2-pro")
    
    Returns:
        True se for modelo OpenAI
    """
    # Remove prefixo se existir
    clean_name = model_name.replace("openai/", "").lower()
    
    # Lista de padrões OpenAI
    openai_patterns = [
        "gpt-5",
        "gpt-4",
        "gpt-3",
        "o1",
        "o3",
    ]
    
    return any(pattern in clean_name for pattern in openai_patterns)


def uses_responses_api(model_name: str) -> bool:
    """
    Verifica se modelo usa Responses API em vez de Chat API.
    
    Args:
        model_name: Nome do modelo (ex: "openai/gpt-5.2")
    
    Returns:
        True se usa Responses API
    """
    clean_name = model_name.replace("openai/", "").lower()

    for responses_model in OPENAI_MODELS_USE_RESPONSES_API:
        if responses_model.lower() in clean_name:
            return True

    return False


def supports_temperature(model_name: str) -> bool:
    """
    Verifica se o modelo suporta o parâmetro temperature.

    Modelos de reasoning (pro, o1, o3) NÃO suportam temperature.
    """
    clean_name = model_name.replace("openai/", "").lower()
    for no_temp_model in OPENAI_MODELS_NO_TEMPERATURE:
        if no_temp_model.lower() in clean_name:
            return False
    return True


def should_use_openai_direct(model_name: str) -> bool:
    """
    Verifica se modelo OpenAI deve usar API OpenAI directa.
    
    TODOS os modelos OpenAI usam OpenAI directa agora!
    (Incluindo GPT-5.2 via Responses API)
    
    Args:
        model_name: Nome do modelo (ex: "openai/gpt-5.2-pro")
    
    Returns:
        True se deve usar OpenAI directa
    """
    # Simplesmente verifica se é modelo OpenAI
    return is_openai_model(model_name)


def normalize_model_name(model_name: str, for_api: str = "openai") -> str:
    """
    Normaliza nome do modelo para cada API.
    
    Args:
        model_name: Nome original (ex: "openai/gpt-5.2-pro")
        for_api: "openai" ou "openrouter"
    
    Returns:
        Nome correcto para a API
    """
    if for_api == "openai":
        # OpenAI API: sem prefixo
        return model_name.replace("openai/", "")
    else:
        # OpenRouter API: com prefixo
        if not model_name.startswith("openai/") and is_openai_model(model_name):
            return f"openai/{model_name}"
        return model_name


class OpenAIClient:
    """
    Cliente para API OpenAI directa.
    
    Usa: https://api.openai.com
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 180,
        max_retries: int = 5,
    ):
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self.base_url = "https://api.openai.com/v1"
        self.timeout = timeout
        self.max_retries = max_retries

        if not self.api_key:
            logger.warning("OPENAI_API_KEY não configurada!")

        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers=self._get_headers(),
        )

        # Estatísticas
        self._stats = {
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "total_tokens": 0,
            "total_latency_ms": 0,
        }

    def _get_headers(self) -> Dict[str, str]:
        """Retorna headers para a API."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _make_request(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> Dict[str, Any]:
        """Faz uma requisição à API Chat Completions com retry automático."""
        url = f"{self.base_url}/chat/completions"

        # Normalizar nome do modelo (sem prefixo openai/)
        clean_model = normalize_model_name(model, for_api="openai")

        payload = {
            "model": clean_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        logger.debug(f"OpenAI Request para {clean_model}: {len(str(messages))} chars")

        response = self._client.post(url, json=payload)
        response.raise_for_status()

        return response.json()

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _make_request_responses(
        self,
        model: str,
        input_text: str,
        instructions: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: int = 16384,
    ) -> Dict[str, Any]:
        """
        Faz requisição à API Responses (/v1/responses) com retry automático.

        Esta API é usada por modelos como GPT-5.2 e GPT-5.2-pro.
        NOTA: Responses API usa 'max_output_tokens' (não 'max_tokens')
              e 'instructions' para system prompt.
        """
        url = f"{self.base_url}/responses"

        # Normalizar nome do modelo
        clean_model = normalize_model_name(model, for_api="openai")

        payload = {
            "model": clean_model,
            "input": input_text,
            "max_output_tokens": max(max_output_tokens, 16),  # Mínimo 16 na Responses API
        }

        # Modelos de reasoning (pro, o1, o3) NÃO suportam temperature
        if supports_temperature(model):
            payload["temperature"] = temperature

        # Adicionar instructions (system prompt) se fornecido
        if instructions:
            payload["instructions"] = instructions

        logger.debug(f"OpenAI Responses Request para {clean_model}: {len(input_text)} chars")

        response = self._client.post(url, json=payload)
        response.raise_for_status()

        return response.json()

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """
        Envia mensagens para um modelo e retorna a resposta.
        """
        self._stats["total_calls"] += 1
        start_time = datetime.now()

        # Adicionar system prompt se fornecido
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        try:
            logger.info(f"🔵 Chamando OpenAI API: {model}")

            raw_response = self._make_request(
                model=model,
                messages=full_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Extrair resposta
            choice = raw_response.get("choices", [{}])[0]
            message = choice.get("message", {})
            usage = raw_response.get("usage", {})

            latency_ms = (datetime.now() - start_time).total_seconds() * 1000

            response = LLMResponse(
                content=message.get("content", ""),
                model=raw_response.get("model", model),
                role=message.get("role", "assistant"),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                latency_ms=latency_ms,
                raw_response=raw_response,
                success=True,
                api_used="openai"
            )

            self._stats["successful_calls"] += 1
            self._stats["total_tokens"] += response.total_tokens
            self._stats["total_latency_ms"] += latency_ms

            logger.info(f"✅ OpenAI resposta: {response.total_tokens} tokens, {latency_ms:.0f}ms")

            return response

        except Exception as e:
            logger.error(f"❌ Erro OpenAI API: {e}")
            self._stats["failed_calls"] += 1
            
            # Retornar erro para fallback
            return LLMResponse(
                content="",
                model=model,
                role="assistant",
                error=str(e),
                success=False,
                api_used="openai"
            )

    def chat_simple(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> LLMResponse:
        """Versão simplificada de chat com apenas um prompt."""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def chat_responses(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """
        Envia mensagens para um modelo usando Responses API (/v1/responses).

        Esta API é usada por modelos como GPT-5.2 e GPT-5.2-pro.
        Usa 'instructions' para system prompt e 'input' para conteúdo do user.
        """
        self._stats["total_calls"] += 1
        start_time = datetime.now()

        # Extrair instructions (system prompt) separadamente
        instructions = system_prompt

        # Converter messages para input
        input_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                # Concatenar system messages às instructions
                if instructions:
                    instructions = f"{instructions}\n\n{content}"
                else:
                    instructions = content
            elif role == "user":
                input_parts.append(content)
            elif role == "assistant":
                input_parts.append(f"Assistant: {content}")

        input_text = "\n\n".join(input_parts)

        try:
            logger.info(f"🔵 Chamando OpenAI Responses API: {model}")

            raw_response = self._make_request_responses(
                model=model,
                input_text=input_text,
                instructions=instructions,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )

            # Extrair resposta (formato diferente!)
            # Responses API retorna: {"output_text": "...", "usage": {...}}
            output_text = raw_response.get("output_text", "")
            usage = raw_response.get("usage", {})

            latency_ms = (datetime.now() - start_time).total_seconds() * 1000

            response = LLMResponse(
                content=output_text,
                model=raw_response.get("model", model),
                role="assistant",
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                latency_ms=latency_ms,
                raw_response=raw_response,
                success=True,
                api_used="openai (responses)"
            )

            self._stats["successful_calls"] += 1
            self._stats["total_tokens"] += response.total_tokens
            self._stats["total_latency_ms"] += latency_ms

            logger.info(f"✅ OpenAI Responses resposta: {response.total_tokens} tokens, {latency_ms:.0f}ms")

            return response

        except Exception as e:
            logger.error(f"❌ Erro OpenAI Responses API: {e}")
            self._stats["failed_calls"] += 1
            
            # Retornar erro para fallback
            return LLMResponse(
                content="",
                model=model,
                role="assistant",
                error=str(e),
                success=False,
                api_used="openai (responses)"
            )


    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas de uso."""
        stats = self._stats.copy()
        if stats["successful_calls"] > 0:
            stats["avg_latency_ms"] = stats["total_latency_ms"] / stats["successful_calls"]
            stats["avg_tokens"] = stats["total_tokens"] / stats["successful_calls"]
        else:
            stats["avg_latency_ms"] = 0
            stats["avg_tokens"] = 0
        return stats

    def close(self):
        """Fecha o cliente HTTP."""
        self._client.close()


class OpenRouterClient:
    """
    Cliente para API OpenRouter.
    
    Usa: https://openrouter.ai/api/v1
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: int = 180,
        max_retries: int = 5,
    ):
        self.api_key = (api_key or os.getenv("OPENROUTER_API_KEY", "")).strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

        if not self.api_key:
            logger.warning("OPENROUTER_API_KEY não configurada!")

        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers=self._get_headers(),
        )

        # Estatísticas
        self._stats = {
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "total_tokens": 0,
            "total_latency_ms": 0,
        }

    def _get_headers(self) -> Dict[str, str]:
        """Retorna headers para a API."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://tribunal-goldenmaster.local",
            "X-Title": "Tribunal GoldenMaster GUI",
        }

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _make_request(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> Dict[str, Any]:
        """Faz uma requisição à API com retry automático."""
        url = f"{self.base_url}/chat/completions"

        # Normalizar nome do modelo (com prefixo openai/ se necessário)
        clean_model = normalize_model_name(model, for_api="openrouter")

        payload = {
            "model": clean_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        logger.debug(f"OpenRouter Request para {clean_model}: {len(str(messages))} chars")

        response = self._client.post(url, json=payload)
        response.raise_for_status()

        return response.json()

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Envia mensagens para um modelo e retorna a resposta."""
        self._stats["total_calls"] += 1
        start_time = datetime.now()

        # Adicionar system prompt se fornecido
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        try:
            logger.info(f"🟠 Chamando OpenRouter API: {model}")

            raw_response = self._make_request(
                model=model,
                messages=full_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Extrair resposta
            choice = raw_response.get("choices", [{}])[0]
            message = choice.get("message", {})
            usage = raw_response.get("usage", {})

            latency_ms = (datetime.now() - start_time).total_seconds() * 1000

            response = LLMResponse(
                content=message.get("content", ""),
                model=raw_response.get("model", model),
                role=message.get("role", "assistant"),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                latency_ms=latency_ms,
                raw_response=raw_response,
                success=True,
                api_used="openrouter"
            )

            self._stats["successful_calls"] += 1
            self._stats["total_tokens"] += response.total_tokens
            self._stats["total_latency_ms"] += latency_ms

            logger.info(f"✅ OpenRouter resposta: {response.total_tokens} tokens, {latency_ms:.0f}ms")

            return response

        except Exception as e:
            logger.error(f"❌ Erro OpenRouter API: {e}")
            self._stats["failed_calls"] += 1
            return LLMResponse(
                content="",
                model=model,
                role="assistant",
                error=str(e),
                success=False,
                api_used="openrouter"
            )

    def chat_simple(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> LLMResponse:
        """Versão simplificada de chat com apenas um prompt."""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas de uso."""
        stats = self._stats.copy()
        if stats["successful_calls"] > 0:
            stats["avg_latency_ms"] = stats["total_latency_ms"] / stats["successful_calls"]
            stats["avg_tokens"] = stats["total_tokens"] / stats["successful_calls"]
        else:
            stats["avg_latency_ms"] = 0
            stats["avg_tokens"] = 0
        return stats

    def close(self):
        """Fecha o cliente HTTP."""
        self._client.close()


class UnifiedLLMClient:
    """
    Cliente UNIFICADO que escolhe automaticamente a API correcta.
    
    FUNCIONALIDADES:
    1. Detecta se modelo é OpenAI ou outro
    2. Usa API apropriada (OpenAI directa ou OpenRouter)
    3. Fallback automático se OpenAI falhar
    """

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        openrouter_api_key: Optional[str] = None,
        timeout: int = 180,
        max_retries: int = 5,
        enable_fallback: bool = True,
    ):
        """
        Args:
            openai_api_key: API key OpenAI directa
            openrouter_api_key: API key OpenRouter
            timeout: Timeout em segundos
            max_retries: Número máximo de retries
            enable_fallback: Se True, usa fallback OpenRouter quando OpenAI falhar
        """
        self.enable_fallback = enable_fallback
        
        # Clientes
        self.openai_client = OpenAIClient(
            api_key=openai_api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
        
        self.openrouter_client = OpenRouterClient(
            api_key=openrouter_api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
        
        logger.info("✅ UnifiedLLMClient inicializado (Dual API + Fallback)")

    def chat_simple(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> LLMResponse:
        """
        Versão simplificada de chat.
        
        Detecta automaticamente qual API usar e implementa fallback.
        """
        messages = [{"role": "user", "content": prompt}]
        return self.chat(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def chat_vision(
        self,
        model: str,
        prompt: str,
        image_path: Union[str, Path],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """
        Chat com imagem (Vision) - envia imagem + prompt ao LLM.

        Usado para Vision OCR de PDFs escaneados.
        Formato multimodal compatível com OpenAI, OpenRouter, Claude, Gemini.
        """
        image_path = Path(image_path)
        if not image_path.exists():
            return LLMResponse(
                content="",
                model=model,
                role="assistant",
                error=f"Imagem não encontrada: {image_path}",
                success=False,
                api_used="none"
            )

        # Ler e codificar imagem em base64
        image_bytes = image_path.read_bytes()
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        # Determinar MIME type
        suffix = image_path.suffix.lower()
        mime_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
        mime_type = mime_types.get(suffix, "image/png")

        # Construir mensagem multimodal
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{b64_image}"
                    }
                }
            ]
        }]

        logger.info(f"🖼️ Vision OCR: enviando imagem {image_path.name} ({len(image_bytes):,} bytes) para {model}")

        return self.chat(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """
        Chat com detecção automática de API + fallback.
        
        1. Detecta se deve usar OpenAI directa
        2. Se OpenAI, detecta se usa Responses API ou Chat API
        3. Tenta API apropriada
        4. Se falhar E fallback habilitado → tenta OpenRouter
        """
        # Detectar se deve usar OpenAI directa
        use_openai_direct = should_use_openai_direct(model)
        
        if use_openai_direct:
            # Detectar qual API OpenAI usar
            use_responses = uses_responses_api(model)
            
            if use_responses:
                logger.info(f"🎯 Modelo OpenAI detectado: {model} (via Responses API)")
                
                # Tentar Responses API
                response = self.openai_client.chat_responses(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_prompt=system_prompt,
                )
            else:
                logger.info(f"🎯 Modelo OpenAI detectado: {model} (via Chat API)")
                
                # Tentar Chat API normal
                response = self.openai_client.chat(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_prompt=system_prompt,
                )
            
            # Se sucesso, retornar
            if response.success:
                return response
            
            # Se falhou E fallback habilitado
            if self.enable_fallback:
                logger.warning(f"⚠️ OpenAI API falhou: {response.error}")
                logger.info(f"🔄 Usando fallback OpenRouter...")
                
                # Tentar OpenRouter como backup
                response_fallback = self.openrouter_client.chat(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_prompt=system_prompt,
                )
                
                # Marcar que usou fallback
                if response_fallback.success:
                    logger.info("✅ Fallback OpenRouter bem-sucedido!")
                    response_fallback.api_used = "openrouter (fallback)"
                
                return response_fallback
            else:
                # Fallback desabilitado, retornar erro
                return response
        
        else:
            # Modelo não-OpenAI
            logger.info(f"🎯 Modelo não-OpenAI detectado: {model}")
            
            return self.openrouter_client.chat(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas combinadas de ambas APIs."""
        openai_stats = self.openai_client.get_stats()
        openrouter_stats = self.openrouter_client.get_stats()
        
        return {
            "openai": openai_stats,
            "openrouter": openrouter_stats,
            "total_calls": openai_stats["total_calls"] + openrouter_stats["total_calls"],
            "total_tokens": openai_stats["total_tokens"] + openrouter_stats["total_tokens"],
        }

    def test_connection(self) -> Dict[str, Any]:
        """
        Testa conexão com ambas APIs (OpenAI e OpenRouter).
        
        Returns:
            Dict com status de cada API:
            {
                "openai": {"success": bool, "message": str, "latency_ms": float},
                "openrouter": {"success": bool, "message": str, "latency_ms": float}
            }
        """
        results = {}
        
        # Testar OpenAI API
        logger.info("🔵 Testando OpenAI API...")
        try:
            start = datetime.now()
            response = self.openai_client.chat_simple(
                model="gpt-4o-mini",  # Modelo mais barato para teste
                prompt="Responde apenas: OK",
                temperature=0,
                max_tokens=5,
            )
            latency = (datetime.now() - start).total_seconds() * 1000
            
            if response.success:
                results["openai"] = {
                    "success": True,
                    "message": f"✅ Conexão bem-sucedida! ({latency:.0f}ms)",
                    "latency_ms": latency,
                    "model_used": response.model,
                }
                logger.info(f"✅ OpenAI API OK ({latency:.0f}ms)")
            else:
                results["openai"] = {
                    "success": False,
                    "message": f"❌ Erro: {response.error}",
                    "latency_ms": latency,
                }
                logger.error(f"❌ OpenAI API falhou: {response.error}")
        except Exception as e:
            results["openai"] = {
                "success": False,
                "message": f"❌ Exceção: {str(e)}",
                "latency_ms": 0,
            }
            logger.error(f"❌ OpenAI API exceção: {e}")
        
        # Testar OpenRouter API
        logger.info("🟠 Testando OpenRouter API...")
        try:
            start = datetime.now()
            response = self.openrouter_client.chat_simple(
                model="openai/gpt-4o-mini",  # Modelo barato via OpenRouter
                prompt="Responde apenas: OK",
                temperature=0,
                max_tokens=5,
            )
            latency = (datetime.now() - start).total_seconds() * 1000
            
            if response.success:
                results["openrouter"] = {
                    "success": True,
                    "message": f"✅ Conexão bem-sucedida! ({latency:.0f}ms)",
                    "latency_ms": latency,
                    "model_used": response.model,
                }
                logger.info(f"✅ OpenRouter API OK ({latency:.0f}ms)")
            else:
                results["openrouter"] = {
                    "success": False,
                    "message": f"❌ Erro: {response.error}",
                    "latency_ms": latency,
                }
                logger.error(f"❌ OpenRouter API falhou: {response.error}")
        except Exception as e:
            results["openrouter"] = {
                "success": False,
                "message": f"❌ Exceção: {str(e)}",
                "latency_ms": 0,
            }
            logger.error(f"❌ OpenRouter API exceção: {e}")
        
        return results

    def close(self):
        """Fecha ambos os clientes."""
        self.openai_client.close()
        self.openrouter_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Cliente global singleton
_global_client: Optional[UnifiedLLMClient] = None


def get_llm_client() -> UnifiedLLMClient:
    """
    Retorna o cliente LLM global unificado.
    
    IMPORTANTE: Este é o cliente usado por todo o programa!
    """
    global _global_client
    if _global_client is None:
        _global_client = UnifiedLLMClient(
            openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
            openrouter_api_key=(os.getenv("OPENROUTER_API_KEY") or "").strip(),
            enable_fallback=True,
        )
    return _global_client


def call_llm(
    model: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 16384,
) -> LLMResponse:
    """
    Função de conveniência para chamar um LLM.
    
    Usa o cliente unificado com detecção automática + fallback.
    """
    client = get_llm_client()
    return client.chat_simple(
        model=model,
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )

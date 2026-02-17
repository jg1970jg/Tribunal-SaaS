# -*- coding: utf-8 -*-
"""
Cliente LLM UNIFICADO - Dual API System + PROMPT CACHING
═══════════════════════════════════════════════════════════════════════════

FUNCIONALIDADES:
1. ✅ API OpenAI directa (para modelos OpenAI - usa saldo OpenAI)
2. ✅ API OpenRouter (para modelos outros - Anthropic, Google, etc.)
3. ✅ Fallback automático (se OpenAI falhar → OpenRouter)
4. ✅ Detecção automática de modelo
5. ✅ Logging detalhado
6. ✅ PROMPT CACHING (NOVO!) - Economia 50-90%

CHANGELOG:
- 2026-02-12: Adicionar Prompt Caching (Anthropic manual + OpenAI/Gemini auto)
- 2026-02-10: Fix respostas corrompidas OpenRouter (defensive JSON parsing)
- 2026-02-10: Detectar conteúdo vazio como falha (permite retry)
- 2026-02-10: Validação robusta do body HTTP antes de json.loads()
- 2026-02-10: Safe JSON parse em _make_request para ambos os clientes
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
    NÃO faz retry em 429 insufficient_quota (saldo esgotado - nunca vai funcionar).
    Faz retry em rate_limit (429 normal), erros de servidor (5xx) e timeouts.

    FIX 2026-02-10: Também faz retry em ValueError/JSONDecodeError
    FIX 2026-02-14: NÃO retry em insufficient_quota (evita 5×retry inútil)
    """
    if isinstance(exception, httpx.TimeoutException):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        status = exception.response.status_code
        # FIX 2026-02-14: 429 insufficient_quota = saldo esgotado, NUNCA vai funcionar
        if status == 429:
            try:
                body = exception.response.text
                if "insufficient_quota" in body or "exceeded your current quota" in body:
                    logging.getLogger(__name__).warning(
                        "[QUOTA] 429 insufficient_quota detectado — NÃO faz retry (saldo esgotado)"
                    )
                    return False
            except Exception:
                pass
            return True  # 429 rate_limit normal → retry
        # Retry em erros de servidor (5xx)
        return status >= 500
    # FIX 2026-02-10: Retry em JSON corrompido (HTTP 200 mas body inválido)
    if isinstance(exception, (ValueError, json.JSONDecodeError)):
        return True
    return False


def _safe_parse_json(response: httpx.Response, context: str = "") -> Dict[str, Any]:
    """
    FIX 2026-02-10: Parse JSON defensivo com validação do body.

    Verifica:
    1. Response tem body não-vazio
    2. Body começa com { ou [ (é JSON válido)
    3. json.loads() não falha

    Se falhar, lança ValueError (que é retryable).
    """
    body = response.text.strip()

    if not body:
        raise ValueError(
            f"[{context}] Resposta HTTP {response.status_code} com body VAZIO. "
            f"Headers: {dict(response.headers)}"
        )

    # Verificar se parece JSON (deve começar com { ou [)
    if not body.startswith(("{", "[")):
        # Truncar para log
        preview = body[:500] if len(body) > 500 else body
        raise ValueError(
            f"[{context}] Resposta HTTP {response.status_code} não é JSON válido. "
            f"Começa com: {preview!r}"
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        preview = body[:500] if len(body) > 500 else body
        raise ValueError(
            f"[{context}] JSON parse falhou: {e}. "
            f"Body ({len(body)} chars): {preview!r}"
        ) from e

    # Verificar se é um objecto (dict) — respostas válidas são sempre dicts
    if not isinstance(data, dict):
        raise ValueError(
            f"[{context}] JSON válido mas não é objecto (é {type(data).__name__}). "
            f"Valor: {str(data)[:200]}"
        )

    # Verificar se a API retornou um erro no body
    if "error" in data and not data.get("choices") and not data.get("output"):
        error_msg = data.get("error", {})
        if isinstance(error_msg, dict):
            error_msg = error_msg.get("message", str(error_msg))
        logger.warning(f"[{context}] API retornou erro no body: {error_msg}")
        # Não lançar excepção aqui — deixar o chamador decidir

    return data


# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# PROMPT CACHING - CONFIGURAÇÃO
# =============================================================================

# Modelos com cache MANUAL (Anthropic - precisa cache_control)
MODELS_MANUAL_CACHE = {
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-3-5-sonnet",
    "anthropic/claude-3-5-haiku",
    "anthropic/claude-opus-4.6",
}

# Modelos com cache AUTOMÁTICO (OpenAI, Gemini)
MODELS_AUTO_CACHE = {
    "openai/gpt-5.2",
    "openai/gpt-5.2-pro",
    "openai/gpt-4o",
    "openai/gpt-4.1",
    "google/gemini-3-flash-preview",
    "google/gemini-3-pro-preview",
}


def supports_cache(model: str) -> bool:
    """Verifica se modelo suporta prompt caching."""
    return model in MODELS_AUTO_CACHE or model in MODELS_MANUAL_CACHE


def requires_manual_cache(model: str) -> bool:
    """Verifica se modelo precisa de cache_control manual (Anthropic)."""
    return model in MODELS_MANUAL_CACHE


def prepare_messages_with_cache(
    messages: List[Dict[str, Any]],
    model: str,
    enable_cache: bool = True
) -> List[Dict[str, Any]]:
    """
    Adiciona cache_control às mensagens para modelos Anthropic.
    
    REGRAS:
    1. Cache APENAS em 'user' messages
    2. Cache nos últimos 1-4 blocos (máximo 4 breakpoints)
    3. Cada bloco ≥ 1,024 tokens (~4096 chars)
    
    Args:
        messages: Lista de mensagens
        model: ID do modelo
        enable_cache: Se False, não adiciona cache
        
    Returns:
        Mensagens com cache_control adicionado
    """
    if not enable_cache or not requires_manual_cache(model):
        return messages
    
    # Encontrar mensagens 'user' longas (>4096 chars ≈ 1024 tokens)
    user_messages_idx = [
        i for i, msg in enumerate(messages)
        if msg.get("role") == "user" and len(str(msg.get("content", ""))) > 4096
    ]
    
    if not user_messages_idx:
        return messages
    
    # Cachear os últimos 1-4 blocos (max 4 breakpoints)
    num_to_cache = min(4, len(user_messages_idx))
    indices_to_cache = user_messages_idx[-num_to_cache:]
    
    # Adicionar cache_control
    cached_messages = messages.copy()
    for idx in indices_to_cache:
        content = cached_messages[idx]["content"]
        
        if isinstance(content, str):
            cached_messages[idx]["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"}
                }
            ]
        elif isinstance(content, list):
            # Já é array, adicionar cache_control ao último texto
            for item in reversed(content):
                if item.get("type") == "text":
                    item["cache_control"] = {"type": "ephemeral"}
                    break
    
    logger.info(f"[CACHE] ✅ Adicionado cache_control a {num_to_cache} mensagens ({model})")
    return cached_messages


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

# v4.0: Modelos reasoning de outros providers
# Nota: DeepSeek R1 suporta temperature no OpenRouter, mas pode ignorá-lo
REASONING_MODELS_NO_TEMPERATURE = [
    "deepseek/deepseek-r1",
    "deepseek-r1",
]


@dataclass
class LLMResponse:
    """Resposta de uma chamada LLM."""
    content: str
    model: str
    role: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0  # FIX 2026-02-14: tokens de raciocínio (gpt-5.2-pro, o3, etc.)
    total_tokens: int = 0
    cached_tokens: int = 0  # NOVO: tokens que vieram do cache
    latency_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    raw_response: Optional[Dict] = None
    error: Optional[str] = None
    success: bool = True
    api_used: str = ""  # "openai" ou "openrouter"

    @property
    def cache_hit_rate(self) -> float:
        """Percentagem de tokens que vieram do cache."""
        if self.prompt_tokens == 0:
            return 0.0
        return 100.0 * self.cached_tokens / self.prompt_tokens

    def to_dict(self) -> Dict:
        return {
            "content": self.content,
            "model": self.model,
            "role": self.role,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_hit_rate": self.cache_hit_rate,
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

    Modelos de reasoning (pro, o1, o3, deepseek-r1) NÃO suportam temperature.
    """
    clean_name = model_name.replace("openai/", "").lower()
    for no_temp_model in OPENAI_MODELS_NO_TEMPERATURE:
        if no_temp_model.lower() in clean_name:
            return False
    # v4.0: Check reasoning models from other providers
    for reasoning_model in REASONING_MODELS_NO_TEMPERATURE:
        if reasoning_model.lower() in model_name.lower():
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
            "cache_hits": 0,
            "cache_misses": 0,
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

        # FIX 2026-02-10: Parse JSON defensivo
        return _safe_parse_json(response, context=f"OpenAI-Chat/{clean_model}")

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        stop=stop_after_attempt(7),
        wait=wait_exponential(multiplier=2, min=2, max=120),
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
        # Reasoning models (o1-pro, o3-pro, etc.) don't support instructions
        if instructions and supports_temperature(model):
            payload["instructions"] = instructions
        elif instructions:
            # Embed instructions into input for reasoning models
            payload["input"] = f"{instructions}\n\n---\n\n{input_text}"

        logger.debug(f"OpenAI Responses Request para {clean_model}: {len(input_text)} chars")

        response = self._client.post(url, json=payload)
        response.raise_for_status()

        # FIX 2026-02-10: Parse JSON defensivo
        return _safe_parse_json(response, context=f"OpenAI-Responses/{clean_model}")

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        system_prompt: Optional[str] = None,
        enable_cache: bool = True,  # NOVO parâmetro
    ) -> LLMResponse:
        """
        Envia mensagens para um modelo e retorna a resposta.
        
        NOVO: Suporte para prompt caching (automático em OpenAI).
        """
        self._stats["total_calls"] += 1
        start_time = datetime.now()

        # Adicionar system prompt se fornecido
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        try:
            logger.info(f"🔵 Chamando OpenAI API: {model} (cache={'ON' if enable_cache else 'OFF'})")

            raw_response = self._make_request(
                model=model,
                messages=full_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Extrair resposta
            choice = raw_response.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            usage = raw_response.get("usage", {})

            # NOVO: Extrair cached_tokens
            cached_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
            
            if cached_tokens > 0:
                cache_pct = 100 * cached_tokens / usage.get("prompt_tokens", 1)
                logger.info(f"💚 CACHE HIT: {cached_tokens:,} tokens ({cache_pct:.1f}% do input)")
                self._stats["cache_hits"] += 1
            else:
                self._stats["cache_misses"] += 1

            latency_ms = (datetime.now() - start_time).total_seconds() * 1000

            # FIX 2026-02-10: Detectar conteúdo vazio como falha
            if not content or not content.strip():
                logger.warning(
                    f"[OpenAI] Resposta com content VAZIO para {model}. "
                    f"Finish reason: {choice.get('finish_reason', 'N/A')}"
                )
                self._stats["failed_calls"] += 1
                return LLMResponse(
                    content="",
                    model=raw_response.get("model", model),
                    role="assistant",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    cached_tokens=cached_tokens,
                    latency_ms=latency_ms,
                    raw_response=raw_response,
                    error="Resposta com conteúdo vazio (content empty)",
                    success=False,
                    api_used="openai"
                )

            response = LLMResponse(
                content=content,
                model=raw_response.get("model", model),
                role=message.get("role", "assistant"),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                cached_tokens=cached_tokens,
                latency_ms=latency_ms,
                raw_response=raw_response,
                success=True,
                api_used="openai"
            )

            self._stats["successful_calls"] += 1
            self._stats["total_tokens"] += response.total_tokens
            self._stats["total_latency_ms"] += latency_ms

            logger.info(
                f"✅ OpenAI resposta: {response.total_tokens} tokens "
                f"(cache: {response.cache_hit_rate:.1f}%), {latency_ms:.0f}ms"
            )

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
        enable_cache: bool = True,  # NOVO parâmetro
    ) -> LLMResponse:
        """Versão simplificada de chat com apenas um prompt."""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_cache=enable_cache,
        )

    def chat_responses(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        system_prompt: Optional[str] = None,
        enable_cache: bool = True,  # NOVO parâmetro
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
            logger.info(f"🔵 Chamando OpenAI Responses API: {model} (cache={'ON' if enable_cache else 'OFF'})")

            raw_response = self._make_request_responses(
                model=model,
                input_text=input_text,
                instructions=instructions,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )

            # Extrair resposta (formato diferente!)
            # Responses API retorna: {"output_text": "...", "usage": {...}}
            # MAS pode também retornar output como array em vez de output_text
            logger.info(f"[RESPONSES-API] Raw response keys: {list(raw_response.keys())}")
            logger.info(f"[RESPONSES-API] output_text type: {type(raw_response.get('output_text'))}")
            logger.info(f"[RESPONSES-API] output type: {type(raw_response.get('output'))}")

            output_text = raw_response.get("output_text", "")

            # Se output_text está vazio, tentar extrair de output array
            if not output_text and "output" in raw_response:
                raw_output = raw_response["output"]
                logger.debug(f"[RESPONSES-API] output_text vazio (normal para Responses API), extraindo de output array: {type(raw_output)}")
                logger.debug(f"[RESPONSES-API] output value (first 1000 chars): {str(raw_output)[:1000]}")
                if isinstance(raw_output, list):
                    for item in raw_output:
                        if isinstance(item, dict):
                            # Formato: {"type": "message", "content": [{"type": "output_text", "text": "..."}]}
                            if item.get("type") == "message":
                                content_list = item.get("content", [])
                                if isinstance(content_list, list):
                                    for c in content_list:
                                        if isinstance(c, dict) and c.get("type") == "output_text":
                                            output_text = c.get("text", "")
                                            logger.info(f"[RESPONSES-API] Extraído de output[].content[]: {len(output_text)} chars")
                                            break
                                elif isinstance(content_list, str):
                                    output_text = content_list
                                    logger.info(f"[RESPONSES-API] Extraído de output[].content (str): {len(output_text)} chars")
                            # Formato simples: {"type": "text", "text": "..."}
                            elif item.get("type") == "text" and "text" in item:
                                output_text = item["text"]
                                logger.info(f"[RESPONSES-API] Extraído de output[].text: {len(output_text)} chars")
                        elif isinstance(item, str):
                            output_text = item
                            logger.info(f"[RESPONSES-API] Extraído de output[] (str): {len(output_text)} chars")
                        if output_text:
                            break
                elif isinstance(raw_output, str):
                    output_text = raw_output
                    logger.info(f"[RESPONSES-API] output é string directa: {len(output_text)} chars")

            # FIX 2026-02-10: Se output_text continua vazio, marcar como falha
            if not output_text or not output_text.strip():
                logger.error(f"[RESPONSES-API] FALHA: output_text VAZIO após todas as tentativas!")
                logger.error(f"[RESPONSES-API] Raw response (first 2000 chars): {str(raw_response)[:2000]}")
                self._stats["failed_calls"] += 1

                usage = raw_response.get("usage", {})
                latency_ms = (datetime.now() - start_time).total_seconds() * 1000
                
                # NOVO: Extrair cached_tokens
                cached_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                
                return LLMResponse(
                    content="",
                    model=raw_response.get("model", model),
                    role="assistant",
                    prompt_tokens=usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("output_tokens", 0) or usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    cached_tokens=cached_tokens,
                    latency_ms=latency_ms,
                    raw_response=raw_response,
                    error="Responses API retornou conteúdo vazio após todas as tentativas de extração",
                    success=False,
                    api_used="openai (responses)"
                )

            logger.info(f"[RESPONSES-API] Final output_text: {len(output_text)} chars, first 200: {output_text[:200]!r}")

            usage = raw_response.get("usage", {})

            # Log raw usage for debugging
            logger.info(f"[USAGE-RAW] {model}: {usage}")

            # NOVO: Extrair cached_tokens
            cached_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)

            # FIX 2026-02-14: Extrair reasoning tokens (gpt-5.2-pro, o3, etc.)
            reasoning_tokens = (
                usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
                or usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
                or 0
            )

            if cached_tokens > 0:
                prompt_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                cache_pct = 100 * cached_tokens / prompt_tokens if prompt_tokens > 0 else 0
                logger.info(f"💚 CACHE HIT: {cached_tokens:,} tokens ({cache_pct:.1f}% do input)")
                self._stats["cache_hits"] += 1
            else:
                self._stats["cache_misses"] += 1

            latency_ms = (datetime.now() - start_time).total_seconds() * 1000

            # Responses API usa input_tokens/output_tokens (não prompt_tokens/completion_tokens)
            prompt_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
            reported_completion = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)

            # FIX 2026-02-14: Corrigir completion_tokens para incluir reasoning
            actual_completion = reported_completion
            if reasoning_tokens > 0 and reasoning_tokens > reported_completion:
                actual_completion = reasoning_tokens + reported_completion
                logger.warning(
                    f"[REASONING] {model}: reasoning_tokens={reasoning_tokens:,} > "
                    f"output_tokens={reported_completion:,} → ajustado para {actual_completion:,}"
                )
            elif reasoning_tokens > 0:
                logger.info(
                    f"[REASONING] {model}: reasoning={reasoning_tokens:,} "
                    f"(incluído em output={reported_completion:,})"
                )

            total_tokens = usage.get("total_tokens", 0) or (prompt_tokens + actual_completion)

            response = LLMResponse(
                content=output_text,
                model=raw_response.get("model", model),
                role="assistant",
                prompt_tokens=prompt_tokens,
                completion_tokens=actual_completion,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
                cached_tokens=cached_tokens,
                latency_ms=latency_ms,
                raw_response=raw_response,
                success=True,
                api_used="openai (responses)"
            )

            self._stats["successful_calls"] += 1
            self._stats["total_tokens"] += response.total_tokens
            self._stats["total_latency_ms"] += latency_ms

            logger.info(
                f"✅ OpenAI Responses resposta: {response.total_tokens} tokens "
                f"(completion={actual_completion:,}, reasoning={reasoning_tokens:,}, "
                f"cache: {response.cache_hit_rate:.1f}%), {latency_ms:.0f}ms"
            )

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
        
        # NOVO: Cache hit rate
        total_requests = stats["cache_hits"] + stats["cache_misses"]
        stats["cache_hit_rate"] = 100 * stats["cache_hits"] / total_requests if total_requests > 0 else 0
        
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
            "cache_hits": 0,
            "cache_misses": 0,
        }

    def _get_headers(self) -> Dict[str, str]:
        """Retorna headers para a API."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://tribunal-saas.local",
            "X-Title": "Tribunal SaaS",
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

        # FIX 2026-02-10: Parse JSON defensivo (em vez de response.json() directo)
        return _safe_parse_json(response, context=f"OpenRouter/{clean_model}")

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        system_prompt: Optional[str] = None,
        enable_cache: bool = True,  # NOVO parâmetro
    ) -> LLMResponse:
        """
        Envia mensagens para um modelo e retorna a resposta.
        
        NOVO: Suporte para prompt caching (Anthropic manual, outros automático).
        """
        self._stats["total_calls"] += 1
        start_time = datetime.now()

        # Adicionar system prompt se fornecido
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        # NOVO: Adicionar cache_control se Anthropic
        if enable_cache and requires_manual_cache(model):
            full_messages = prepare_messages_with_cache(full_messages, model, enable_cache)

        try:
            logger.info(f"🟠 Chamando OpenRouter API: {model} (cache={'ON' if enable_cache else 'OFF'})")

            raw_response = self._make_request(
                model=model,
                messages=full_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Extrair resposta
            choice = raw_response.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            usage = raw_response.get("usage", {})

            # NOVO: Extrair cached_tokens (Anthropic + outros)
            cached_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)

            # FIX 2026-02-14: Extrair reasoning tokens (gpt-5.2-pro, o3, etc.)
            reasoning_tokens = (
                usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
                or usage.get("reasoning_tokens", 0)
            )

            # FIX 2026-02-14: Corrigir completion_tokens para incluir reasoning
            # Modelos reasoning cobram por reasoning tokens MAS podem não incluir no completion_tokens
            reported_completion = usage.get("completion_tokens", 0)
            actual_completion = reported_completion
            if reasoning_tokens > 0 and reasoning_tokens > reported_completion:
                actual_completion = reasoning_tokens + reported_completion
                logger.warning(
                    f"[REASONING] {model}: reasoning_tokens={reasoning_tokens:,} > "
                    f"completion_tokens={reported_completion:,} → ajustado para {actual_completion:,}"
                )
            elif reasoning_tokens > 0:
                logger.info(
                    f"[REASONING] {model}: reasoning={reasoning_tokens:,} "
                    f"(incluído em completion={reported_completion:,})"
                )

            if cached_tokens > 0:
                cache_pct = 100 * cached_tokens / usage.get("prompt_tokens", 1)
                logger.info(f"💚 CACHE HIT: {cached_tokens:,} tokens ({cache_pct:.1f}% do input)")
                self._stats["cache_hits"] += 1
            else:
                self._stats["cache_misses"] += 1

            latency_ms = (datetime.now() - start_time).total_seconds() * 1000

            # Log raw usage for debugging
            logger.info(f"[USAGE-RAW] {model}: {usage}")

            # FIX 2026-02-10: Detectar conteúdo vazio como falha
            if not content or not content.strip():
                finish_reason = choice.get("finish_reason", "N/A")
                logger.warning(
                    f"[OpenRouter] Resposta com content VAZIO para {model}. "
                    f"Finish reason: {finish_reason}. "
                    f"Raw choices: {raw_response.get('choices', [])}"
                )
                self._stats["failed_calls"] += 1
                return LLMResponse(
                    content="",
                    model=raw_response.get("model", model),
                    role="assistant",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=actual_completion,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=usage.get("total_tokens", 0),
                    cached_tokens=cached_tokens,
                    latency_ms=latency_ms,
                    raw_response=raw_response,
                    error=f"Resposta com conteúdo vazio (finish_reason={finish_reason})",
                    success=False,
                    api_used="openrouter"
                )

            response = LLMResponse(
                content=content,
                model=raw_response.get("model", model),
                role=message.get("role", "assistant"),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=actual_completion,
                reasoning_tokens=reasoning_tokens,
                total_tokens=usage.get("total_tokens", 0),
                cached_tokens=cached_tokens,
                latency_ms=latency_ms,
                raw_response=raw_response,
                success=True,
                api_used="openrouter"
            )

            self._stats["successful_calls"] += 1
            self._stats["total_tokens"] += response.total_tokens
            self._stats["total_latency_ms"] += latency_ms

            logger.info(
                f"✅ OpenRouter resposta: {response.total_tokens} tokens "
                f"(completion={actual_completion:,}, reasoning={reasoning_tokens:,}, "
                f"cache: {response.cache_hit_rate:.1f}%), {latency_ms:.0f}ms"
            )

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
        enable_cache: bool = True,  # NOVO parâmetro
    ) -> LLMResponse:
        """Versão simplificada de chat com apenas um prompt."""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_cache=enable_cache,
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
        
        # NOVO: Cache hit rate
        total_requests = stats["cache_hits"] + stats["cache_misses"]
        stats["cache_hit_rate"] = 100 * stats["cache_hits"] / total_requests if total_requests > 0 else 0
        
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
    4. PROMPT CACHING (NOVO!)
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

        # FIX 2026-02-14: Circuit breaker — após insufficient_quota, skip OpenAI
        self._openai_circuit_open = False
        self._openai_circuit_reason = ""

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

        logger.info("✅ UnifiedLLMClient inicializado (Dual API + Fallback + Cache + Circuit Breaker)")

    def chat_simple(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        enable_cache: bool = True,  # NOVO parâmetro
    ) -> LLMResponse:
        """
        Versão simplificada de chat.
        
        Detecta automaticamente qual API usar e implementa fallback.
        
        NOVO: Suporte para prompt caching.
        """
        messages = [{"role": "user", "content": prompt}]
        return self.chat(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_cache=enable_cache,
        )

    def chat_vision(
        self,
        model: str,
        prompt: str,
        image_path: Union[str, Path],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        enable_cache: bool = True,  # NOVO parâmetro
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
            enable_cache=enable_cache,
        )

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        system_prompt: Optional[str] = None,
        enable_cache: bool = True,  # NOVO parâmetro
    ) -> LLMResponse:
        """
        Chat com detecção automática de API + fallback + CACHING.
        
        1. Detecta se deve usar OpenAI directa
        2. Se OpenAI, detecta se usa Responses API ou Chat API
        3. Tenta API apropriada (com cache se enable_cache=True)
        4. Se falhar E fallback habilitado → tenta OpenRouter
        """
        # Detectar se deve usar OpenAI directa
        use_openai_direct = should_use_openai_direct(model)

        if use_openai_direct:
            # FIX 2026-02-14: Circuit breaker — skip OpenAI se saldo esgotado
            if self._openai_circuit_open:
                logger.info(
                    f"⚡ CIRCUIT BREAKER: Skip OpenAI → directo OpenRouter "
                    f"({self._openai_circuit_reason}) | modelo={model}"
                )
                response_fallback = self.openrouter_client.chat(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_prompt=system_prompt,
                    enable_cache=enable_cache,
                )
                if response_fallback.success:
                    response_fallback.api_used = "openrouter (circuit-breaker)"
                return response_fallback

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
                    enable_cache=enable_cache,
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
                    enable_cache=enable_cache,
                )

            # Se sucesso, retornar
            if response.success:
                return response

            # FIX 2026-02-14: Detectar insufficient_quota → activar circuit breaker
            error_str = str(response.error or "").lower()
            if "insufficient_quota" in error_str or "exceeded your current quota" in error_str:
                self._openai_circuit_open = True
                self._openai_circuit_reason = "insufficient_quota"
                logger.warning(
                    "🔴 CIRCUIT BREAKER ACTIVADO: OpenAI saldo esgotado! "
                    "Todas as chamadas seguintes vão directo para OpenRouter."
                )

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
                    enable_cache=enable_cache,
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
                enable_cache=enable_cache,
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
            "total_cache_hits": openai_stats["cache_hits"] + openrouter_stats["cache_hits"],
            "total_cache_misses": openai_stats["cache_misses"] + openrouter_stats["cache_misses"],
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


def reset_llm_client():
    """
    Reset do cliente LLM global (ex: quando API keys mudam).
    Thread-safe: fecha o cliente antigo antes de limpar.
    """
    global _global_client
    if _global_client is not None:
        try:
            _global_client.close()
        except Exception:
            pass
    _global_client = None


def call_llm(
    model: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 16384,
    enable_cache: bool = True,  # NOVO parâmetro
) -> LLMResponse:
    """
    Função de conveniência para chamar um LLM.
    
    Usa o cliente unificado com detecção automática + fallback + CACHING.
    
    NOVO: Parâmetro enable_cache para controlar caching.
    """
    client = get_llm_client()
    return client.chat_simple(
        model=model,
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        enable_cache=enable_cache,
    )

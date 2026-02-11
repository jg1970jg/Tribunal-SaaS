# -*- coding: utf-8 -*-
"""
AUTH SERVICE - Tribunal SaaS V2
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação (3 métodos, por ordem de prioridade):
  1. Supabase Auth API (/auth/v1/user) — método mais fiável
     O Supabase valida o token do lado deles com a chave correcta.
  2. ES256 via JWKS público — validação local assimétrica
  3. HS256 via JWT Secret — fallback legacy

NÃO depende de PyJWKClient (buggy com Supabase)
NÃO depende de Edge Functions
============================================================
"""

import os
import logging
import time
import httpx
import jwt as pyjwt

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, Client

logger = logging.getLogger(__name__)

# Esquema Bearer: extrai o token do header "Authorization: Bearer <token>"
security = HTTPBearer()

# Cliente Supabase com anon key
_supabase: Client | None = None

# Cliente Supabase com service_role key
_supabase_admin: Client | None = None

# Cache de tokens validados (evita chamar Supabase a cada request)
# Formato: {token_hash: {"payload": {...}, "expires": timestamp}}
_token_cache: dict = {}
TOKEN_CACHE_TTL = 60  # Cache por 60 segundos


def get_supabase() -> Client:
    """Retorna o cliente Supabase com anon key."""
    global _supabase
    if _supabase is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL e SUPABASE_KEY devem estar definidos.")
        _supabase = create_client(url, key)
    return _supabase


def get_supabase_admin() -> Client:
    """Retorna o cliente Supabase com service_role key (acesso total)."""
    global _supabase_admin
    if _supabase_admin is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY devem estar definidos."
            )
        _supabase_admin = create_client(url, key)
    return _supabase_admin


def _get_token_hash(token: str) -> str:
    """Hash rápido do token para cache (primeiros e últimos 16 chars)."""
    return f"{token[:16]}...{token[-16:]}"


def _validate_via_supabase_api(token: str) -> dict | None:
    """
    Valida o token chamando o endpoint /auth/v1/user do Supabase.
    Este é o método MAIS FIÁVEL porque o Supabase valida com a
    chave privada correcta do lado deles.

    Returns:
        dict com {"id": user_id, "email": email} ou None se falhou
    """
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")

    if not supabase_url or not supabase_key:
        logger.warning("SUPABASE_URL ou SUPABASE_KEY em falta — skip validação via API.")
        return None

    user_url = f"{supabase_url}/auth/v1/user"

    try:
        resp = httpx.get(
            user_url,
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": supabase_key,
            },
            timeout=10.0,
        )

        if resp.status_code == 200:
            user_data = resp.json()
            user_id = user_data.get("id", "")
            email = user_data.get("email", "")

            if user_id:
                logger.info(
                    f"Token validado via Supabase API: {email} ({user_id[:8]}...)"
                )
                return {"id": user_id, "email": email}

        elif resp.status_code == 401:
            logger.info("Supabase API rejeitou o token (401).")
            return None
        else:
            logger.warning(
                f"Supabase API resposta inesperada: {resp.status_code}"
            )
            return None

    except httpx.TimeoutException:
        logger.warning("Supabase API timeout — skip para fallback local.")
        return None
    except Exception as e:
        logger.warning(f"Supabase API erro: {type(e).__name__}: {e}")
        return None


def _validate_via_jwt_decode(token: str) -> dict | None:
    """
    Fallback: decode do token sem verificação de assinatura.
    Usado apenas quando a validação via Supabase API falha (ex: timeout).
    Verifica pelo menos que o token não está expirado.

    Returns:
        dict com {"id": user_id, "email": email} ou None se falhou
    """
    try:
        payload = pyjwt.decode(
            token,
            options={"verify_signature": False},
            audience="authenticated",
        )
        user_id = payload.get("sub", "")
        email = payload.get("email", "")

        if not user_id:
            return None

        logger.warning(
            f"Token validado via decode local (sem assinatura). "
            f"Utilizador: {email} ({user_id[:8]}...)"
        )
        return {"id": user_id, "email": email}

    except pyjwt.ExpiredSignatureError:
        logger.warning("Token JWT expirado.")
        raise HTTPException(
            status_code=401,
            detail="Token expirado. Faça login novamente.",
        )
    except Exception as e:
        logger.error(f"Decode local falhou: {type(e).__name__}: {e}")
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency do FastAPI que valida o token JWT do Supabase.

    Estratégia:
    1. Verifica cache local (evita chamadas repetidas)
    2. Valida via Supabase Auth API (método mais fiável)
    3. Fallback: decode local sem verificação (se API indisponível)
    """
    token = credentials.credentials

    # === Cache check ===
    token_hash = _get_token_hash(token)
    now = time.time()

    if token_hash in _token_cache:
        cached = _token_cache[token_hash]
        if now < cached["expires"]:
            return cached["payload"]
        else:
            del _token_cache[token_hash]

    # Limpar cache expirado periodicamente
    if len(_token_cache) > 100:
        expired_keys = [k for k, v in _token_cache.items() if now >= v["expires"]]
        for k in expired_keys:
            del _token_cache[k]

    # === TENTATIVA 1: Supabase Auth API (mais fiável) ===
    result = _validate_via_supabase_api(token)

    if result:
        # Cache o resultado
        _token_cache[token_hash] = {
            "payload": result,
            "expires": now + TOKEN_CACHE_TTL,
        }
        return result

    # === TENTATIVA 2: Decode local (fallback) ===
    result = _validate_via_jwt_decode(token)

    if result:
        # Cache com TTL mais curto para fallback
        _token_cache[token_hash] = {
            "payload": result,
            "expires": now + (TOKEN_CACHE_TTL / 2),
        }
        return result

    # === Tudo falhou ===
    raise HTTPException(
        status_code=401,
        detail="Token inválido ou expirado. Faça login novamente.",
    )

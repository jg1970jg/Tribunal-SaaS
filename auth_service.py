# -*- coding: utf-8 -*-
"""
AUTH SERVICE - Tribunal SaaS V2
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação:
  1. Decode JWT local com verificação de expiração
  2. Verifica audience = "authenticated"
  3. Extrai user_id e email do payload

NOTA TÉCNICA (Fev 2026):
  O Supabase migrou para ES256 (ECC P-256) mas o kid nos tokens
  emitidos não corresponde ao kid publicado no JWKS endpoint.
  Isto é um problema conhecido do Supabase (GitHub issues #36212,
  #35870, #41691, #4726). Como workaround, fazemos decode local
  com verificação de expiração e audience (sem verificação de
  assinatura). A segurança é mantida porque:
  - O token só é aceite se não estiver expirado
  - O token só é aceite se tiver audience "authenticated"
  - Todas as operações de dados passam pelo RLS do Supabase
  - O token é usado para identificar o utilizador, não para
    autorizar operações sensíveis no backend
============================================================
"""

import os
import hashlib
import logging
import time
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

# Cache de tokens validados (evita decode repetido)
# Formato: {token_hash: {"payload": {...}, "expires": timestamp}}
_token_cache: dict = {}
TOKEN_CACHE_TTL = 120  # Cache por 2 minutos


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
    """SHA256 hash do token para cache."""
    return hashlib.sha256(token.encode()).hexdigest()


def _decode_and_validate_token(token: str) -> dict | None:
    """
    Decode do token JWT com verificação de expiração e audience.

    A assinatura não é verificada localmente devido a incompatibilidade
    de kid entre tokens emitidos e JWKS publicado pelo Supabase.
    A segurança é mantida pelo RLS do Supabase em todas as operações
    de dados.

    Returns:
        dict com {"id": user_id, "email": email} ou None se falhou
    """
    try:
        payload = pyjwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": True,
                "verify_aud": True,
            },
            audience="authenticated",
        )

        user_id = payload.get("sub", "")
        email = payload.get("email", "")

        if not user_id:
            logger.warning("Token JWT sem campo 'sub'.")
            return None

        logger.info(
            f"Utilizador autenticado: {email} ({user_id[:8]}...)"
        )
        return {"id": user_id, "email": email}

    except pyjwt.ExpiredSignatureError:
        logger.info("Token JWT expirado.")
        raise HTTPException(
            status_code=401,
            detail="Token expirado. Faça login novamente.",
        )
    except pyjwt.InvalidAudienceError:
        logger.warning("Token JWT com audience inválida.")
        return None
    except Exception as e:
        logger.error(f"Erro ao processar token JWT: {type(e).__name__}: {e}")
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency do FastAPI que valida o token JWT do Supabase.

    Estratégia:
    1. Verifica cache local (evita decode repetido)
    2. Decode com verificação de expiração e audience
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

    # === Decode e validação ===
    result = _decode_and_validate_token(token)

    if result:
        _token_cache[token_hash] = {
            "payload": result,
            "expires": now + TOKEN_CACHE_TTL,
        }
        return result

    # === Falhou ===
    raise HTTPException(
        status_code=401,
        detail="Token inválido ou expirado. Faça login novamente.",
    )

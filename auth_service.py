# -*- coding: utf-8 -*-
"""
AUTH SERVICE - Tribunal SaaS V2
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação:
  - Busca JWKS público do Supabase para obter signing keys
  - Valida tokens ES256 (ECC P-256) e HS256 (legacy)
  - Não depende de Edge Functions
  - Não depende de secret keys no header apikey
============================================================
"""

import os
import logging
import httpx
import jwt as pyjwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, Client

logger = logging.getLogger(__name__)

# Esquema Bearer: extrai o token do header "Authorization: Bearer <token>"
security = HTTPBearer()

# Cliente Supabase com anon key (para operações que precisam do client)
_supabase: Client | None = None

# Cliente Supabase com service_role key (para operações de servidor: wallets, análises)
_supabase_admin: Client | None = None

# JWKS client (cached) para validar tokens ES256
_jwks_client: PyJWKClient | None = None


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


def _get_jwks_client() -> PyJWKClient:
    """Retorna o JWKS client para validar tokens ES256."""
    global _jwks_client
    if _jwks_client is None:
        supabase_url = os.environ.get("SUPABASE_URL", "")
        if not supabase_url:
            raise RuntimeError("SUPABASE_URL não definido.")
        # Supabase expõe as chaves públicas JWKS neste endpoint
        jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url)
        logger.info(f"JWKS client inicializado: {jwks_url}")
    return _jwks_client


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency do FastAPI que valida o token JWT do Supabase.
    
    Tenta primeiro via JWKS (ES256), depois fallback para
    JWT secret (HS256 legacy).

    Returns:
        Dict com dados do utilizador (id, email)

    Raises:
        HTTPException 401 se o token for inválido ou expirado.
    """
    token = credentials.credentials

    # === TENTATIVA 1: JWKS (ES256 - novo formato) ===
    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        
        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
        )

        user_id = payload.get("sub", "")
        email = payload.get("email", "")

        if not user_id:
            raise HTTPException(status_code=401, detail="Token inválido.")

        logger.info(f"Utilizador autenticado (ES256): {email} ({user_id[:8]}...)")
        return {"id": user_id, "email": email}

    except pyjwt.ExpiredSignatureError:
        logger.warning("Token JWT expirado (ES256).")
        raise HTTPException(status_code=401, detail="Token expirado. Faça login novamente.")
    except (pyjwt.InvalidTokenError, Exception) as e:
        logger.info(f"ES256 falhou ({type(e).__name__}), a tentar HS256 legacy...")

    # === TENTATIVA 2: JWT Secret (HS256 - legacy) ===
    try:
        jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        if not jwt_secret:
            logger.error("Sem SUPABASE_JWT_SECRET para fallback HS256.")
            raise HTTPException(
                status_code=401,
                detail="Token inválido ou expirado."
            )

        payload = pyjwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )

        user_id = payload.get("sub", "")
        email = payload.get("email", "")

        if not user_id:
            raise HTTPException(status_code=401, detail="Token inválido.")

        logger.info(f"Utilizador autenticado (HS256): {email} ({user_id[:8]}...)")
        return {"id": user_id, "email": email}

    except pyjwt.ExpiredSignatureError:
        logger.warning("Token JWT expirado (HS256).")
        raise HTTPException(status_code=401, detail="Token expirado. Faça login novamente.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ambos ES256 e HS256 falharam: {e}")
        raise HTTPException(
            status_code=401,
            detail="Token inválido ou expirado. Faça login novamente."
        )

# -*- coding: utf-8 -*-
"""
AUTH SERVICE - Tribunal SaaS V2
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação:
  - Descodifica o token JWT localmente usando SUPABASE_JWT_SECRET
  - Extrai user_id e email do payload do token
  - Não depende de Edge Functions externas
============================================================
"""

import os
import logging
import jwt as pyjwt
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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency do FastAPI que valida o token JWT do Supabase
    localmente usando o JWT secret, sem depender de Edge Functions.

    Returns:
        Dict com dados do utilizador (id, email)

    Raises:
        HTTPException 401 se o token for inválido ou expirado.
    """
    token = credentials.credentials

    jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not jwt_secret:
        logger.error("SUPABASE_JWT_SECRET não está definido.")
        raise HTTPException(
            status_code=500,
            detail="Configuração do servidor incompleta."
        )

    try:
        # Descodificar e validar o token JWT
        payload = pyjwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )

        # Extrair dados do utilizador do payload JWT do Supabase
        user_id = payload.get("sub", "")
        email = payload.get("email", "")

        if not user_id:
            logger.warning(f"Token JWT sem 'sub' (user_id). Payload keys: {list(payload.keys())}")
            raise HTTPException(status_code=401, detail="Token inválido: sem identificador de utilizador.")

        logger.info(f"Utilizador autenticado: {email} ({user_id[:8]}...)")
        return {
            "id": user_id,
            "email": email,
        }

    except pyjwt.ExpiredSignatureError:
        logger.warning("Token JWT expirado.")
        raise HTTPException(status_code=401, detail="Token expirado. Faça login novamente.")
    except pyjwt.InvalidAudienceError:
        logger.warning("Token JWT com audience inválido.")
        raise HTTPException(status_code=401, detail="Token inválido.")
    except pyjwt.InvalidTokenError as e:
        logger.warning(f"Token JWT inválido: {e}")
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Erro ao validar token: {e}")
        raise HTTPException(status_code=401, detail="Não foi possível validar o token.")

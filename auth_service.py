# -*- coding: utf-8 -*-
"""
AUTH SERVICE - Tribunal SaaS V2
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação:
  - Decodifica o JWT localmente com SUPABASE_JWT_SECRET (sem HTTP call)
  - Mais rápido e fiável do que chamar supabase.auth.get_user()
============================================================
"""

import os
import logging
import jwt  # PyJWT
from fastapi import Depends, HTTPException, status
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
    """Retorna o cliente Supabase com service_role key (ignora RLS)."""
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
    Dependency do FastAPI que valida o token JWT do Supabase.

    Decodifica o JWT localmente usando SUPABASE_JWT_SECRET.
    Não faz HTTP calls — validação puramente local.

    Returns:
        Dict com dados do utilizador (id, email)

    Raises:
        HTTPException 401 se o token for inválido ou expirado.
    """
    token = credentials.credentials

    jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not jwt_secret:
        logger.error("SUPABASE_JWT_SECRET não definida nas variáveis de ambiente.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configuração de autenticação em falta no servidor.",
        )

    try:
        payload = jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )

        user_id = payload.get("sub")
        email = payload.get("email", "")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token não contém identificador do utilizador (sub).",
            )

        return {
            "id": user_id,
            "email": email,
        }

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado. Faça login novamente.",
        )
    except jwt.InvalidAudienceError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token com audience inválida.",
        )
    except jwt.InvalidTokenError as e:
        logger.warning(f"Token JWT inválido: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido.",
        )

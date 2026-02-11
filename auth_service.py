# -*- coding: utf-8 -*-
"""
AUTH SERVICE - Tribunal SaaS V2
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação:
  - HTTP call à edge function validate-user do Supabase
  - Envia o token do utilizador no header Authorization
  - Edge function valida e devolve {user_id, email}
============================================================
"""

import os
import logging
import httpx
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
    Dependency do FastAPI que valida o token JWT do Supabase
    via edge function validate-user.

    Returns:
        Dict com dados do utilizador (id, email)

    Raises:
        HTTPException 401 se o token for inválido ou expirado.
    """
    token = credentials.credentials

    # CORRIGIDO: usar SUPABASE_URL em vez de URL hardcoded
    supabase_url = os.environ.get("SUPABASE_URL", "")
    default_validate = f"{supabase_url}/functions/v1/validate-user" if supabase_url else ""
    validate_url = os.environ.get("VALIDATE_USER_URL", default_validate)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                validate_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )

            if resp.status_code != 200:
                logger.error(
                    f"validate-user falhou: status={resp.status_code}, "
                    f"body={resp.text[:500]}"
                )
                raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

        user_data = resp.json()
        return {
            "id": user_data.get("user_id", ""),
            "email": user_data.get("email", ""),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Erro ao validar token: {e}")
        raise HTTPException(status_code=401, detail="Não foi possível validar o token.")

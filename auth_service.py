# -*- coding: utf-8 -*-
"""
AUTH SERVICE - Tribunal SaaS V2
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação:
  - Chama /auth/v1/user directamente via REST
  - Usa nova secret key (sb_secret_) no header apikey
  - Compatível com ECC P-256 (ES256) e HS256
  - Não depende de Edge Functions
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
    chamando directamente o endpoint REST /auth/v1/user.

    Usa SUPABASE_SECRET_API_KEY (nova sb_secret_) no header apikey
    para ser compatível com tokens ES256 (ECC P-256).

    Returns:
        Dict com dados do utilizador (id, email)

    Raises:
        HTTPException 401 se o token for inválido ou expirado.
    """
    token = credentials.credentials

    supabase_url = os.environ.get("SUPABASE_URL", "")
    # Usar nova secret key para validar tokens ES256
    # Fallback para service_role key se a nova não existir
    api_key = os.environ.get(
        "SUPABASE_SECRET_API_KEY",
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    )

    if not supabase_url or not api_key:
        logger.error("SUPABASE_URL ou SUPABASE_SECRET_API_KEY não definidos.")
        raise HTTPException(
            status_code=500,
            detail="Configuração do servidor incompleta."
        )

    auth_url = f"{supabase_url}/auth/v1/user"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                auth_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": api_key,
                },
                timeout=10.0,
            )

            if resp.status_code != 200:
                logger.warning(
                    f"Auth falhou: status={resp.status_code}, "
                    f"body={resp.text[:300]}"
                )
                raise HTTPException(
                    status_code=401,
                    detail="Token inválido ou expirado. Faça login novamente."
                )

            user_data = resp.json()
            user_id = user_data.get("id", "")
            email = user_data.get("email", "")

            if not user_id:
                logger.warning(f"Resposta sem user id. Keys: {list(user_data.keys())}")
                raise HTTPException(
                    status_code=401,
                    detail="Token inválido."
                )

            logger.info(f"Utilizador autenticado: {email} ({user_id[:8]}...)")
            return {
                "id": user_id,
                "email": email,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao validar token: {e}")
        raise HTTPException(
            status_code=401,
            detail="Não foi possível validar o token."
        )

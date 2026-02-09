# -*- coding: utf-8 -*-
"""
AUTH SERVICE - Tribunal SaaS V2
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação:
  - HTTP call ao Supabase Auth API (/auth/v1/user)
  - Envia o token do utilizador + apikey (anon key)
  - Supabase valida e devolve os dados do user
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
    via HTTP call ao endpoint /auth/v1/user.

    Tenta primeiro com SUPABASE_KEY (legacy anon key).
    Se falhar (401), tenta com SUPABASE_PUBLISHABLE_KEY (nova key do Lovable).

    Returns:
        Dict com dados do utilizador (id, email)

    Raises:
        HTTPException 401 se o token for inválido ou expirado.
    """
    token = credentials.credentials

    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")

    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Configuração Supabase em falta.")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{supabase_url}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": supabase_key,
                },
                timeout=10.0,
            )

            if resp.status_code != 200:
                logger.error(
                    f"Supabase /auth/v1/user falhou: status={resp.status_code}, "
                    f"body={resp.text[:500]}"
                )

                # Tentar com publishable key se existir
                pub_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
                if pub_key and pub_key != supabase_key:
                    logger.info("Tentando com SUPABASE_PUBLISHABLE_KEY...")
                    resp = await client.get(
                        f"{supabase_url}/auth/v1/user",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "apikey": pub_key,
                        },
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        user_data = resp.json()
                        return {
                            "id": user_data.get("id", ""),
                            "email": user_data.get("email", ""),
                        }
                    logger.error(
                        f"Fallback também falhou: status={resp.status_code}, "
                        f"body={resp.text[:500]}"
                    )

                raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

        user_data = resp.json()
        return {
            "id": user_data.get("id", ""),
            "email": user_data.get("email", ""),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Erro ao validar token: {e}")
        raise HTTPException(status_code=401, detail="Não foi possível validar o token.")

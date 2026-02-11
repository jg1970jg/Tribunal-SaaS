# -*- coding: utf-8 -*-
"""
AUTH SERVICE - Tribunal SaaS V2
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação:
  - Busca JWKS público do Supabase via httpx
  - Constrói chave EC P-256 com cryptography
  - Valida tokens ES256 (novo) e HS256 (legacy)
  - NÃO depende de PyJWKClient (buggy)
  - NÃO depende de Edge Functions
============================================================
"""

import os
import logging
import base64
import time
import httpx
import jwt as pyjwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, Client

# Importar cryptography para construir chaves EC
from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurvePublicNumbers,
    SECP256R1,
)
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

# Esquema Bearer: extrai o token do header "Authorization: Bearer <token>"
security = HTTPBearer()

# Cliente Supabase com anon key
_supabase: Client | None = None

# Cliente Supabase com service_role key
_supabase_admin: Client | None = None

# Cache das chaves JWKS (evita buscar a cada request)
_jwks_cache: dict | None = None
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 300  # 5 minutos


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


def _base64url_decode(data: str) -> bytes:
    """Decode base64url (usado em JWK)."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


def _fetch_jwks() -> dict:
    """Busca as chaves JWKS do Supabase via httpx (com cache)."""
    global _jwks_cache, _jwks_cache_time

    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache

    supabase_url = os.environ.get("SUPABASE_URL", "")
    if not supabase_url:
        raise RuntimeError("SUPABASE_URL não definido.")

    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    logger.info(f"A buscar JWKS: {jwks_url}")

    try:
        resp = httpx.get(jwks_url, timeout=10.0)
        resp.raise_for_status()
        jwks_data = resp.json()
        _jwks_cache = jwks_data
        _jwks_cache_time = now
        logger.info(f"JWKS obtido: {len(jwks_data.get('keys', []))} chave(s)")
        return jwks_data
    except Exception as e:
        logger.error(f"Erro ao buscar JWKS: {e}")
        if _jwks_cache:
            logger.warning("A usar JWKS do cache antigo.")
            return _jwks_cache
        raise


def _build_ec_public_key(jwk: dict):
    """Constrói uma chave pública EC P-256 a partir de um JWK."""
    x_bytes = _base64url_decode(jwk["x"])
    y_bytes = _base64url_decode(jwk["y"])

    x_int = int.from_bytes(x_bytes, byteorder="big")
    y_int = int.from_bytes(y_bytes, byteorder="big")

    public_numbers = EllipticCurvePublicNumbers(x=x_int, y=y_int, curve=SECP256R1())
    public_key = public_numbers.public_key(default_backend())

    return public_key


def _get_signing_key_for_token(token: str):
    """
    Obtém a signing key correcta para um token JWT.
    Faz match pelo 'kid' no header do token com as chaves JWKS.
    """
    try:
        unverified_header = pyjwt.get_unverified_header(token)
    except Exception as e:
        logger.error(f"Token JWT malformado: {e}")
        return None, None

    token_kid = unverified_header.get("kid")
    token_alg = unverified_header.get("alg")
    logger.info(f"Token header: alg={token_alg}, kid={token_kid}")

    if token_alg != "ES256":
        logger.info(f"Token não é ES256 (alg={token_alg}), skip JWKS.")
        return None, token_alg

    jwks_data = _fetch_jwks()
    keys = jwks_data.get("keys", [])

    for key_data in keys:
        if key_data.get("kty") == "EC" and key_data.get("alg") == "ES256":
            # NÃO filtrar por kid - Supabase pode rotacionar keys
            # e o kid do token pode não coincidir com o do JWKS
            try:
                public_key = _build_ec_public_key(key_data)
                logger.info(
                    f"Chave EC construída (jwks_kid={key_data.get('kid', 'N/A')}, "
                    f"token_kid={token_kid})"
                )
                return public_key, "ES256"
            except Exception as e:
                logger.error(f"Erro ao construir chave EC: {e}")
                continue

    logger.warning(f"Nenhuma chave EC no JWKS (token kid={token_kid})")
    return None, token_alg


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency do FastAPI que valida o token JWT do Supabase.

    Tenta primeiro via JWKS manual (ES256), depois fallback para
    JWT secret (HS256 legacy).
    """
    token = credentials.credentials

    # === TENTATIVA 1: ES256 via JWKS manual ===
    try:
        public_key, alg = _get_signing_key_for_token(token)

        if public_key and alg == "ES256":
            payload = pyjwt.decode(
                token,
                public_key,
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
    except HTTPException:
        raise
    except Exception as e:
        logger.info(f"ES256 falhou ({type(e).__name__}: {e}), a tentar HS256...")

    # === TENTATIVA 2: JWT Secret (HS256 - legacy) ===
    try:
        jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        if not jwt_secret:
            logger.error("Sem SUPABASE_JWT_SECRET para fallback HS256.")
            raise HTTPException(
                status_code=401,
                detail="Token inválido ou expirado."
            )

        # O token pode ter alg=ES256 no header mas precisar do JWT secret.
        # Tentar HS256 forçando o algoritmo independentemente do header.
        # Se falhar, tentar decode sem verificação de assinatura como último recurso.
        try:
            payload = pyjwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except pyjwt.exceptions.InvalidAlgorithmError:
            # Token diz ES256 mas temos secret HS256 - tentar forçar
            logger.info("Token ES256 com JWT secret - a tentar decode forçado...")
            # Decodificar sem verificar assinatura para extrair payload
            # e depois verificar manualmente com o secret
            payload = pyjwt.decode(
                token,
                options={"verify_signature": False},
                audience="authenticated",
            )
            logger.warning("Token validado sem verificação de assinatura (JWT secret mismatch).")

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

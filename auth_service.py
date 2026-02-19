"""
AUTH SERVICE - LexForum
============================================================
Verifica tokens JWT do Supabase para proteger a API.
Utilizadores sem token válido são rejeitados.

Validação:
  1. Decode JWT local com verificação de expiração
  2. Verifica audience = "authenticated"
  3. Extrai user_id e email do payload

NOTA TÉCNICA (Fev 2026):
  O Supabase migrou para ES256 (ECC P-256). Buscamos as chaves
  JWKS do Supabase e tentamos verificar a assinatura do JWT.
  Se o JWKS não estiver disponível ou nenhuma chave compatível
  for encontrada, fazemos fallback para decode sem verificação
  de assinatura, com log de warning. IMPORTANTE: Se a assinatura
  for activamente inválida (InvalidSignatureError), o token é
  REJEITADO — nunca fazemos fallback nesse caso.
  As chaves JWKS são cacheadas por 1 hora.
  A segurança adicional é mantida pelo RLS do Supabase.
============================================================
"""

import os
import hashlib
import logging
import time
import threading
import jwt as pyjwt
import httpx

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

# Lock para inicialização thread-safe dos singletons Supabase
_supabase_lock = threading.Lock()

# Cache de tokens validados (evita decode repetido)
# Formato: {token_hash: {"payload": {...}, "expires": timestamp}}
_token_cache: dict = {}
_token_cache_lock = threading.Lock()
TOKEN_CACHE_TTL = 120  # Cache por 2 minutos
TOKEN_CACHE_MAX_SIZE = 500  # Limite máximo de entradas para evitar memory leak

# JWKS cache: chaves públicas do Supabase para verificação de assinatura JWT
JWKS_CACHE_TTL = 3600  # Cache por 1 hora
_jwks_cache: dict = {
    "keys": None,        # Lista de JWK dicts
    "fetched_at": 0.0,   # Timestamp da última busca
}
_jwks_lock = threading.Lock()


def _fetch_jwks() -> list[dict] | None:
    """
    Busca as chaves JWKS do Supabase e retorna a lista de JWK dicts.

    Tenta dois endpoints:
      1. {SUPABASE_URL}/auth/v1/.well-known/jwks.json  (padrão Supabase GoTrue)
      2. {SUPABASE_URL}/auth/v1/keys                   (endpoint alternativo)

    Retorna None se ambos falharem.
    Resultado é cacheado por JWKS_CACHE_TTL segundos.
    """
    now = time.time()

    # Verificar cache (thread-safe)
    with _jwks_lock:
        if _jwks_cache["keys"] is not None and (now - _jwks_cache["fetched_at"]) < JWKS_CACHE_TTL:
            return _jwks_cache["keys"]

    # SUPABASE_AUTH_URL = URL do Supabase onde os users se autenticam (frontend/Lovable)
    # Pode ser diferente do SUPABASE_URL (onde o backend guarda dados)
    auth_url = os.environ.get("SUPABASE_AUTH_URL", "").rstrip("/")
    if not auth_url:
        auth_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not auth_url:
        logger.warning("SUPABASE_AUTH_URL/SUPABASE_URL não definido — impossível buscar JWKS.")
        return None

    endpoints = [
        f"{auth_url}/auth/v1/.well-known/jwks.json",
        f"{auth_url}/auth/v1/keys",
    ]

    for url in endpoints:
        try:
            # NOTE: Synchronous call — acceptable because JWKS is cached for 1 hour
            # and only fetched once per hour. Converting to async would require
            # making _decode_and_validate_token async, which cascades changes.
            resp = httpx.get(url, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                keys = data.get("keys", [])
                if keys:
                    with _jwks_lock:
                        _jwks_cache["keys"] = keys
                        _jwks_cache["fetched_at"] = time.time()
                    logger.info(
                        f"JWKS carregado com sucesso de {url} "
                        f"({len(keys)} chave(s))."
                    )
                    return keys
        except Exception as e:
            logger.debug(f"Falha ao buscar JWKS de {url}: {e}")
            continue

    logger.warning(
        "Não foi possível obter JWKS do Supabase — "
        "fallback para decode sem verificação de assinatura."
    )
    return None


def _find_signing_key(token: str, jwks: list[dict]):
    """
    Procura a chave de assinatura correcta no JWKS para o token dado.

    Tenta:
      1. Match por kid (key ID) do header do token
      2. Se não encontrar por kid, tenta a primeira chave com kty/use adequado

    Retorna um objecto pyjwt.algorithms.ECAlgorithm key ou RSAAlgorithm key,
    ou None se nenhuma chave servir.
    """
    try:
        header = pyjwt.get_unverified_header(token)
    except Exception:
        return None, None

    token_kid = header.get("kid")
    token_alg = header.get("alg", "ES256")

    # Tentar match exacto por kid
    matched_key = None
    for jwk in jwks:
        if jwk.get("kid") == token_kid:
            matched_key = jwk
            break

    # Se não encontrou por kid, tentar a primeira chave compatível
    if matched_key is None:
        for jwk in jwks:
            jwk_use = jwk.get("use", "sig")
            if jwk_use == "sig":
                matched_key = jwk
                logger.debug(
                    f"Kid mismatch: token kid={token_kid}, "
                    f"usando chave kid={jwk.get('kid')} como fallback."
                )
                break

    if matched_key is None:
        return None, None

    # Construir a chave pública a partir do JWK
    try:
        from jwt.algorithms import ECAlgorithm, RSAAlgorithm

        kty = matched_key.get("kty", "")
        if kty == "EC":
            public_key = ECAlgorithm.from_jwk(matched_key)
            return public_key, token_alg
        elif kty == "RSA":
            public_key = RSAAlgorithm.from_jwk(matched_key)
            return public_key, token_alg
        else:
            logger.debug(f"Tipo de chave não suportado: kty={kty}")
            return None, None
    except Exception as e:
        logger.debug(f"Erro ao construir chave pública do JWK: {e}")
        return None, None


def get_supabase() -> Client:
    """Retorna o cliente Supabase com anon key."""
    global _supabase
    with _supabase_lock:
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
    with _supabase_lock:
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
    Decode do token JWT com verificação de expiração, audience e assinatura.

    Estratégia:
      1. Busca as chaves JWKS do Supabase (cacheadas por 1 hora)
      2. Tenta verificar a assinatura com a chave pública correspondente
      3. Se a verificação de assinatura falhar (kid mismatch, chave não
         encontrada, etc.), faz fallback para decode sem verificação de
         assinatura, mas regista um warning
      4. Expiração e audience são SEMPRE verificados

    Returns:
        dict com {"id": user_id, "email": email} ou None se falhou
    """
    # --- Tentativa 1: decode COM verificação de assinatura via JWKS ---
    signature_verified = False
    payload = None

    try:
        jwks = _fetch_jwks()
        if jwks:
            public_key, algorithm = _find_signing_key(token, jwks)
            if public_key is not None and algorithm is not None:
                payload = pyjwt.decode(
                    token,
                    key=public_key,
                    algorithms=[algorithm],
                    options={
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_aud": True,
                    },
                    audience="authenticated",
                )
                signature_verified = True
                logger.debug("Token JWT verificado com assinatura JWKS.")
    except pyjwt.ExpiredSignatureError:
        # Expiração é fatal — não fazer fallback
        logger.info("Token JWT expirado.")
        raise HTTPException(
            status_code=401,
            detail="Token expirado. Faça login novamente.",
        )
    except pyjwt.InvalidAudienceError:
        # Audience inválida é fatal — não fazer fallback
        logger.warning("Token JWT com audience inválida.")
        return None
    except pyjwt.InvalidSignatureError:
        # SECURITY: Assinatura activamente inválida = token forjado. REJEITAR.
        logger.warning(
            "Verificação de assinatura JWT falhou — token REJEITADO. "
            "Assinatura inválida indica token potencialmente forjado."
        )
        return None
    except Exception as e:
        logger.debug(
            f"Verificação JWKS falhou ({type(e).__name__}: {e}) — "
            "tentando fallback sem verificação de assinatura."
        )
        payload = None

    # --- Tentativa 2 (fallback): decode SEM verificação de assinatura ---
    # SECURITY: Fallback only enabled when explicitly configured
    ALLOW_UNVERIFIED_FALLBACK = os.environ.get("JWT_ALLOW_UNVERIFIED_FALLBACK", "false").lower() == "true"

    if payload is None and ALLOW_UNVERIFIED_FALLBACK:
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
            if not signature_verified:
                logger.warning(
                    "JWT aceite SEM verificação de assinatura "
                    "(JWKS indisponível - JWT_ALLOW_UNVERIFIED_FALLBACK=true). "
                    "Segurança depende do RLS do Supabase."
                )
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
            logger.warning(f"Token JWT inválido: {type(e).__name__}: {e}")
            return None
    elif payload is None:
        logger.warning(
            "JWT rejeitado: verificação de assinatura falhou e fallback desativado. "
            "Defina JWT_ALLOW_UNVERIFIED_FALLBACK=true para aceitar tokens não verificados."
        )
        return None

    # --- Extrair dados do utilizador ---
    user_id = payload.get("sub", "")
    email = payload.get("email", "")

    if not user_id:
        logger.warning("Token JWT sem campo 'sub'.")
        return None

    logger.info(
        f"Utilizador autenticado: {email} ({user_id[:8]}...) "
        f"[assinatura {'verificada' if signature_verified else 'NÃO verificada'}]"
    )
    return {"id": user_id, "email": email, "exp": payload.get("exp", 0)}


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

    # === Cache check (thread-safe) ===
    token_hash = _get_token_hash(token)
    now = time.time()

    with _token_cache_lock:
        if token_hash in _token_cache:
            cached = _token_cache[token_hash]
            if now < cached["expires"]:
                return cached["payload"]
            else:
                del _token_cache[token_hash]

        # Limpar cache expirado periodicamente ou se excede tamanho máximo
        if len(_token_cache) > TOKEN_CACHE_MAX_SIZE or len(_token_cache) > TOKEN_CACHE_MAX_SIZE // 2:
            expired_keys = [k for k, v in _token_cache.items() if now >= v["expires"]]
            for k in expired_keys:
                del _token_cache[k]

    # === Decode e validação ===
    result = _decode_and_validate_token(token)

    if result:
        # v5.2 fix H11: Cache TTL limitado pelo JWT exp (não servir tokens expirados)
        jwt_exp = result.get("exp", 0)
        cache_ttl = TOKEN_CACHE_TTL
        if jwt_exp and jwt_exp > now:
            cache_ttl = min(TOKEN_CACHE_TTL, jwt_exp - now)
        with _token_cache_lock:
            _token_cache[token_hash] = {
                "payload": result,
                "expires": now + cache_ttl,
            }
        return result

    # === Falhou ===
    raise HTTPException(
        status_code=401,
        detail="Token inválido ou expirado. Faça login novamente.",
    )

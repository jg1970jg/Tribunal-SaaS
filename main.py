# -*- coding: utf-8 -*-
"""
MAIN - Tribunal SaaS V2 (FastAPI)
============================================================
Servidor principal com:
  - Conexão ao Supabase
  - Rota de saúde (GET /health)
  - Rota protegida de teste (GET /me)
  - Rota de análise (POST /analyze)
============================================================
"""

import os
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, UploadFile, File, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from auth_service import get_current_user, get_supabase
from src.engine import (
    executar_analise_documento,
    InsufficientBalanceError,
    InvalidDocumentError,
    MissingApiKeyError,
    EngineError,
)

logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente (.env)
load_dotenv()


# ============================================================
# LIFESPAN - startup / shutdown
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa recursos no arranque e limpa no shutdown."""
    # -- Startup --
    # Validar que as variáveis obrigatórias existem
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")

    if not supabase_url or not supabase_key:
        print("[AVISO] SUPABASE_URL ou SUPABASE_KEY não definidos no .env")
    else:
        # Testar conexão criando o cliente
        get_supabase()
        print(f"[OK] Supabase conectado: {supabase_url[:40]}...")

    print("[OK] Tribunal SaaS V2 - Servidor iniciado.")

    yield

    # -- Shutdown --
    print("[OK] Servidor encerrado.")


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Tribunal SaaS V2",
    description="API para análise jurídica com IA",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS - permitir frontend (ajustar origens em produção)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ROTAS PÚBLICAS
# ============================================================

@app.get("/health")
async def health():
    """Rota de saúde - verifica se o servidor está online."""
    return {"status": "online"}


# ============================================================
# ROTAS PROTEGIDAS (requerem autenticação)
# ============================================================

@app.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """Retorna dados do utilizador autenticado (rota de teste)."""
    return {
        "user_id": user["id"],
        "email": user["email"],
    }


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    area_direito: str = Form("Civil"),
    perguntas_raw: str = Form(""),
    titulo: str = Form(""),
    user: dict = Depends(get_current_user),
):
    """
    Executa a análise jurídica completa de um documento.

    - Verifica saldo do utilizador (mínimo 2.00 EUR)
    - Extrai texto do ficheiro (PDF, DOCX, XLSX, TXT)
    - Executa pipeline de 4 fases (Extração, Auditoria, Julgamento, Presidente)
    - Retorna resultado completo em JSON
    """
    try:
        file_bytes = await file.read()

        resultado = executar_analise_documento(
            user_id=user["id"],
            file_bytes=file_bytes,
            filename=file.filename,
            area_direito=area_direito,
            perguntas_raw=perguntas_raw,
            titulo=titulo,
        )

        return resultado.to_dict()

    except InsufficientBalanceError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Saldo insuficiente. Por favor carregue a conta. "
                   f"(Saldo atual: {e.saldo_atual:.2f} EUR, "
                   f"mínimo: {e.saldo_minimo:.2f} EUR)",
        )

    except InvalidDocumentError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    except MissingApiKeyError as e:
        logger.error(f"API Key em falta: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço temporariamente indisponível. Contacte o suporte.",
        )

    except EngineError as e:
        logger.error(f"Erro do engine: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    except Exception as e:
        logger.exception("Erro inesperado no endpoint /analyze")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno do servidor.",
        )

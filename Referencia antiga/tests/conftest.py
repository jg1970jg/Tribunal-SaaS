# -*- coding: utf-8 -*-
"""
TRIBUNAL GOLDENMASTER - Configuração de Testes (pytest)
============================================================
Fixtures comuns para todos os testes.
============================================================
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Adicionar diretório raiz ao path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Configurar ambiente de teste
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("MAX_BUDGET_USD", "0.10")  # Budget baixo para testes
os.environ.setdefault("MAX_TOKENS_TOTAL", "10000")  # Limite baixo para testes


@pytest.fixture
def fixtures_dir() -> Path:
    """Retorna diretório de fixtures."""
    return ROOT_DIR / "fixtures"


@pytest.fixture
def sample_txt_path(fixtures_dir) -> Path:
    """Retorna path para sample_input.txt."""
    return fixtures_dir / "sample_input.txt"


@pytest.fixture
def sample_txt_content(sample_txt_path) -> str:
    """Retorna conteúdo do sample_input.txt."""
    if sample_txt_path.exists():
        return sample_txt_path.read_text(encoding="utf-8")
    return "Texto de teste para análise jurídica."


@pytest.fixture
def temp_output_dir() -> Path:
    """Cria diretório temporário para outputs de teste."""
    with tempfile.TemporaryDirectory(prefix="tribunal_test_") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_llm_response():
    """Mock de resposta LLM para testes sem API."""
    from src.llm_client import LLMResponse

    return LLMResponse(
        content="Resposta mock para teste.",
        model="mock/model",
        role="assistant",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        latency_ms=100.0,
        success=True,
        api_used="mock",
    )


@pytest.fixture
def sample_citacao():
    """Citação legal de exemplo para testes."""
    return "artigo 483º do Código Civil"


@pytest.fixture
def sample_documento_content():
    """DocumentContent de exemplo para testes."""
    from src.document_loader import DocumentContent

    return DocumentContent(
        filename="teste.txt",
        extension=".txt",
        text="""
        CONTRATO DE ARRENDAMENTO

        Nos termos do artigo 1022º do Código Civil, o senhorio e arrendatário
        celebram o presente contrato.

        Renda mensal: 500,00 €
        Data de início: 01/01/2024

        Aplicam-se as disposições da Lei n.º 6/2006 de 27 de Fevereiro.
        """,
        num_pages=1,
        num_chars=300,
        num_words=45,
        success=True,
    )

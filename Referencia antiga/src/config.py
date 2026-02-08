# -*- coding: utf-8 -*-
"""
CONFIGURAÇÃO TRIBUNAL GOLDENMASTER - DUAL API SYSTEM
═══════════════════════════════════════════════════════════════════════════

NOVIDADES:
✅ Suporte API OpenAI directa (usa teu saldo OpenAI!)
✅ Fallback automático OpenRouter
✅ Escolha modelos premium (5.2 vs 5.2-pro)
✅ Gestão API keys na interface

CONFIGURAÇÃO ACTUAL:
- 5 Extratores com PROMPT UNIVERSAL
- Auditores: A2=Opus 4.5, A3=Gemini 3 Pro
- Juízes: J3=Gemini 3 Pro
- Chefe: Configurável (5.2 ou 5.2-pro)
- Presidente: Configurável (5.2 ou 5.2-pro)
═══════════════════════════════════════════════════════════════════════════
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Importar prompts maximizados
from prompts_maximos import PROMPT_EXTRATOR_UNIVERSAL, PROMPT_AGREGADOR_PRESERVADOR

BASE_DIR = Path(__file__).resolve().parent.parent

# Carregar .env da raiz do projecto (explícito para evitar ambiguidade)
load_dotenv(BASE_DIR / ".env", override=True)
SRC_DIR = BASE_DIR / "src"
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"
HISTORICO_DIR = BASE_DIR / "historico"

DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORICO_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DATA_DIR / "legislacao_pt.db"

# =============================================================================
# API KEYS - DUAL SYSTEM
# =============================================================================

# OpenAI API (directa - usa teu saldo OpenAI!)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# OpenRouter API (backup + outros modelos)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Configurações gerais
API_TIMEOUT = 180
API_MAX_RETRIES = 5
LOG_LEVEL = "INFO"

# =============================================================================
# MODELOS PREMIUM - OPÇÕES DISPONÍVEIS
# =============================================================================

# Opções para Chefe dos Auditores
CHEFE_MODEL_OPTIONS = {
    "gpt-5.2": {
        "model": "openai/gpt-5.2",  # Formato OpenRouter (sem data)
        "display_name": "GPT-5.2 (económico)",
        "cost_per_analysis": 0.02,
        "description": "Excelente qualidade, custo controlado",
        "recommended": True,
    },
    "gpt-5.2-pro": {
        "model": "openai/gpt-5.2-pro",  # Formato OpenRouter (sem data)
        "display_name": "GPT-5.2-PRO (premium)",
        "cost_per_analysis": 0.20,
        "description": "Máxima precisão, custo elevado",
        "recommended": False,
    },
}

# Opções para Presidente dos Juízes
PRESIDENTE_MODEL_OPTIONS = {
    "gpt-5.2": {
        "model": "openai/gpt-5.2",  # Formato OpenRouter (sem data)
        "display_name": "GPT-5.2 (económico)",
        "cost_per_analysis": 0.02,
        "description": "Excelente qualidade, custo controlado",
        "recommended": True,
    },
    "gpt-5.2-pro": {
        "model": "openai/gpt-5.2-pro",  # Formato OpenRouter (sem data)
        "display_name": "GPT-5.2-PRO (premium)",
        "cost_per_analysis": 0.20,
        "description": "Máxima precisão, custo elevado",
        "recommended": False,
    },
}

# Defaults (podem ser alterados pelo utilizador na interface)
CHEFE_MODEL_DEFAULT = "gpt-5.2"  # económico por defeito
PRESIDENTE_MODEL_DEFAULT = "gpt-5.2"  # económico por defeito

# =============================================================================
# MODELOS ACTUAIS (usados se não houver escolha do utilizador)
# =============================================================================

PRESIDENTE_MODEL = PRESIDENTE_MODEL_OPTIONS[PRESIDENTE_MODEL_DEFAULT]["model"]
CHEFE_MODEL = CHEFE_MODEL_OPTIONS[CHEFE_MODEL_DEFAULT]["model"]
AGREGADOR_MODEL = "openai/gpt-5.2"  # Agregador sempre 5.2 (via OpenRouter)

# =============================================================================
# CENÁRIO A - CHUNKING AUTOMÁTICO
# =============================================================================

CHUNK_SIZE_CHARS = 50000  # Sweet spot: até 35 pág sem chunking
CHUNK_OVERLAP_CHARS = 2500  # 5% overlap

# =============================================================================
# PROVENIÊNCIA E COBERTURA (NOVO!)
# =============================================================================

# Ativar sistema unificado de proveniência
# - Quando True: cada item tem source_spans com offsets absolutos
# - Permite auditoria de cobertura completa
# - Deteta conflitos entre extratores
USE_UNIFIED_PROVENANCE = True  # Ativar proveniência e cobertura

# Alertar se cobertura < X%
COVERAGE_MIN_THRESHOLD = 95.0

# Ignorar gaps menores que X chars na auditoria de cobertura
COVERAGE_MIN_GAP_SIZE = 100

# =============================================================================
# META-INTEGRIDADE E VALIDAÇÃO DE COERÊNCIA (NOVO!)
# =============================================================================

# Ativar validação de meta-integridade após cada run
# - Verifica coerência entre ficheiros gerados
# - Valida doc_ids referenciados existem
# - Valida consistência de contagens e timestamps
USE_META_INTEGRITY = True

# Gerar relatório de meta-integridade mesmo se desativado
# (útil para debugging)
ALWAYS_GENERATE_META_REPORT = False

# Tolerância em minutos para validação de timestamps
META_INTEGRITY_TIMESTAMP_TOLERANCE = 60

# Tolerância percentual para validação de pages_total
META_INTEGRITY_PAGES_TOLERANCE_PERCENT = 5.0

# Tolerância absoluta para contagem de citations
META_INTEGRITY_CITATION_COUNT_TOLERANCE = 5

# =============================================================================
# POLICY DE CONFIANÇA DETERMINÍSTICA
# =============================================================================

# Penalidade máxima global (0.0-1.0)
CONFIDENCE_MAX_PENALTY = 0.50

# Teto de confiança para erros severos (ERROR_RECOVERED, RANGE_INVALID)
CONFIDENCE_SEVERE_CEILING = 0.75

# Aplicar política de confiança automaticamente
APPLY_CONFIDENCE_POLICY = True

# =============================================================================
# 5 EXTRATORES COM PROMPT UNIVERSAL
# =============================================================================

LLM_CONFIGS = [
    {
        "id": "E1",
        "role": "Extrator Completo",
        "model": "anthropic/claude-opus-4.5",
        "temperature": 0.0,
        "instructions": PROMPT_EXTRATOR_UNIVERSAL
    },
    {
        "id": "E2",
        "role": "Extrator Completo",
        "model": "google/gemini-3-flash-preview",
        "temperature": 0.0,
        "instructions": PROMPT_EXTRATOR_UNIVERSAL
    },
    {
        "id": "E3",
        "role": "Extrator Completo",
        "model": "openai/gpt-4o",
        "temperature": 0.0,
        "instructions": PROMPT_EXTRATOR_UNIVERSAL
    },
    {
        "id": "E4",
        "role": "Extrator Completo",
        "model": "anthropic/claude-3-5-sonnet",
        "temperature": 0.0,
        "instructions": PROMPT_EXTRATOR_UNIVERSAL
    },
    {
        "id": "E5",
        "role": "Extrator Completo",
        "model": "deepseek/deepseek-chat",
        "temperature": 0.0,
        "instructions": PROMPT_EXTRATOR_UNIVERSAL
    },
]

EXTRATOR_MODELS = [cfg["model"] for cfg in LLM_CONFIGS]
EXTRATOR_MODELS_NEW = EXTRATOR_MODELS

# =============================================================================
# AUDITORES - GPT-5.2 + OPUS 4.5 + GEMINI 3 PRO + GROK 4.1 (4 auditores!)
# =============================================================================

AUDITOR_MODELS = [
    "openai/gpt-5.2",              # A1: GPT-5.2 (era gpt-4o)
    "anthropic/claude-opus-4.5",   # A2: Claude Opus 4.5
    "google/gemini-3-pro-preview", # A3: Gemini 3 Pro
    "x-ai/grok-4.1-fast",          # A4: xAI Grok 4.1 Fast (NOVO!)
]

AUDITORES = [
    {"id": "A1", "model": AUDITOR_MODELS[0], "temperature": 0.1},
    {"id": "A2", "model": AUDITOR_MODELS[1], "temperature": 0.0},
    {"id": "A3", "model": AUDITOR_MODELS[2], "temperature": 0.0},
    {"id": "A4", "model": AUDITOR_MODELS[3], "temperature": 0.1},  # NOVO!
]

# =============================================================================
# JUÍZES - GPT-5.2 + OPUS 4.5 + GEMINI 3 PRO
# =============================================================================

JUIZ_MODELS = [
    "openai/gpt-5.2",              # J1: GPT-5.2 (era gpt-4o)
    "anthropic/claude-opus-4.5",   # J2: Claude Opus 4.5
    "google/gemini-3-pro-preview"  # J3: Gemini 3 Pro
]

JUIZES = [
    {"id": "J1", "model": JUIZ_MODELS[0], "temperature": 0.2},
    {"id": "J2", "model": JUIZ_MODELS[1], "temperature": 0.1},
    {"id": "J3", "model": JUIZ_MODELS[2], "temperature": 0.0},
]

# =============================================================================
# PROMPTS SISTEMA
# =============================================================================

SYSTEM_AGREGADOR = PROMPT_AGREGADOR_PRESERVADOR
SYSTEM_CHEFE = "Consolide auditorias."
SYSTEM_PRESIDENTE = "Decisão final fundamentada."

# =============================================================================
# CONFIGURAÇÕES RESTANTES (inalteradas)
# =============================================================================

AREAS_DIREITO = ["Civil", "Penal", "Trabalho", "Família", "Administrativo", "Constitucional", "Comercial", "Tributário", "Ambiental", "Consumidor"]

SIMBOLOS_VERIFICACAO = {"aprovado": "✓", "rejeitado": "✗", "atencao": "⚠"}

CORES = {"aprovado": "#28a745", "rejeitado": "#dc3545", "atencao": "#ffc107", "neutro": "#6c757d", "primaria": "#1a5f7a", "secundaria": "#57c5b6"}

# =============================================================================
# VISION OCR - Extração de texto de PDFs escaneados via LLM Vision
# =============================================================================

VISION_OCR_MODEL = "openai/gpt-4o"  # Fallback para Vision OCR básico
VISION_OCR_MAX_TOKENS = 4096
VISION_OCR_TEMPERATURE = 0.0

# Modelos com capacidade de visão (podem receber imagens)
# Usados no pipeline para TODOS os extratores lerem PDFs escaneados
VISION_CAPABLE_MODELS = {
    "anthropic/claude-opus-4.5",
    "google/gemini-3-flash-preview",
    "openai/gpt-4o",
    "anthropic/claude-3-5-sonnet",
}

DRE_BASE_URL = "https://diariodarepublica.pt"
DRE_SEARCH_URL = "https://diariodarepublica.pt/dr/pesquisa"

EXPORT_CONFIG = {"pdf_title": "Tribunal GoldenMaster", "pdf_author": "Sistema", "date_format": "%d/%m/%Y %H:%M:%S"}

SUPPORTED_EXTENSIONS = {".pdf": "PDF", ".docx": "Word", ".xlsx": "Excel", ".txt": "Texto"}

MAX_PERGUNTAS_WARN = 10
MAX_CHARS_PERGUNTA_WARN = 2000
MAX_PERGUNTAS_HARD = 20
MAX_CHARS_PERGUNTA_HARD = 4000
MAX_CHARS_TOTAL_PERGUNTAS_HARD = 60000
PERGUNTAS_HARD_LIMIT = MAX_PERGUNTAS_HARD
PERGUNTAS_SOFT_LIMIT = MAX_PERGUNTAS_WARN

# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================

def get_chefe_model(choice: str = None) -> str:
    """
    Retorna modelo do Chefe conforme escolha do utilizador.
    
    Args:
        choice: "gpt-5.2" ou "gpt-5.2-pro" (None = default)
    
    Returns:
        Nome do modelo
    """
    if choice is None:
        choice = CHEFE_MODEL_DEFAULT
    return CHEFE_MODEL_OPTIONS.get(choice, CHEFE_MODEL_OPTIONS[CHEFE_MODEL_DEFAULT])["model"]


def get_presidente_model(choice: str = None) -> str:
    """
    Retorna modelo do Presidente conforme escolha do utilizador.
    
    Args:
        choice: "gpt-5.2" ou "gpt-5.2-pro" (None = default)
    
    Returns:
        Nome do modelo
    """
    if choice is None:
        choice = PRESIDENTE_MODEL_DEFAULT
    return PRESIDENTE_MODEL_OPTIONS.get(choice, PRESIDENTE_MODEL_OPTIONS[PRESIDENTE_MODEL_DEFAULT])["model"]


# Debug prints desativados para compatibilidade Windows
# print("[OK] Config DUAL API carregado!")
# print(f"   CHUNK_SIZE: {CHUNK_SIZE_CHARS:,} chars")
# print(f"   OVERLAP: {CHUNK_OVERLAP_CHARS:,} chars")

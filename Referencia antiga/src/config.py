# -*- coding: utf-8 -*-
"""
CONFIGURAÇÃO TRIBUNAL GOLDENMASTER - DUAL API SYSTEM
═══════════════════════════════════════════════════════════════════════════

NOVIDADES:
- Suporte API OpenAI directa (usa teu saldo OpenAI!)
- Fallback automático OpenRouter
- Escolha modelos premium (5.2 vs 5.2-pro)
- Gestão API keys na interface

CONFIGURAÇÃO ACTUAL:
- 5 Extratores com PROMPT UNIVERSAL
- Auditores: A2=Opus 4.6, A3=Gemini 3 Pro
- Juízes: J3=Gemini 3 Pro
- Chefe: Configurável (5.2 ou 5.2-pro)
- Presidente: Configurável (5.2 ou 5.2-pro)
- NOVO: Failover GPT-5.2 → GPT-4.1 → Grok (3 níveis)
- NOVO: max_tokens dinâmico por tamanho de documento
═══════════════════════════════════════════════════════════════════════════
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Importar prompts maximizados
from prompts_maximos import PROMPT_EXTRATOR_UNIVERSAL, PROMPT_AGREGADOR_PRESERVADOR

logger = logging.getLogger(__name__)

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
USE_UNIFIED_PROVENANCE = True

# Alertar se cobertura < X%
COVERAGE_MIN_THRESHOLD = 95.0

# Ignorar gaps menores que X chars na auditoria de cobertura
COVERAGE_MIN_GAP_SIZE = 100

# =============================================================================
# META-INTEGRIDADE E VALIDAÇÃO DE COERÊNCIA (NOVO!)
# =============================================================================

USE_META_INTEGRITY = True
ALWAYS_GENERATE_META_REPORT = False
META_INTEGRITY_TIMESTAMP_TOLERANCE = 60
META_INTEGRITY_PAGES_TOLERANCE_PERCENT = 5.0
META_INTEGRITY_CITATION_COUNT_TOLERANCE = 5

# =============================================================================
# POLICY DE CONFIANÇA DETERMINÍSTICA
# =============================================================================

CONFIDENCE_MAX_PENALTY = 0.50
CONFIDENCE_SEVERE_CEILING = 0.75
APPLY_CONFIDENCE_POLICY = True

# =============================================================================
# 5 EXTRATORES COM PROMPT UNIVERSAL
# =============================================================================

LLM_CONFIGS = [
    {
        "id": "E1",
        "role": "Extrator Completo",
        "model": "anthropic/claude-opus-4.6",       # ACTUALIZADO: era claude-opus-4.5
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
# AUDITORES - GPT-5.2 + OPUS 4.6 + GEMINI 3 PRO + GROK 4.1 (4 auditores!)
# =============================================================================

AUDITOR_MODELS = [
    "openai/gpt-5.2",              # A1: GPT-5.2 (titular, failover → gpt-4.1)
    "anthropic/claude-opus-4.6",   # A2: Claude Opus 4.6 (ACTUALIZADO: era 4.5, ctx 1.000K, out 128K)
    "google/gemini-3-pro-preview", # A3: Gemini 3 Pro (ctx 1.049K, out 66K)
    "x-ai/grok-4.1-fast",          # A4: xAI Grok 4.1 Fast (ctx 2.000K, out 30K)
]

AUDITORES = [
    {"id": "A1", "model": AUDITOR_MODELS[0], "temperature": 0.1},
    {"id": "A2", "model": AUDITOR_MODELS[1], "temperature": 0.0},
    {"id": "A3", "model": AUDITOR_MODELS[2], "temperature": 0.0},
    {"id": "A4", "model": AUDITOR_MODELS[3], "temperature": 0.1},
]

# =============================================================================
# JUÍZES - GPT-5.2 + OPUS 4.6 + GEMINI 3 PRO
# =============================================================================

JUIZ_MODELS = [
    "openai/gpt-5.2",              # J1: GPT-5.2 (titular, failover → gpt-4.1)
    "anthropic/claude-opus-4.6",   # J2: Claude Opus 4.6 (ACTUALIZADO: era 4.5, ctx 1.000K, out 128K)
    "google/gemini-3-pro-preview"  # J3: Gemini 3 Pro (ctx 1.049K, out 66K)
]

JUIZES = [
    {"id": "J1", "model": JUIZ_MODELS[0], "temperature": 0.2},
    {"id": "J2", "model": JUIZ_MODELS[1], "temperature": 0.1},
    {"id": "J3", "model": JUIZ_MODELS[2], "temperature": 0.0},
]

# =============================================================================
# LIMITES DE CONTEXTO E OUTPUT POR MODELO (tokens)
# Fonte: OpenRouter API (verificado 2025-02-11)
# =============================================================================

MODEL_CONTEXT_LIMITS = {
    "openai/gpt-5.2":              400_000,
    "openai/gpt-5.2-pro":          400_000,
    "openai/gpt-4.1":            1_048_000,
    "anthropic/claude-opus-4.6": 1_000_000,
    "google/gemini-3-pro-preview": 1_049_000,
    "x-ai/grok-4.1-fast":       2_000_000,
    # Extratores
    "google/gemini-3-flash-preview": 1_049_000,
    "openai/gpt-4o":               128_000,
    "anthropic/claude-3-5-sonnet":  200_000,
    "deepseek/deepseek-chat":      128_000,
}

MODEL_MAX_OUTPUT = {
    "openai/gpt-5.2":              128_000,
    "openai/gpt-5.2-pro":          128_000,
    "openai/gpt-4.1":               32_768,
    "anthropic/claude-opus-4.6":   128_000,
    "google/gemini-3-pro-preview":  66_000,
    "x-ai/grok-4.1-fast":          30_000,
    # Extratores
    "google/gemini-3-flash-preview": 66_000,
    "openai/gpt-4o":                16_384,
    "anthropic/claude-3-5-sonnet":   8_192,
    "deepseek/deepseek-chat":        8_192,
}

# =============================================================================
# FAILOVER: GPT-5.2 → GPT-4.1 → GROK (3 NÍVEIS)
# Quando titular não cabe, suplente assume automaticamente
# =============================================================================

FALLBACK_MODEL_NIVEL2 = "openai/gpt-4.1"       # Suplente (ctx 1.048K, out 32K)
FALLBACK_MODEL_NIVEL3 = "x-ai/grok-4.1-fast"    # Emergência (ctx 2.000K, out 30K)

# Limites em caracteres (tokens x 4) com margem de 30% para prompt overhead
LIMITE_NIVEL1_CHARS = 1_120_000    # ~400K tokens x 4 x 0.70 = docs até ~280 páginas
LIMITE_NIVEL2_CHARS = 2_930_000    # ~1.048K tokens x 4 x 0.70 = docs até ~730 páginas
LIMITE_NIVEL3_CHARS = 5_600_000    # ~2.000K tokens x 4 x 0.70 = docs até ~2.500 páginas

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

SIMBOLOS_VERIFICACAO = {"aprovado": "V", "rejeitado": "X", "atencao": "!"}

CORES = {"aprovado": "#28a745", "rejeitado": "#dc3545", "atencao": "#ffc107", "neutro": "#6c757d", "primaria": "#1a5f7a", "secundaria": "#57c5b6"}

# =============================================================================
# VISION OCR - Extracção de texto de PDFs escaneados via LLM Vision
# =============================================================================

VISION_OCR_MODEL = "openai/gpt-4o"  # Fallback para Vision OCR básico
VISION_OCR_MAX_TOKENS = 4096
VISION_OCR_TEMPERATURE = 0.0

# Modelos com capacidade de visão (podem receber imagens)
VISION_CAPABLE_MODELS = {
    "anthropic/claude-opus-4.6",       # ACTUALIZADO: era claude-opus-4.5
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


# =============================================================================
# FUNÇÕES NOVAS: MAX_TOKENS DINÂMICO + FAILOVER + AVISOS
# =============================================================================

def calcular_max_tokens(doc_chars: int, modelo: str) -> int:
    """
    Calcula max_tokens dinâmico baseado no tamanho do documento,
    respeitando o limite real de cada modelo.

    Args:
        doc_chars: Número de caracteres do documento
        modelo: ID do modelo (ex: "openai/gpt-5.2")

    Returns:
        max_tokens adequado para o modelo e tamanho do documento
    """
    # Escala dinâmica por tamanho de documento
    if doc_chars < 50_000:
        dinamico = 16_384
    elif doc_chars < 150_000:
        dinamico = 20_000
    elif doc_chars < 300_000:
        dinamico = 24_000
    elif doc_chars < 500_000:
        dinamico = 28_000
    else:
        dinamico = 32_000

    # Nunca ultrapassar o limite real do modelo
    limite_modelo = MODEL_MAX_OUTPUT.get(modelo, 16_384)
    resultado = min(dinamico, limite_modelo)

    logger.info(
        f"[MAX_TOKENS] doc={doc_chars:,} chars | modelo={modelo} | "
        f"dinamico={dinamico:,} | limite_modelo={limite_modelo:,} | "
        f"final={resultado:,}"
    )

    return resultado


def selecionar_modelo_com_failover(modelo_titular: str, doc_chars: int, papel: str) -> str:
    """
    Seleciona o modelo adequado com failover automático.

    Cadeia: GPT-5.2 (400K) → GPT-4.1 (1.048K) → Grok (2.000K)

    Args:
        modelo_titular: Modelo original (ex: "openai/gpt-5.2")
        doc_chars: Número de caracteres do documento
        papel: Nome do papel (ex: "A1", "J1", "Chefe", "Presidente")

    Returns:
        Modelo a usar (titular ou suplente)
    """
    # Só aplica failover a modelos GPT-5.2 (titular)
    if "gpt-5.2" not in modelo_titular:
        return modelo_titular

    # Nível 1: Documento cabe no GPT-5.2
    if doc_chars <= LIMITE_NIVEL1_CHARS:
        return modelo_titular

    # Nível 2: Suplente GPT-4.1
    if doc_chars <= LIMITE_NIVEL2_CHARS:
        logger.warning(
            f"[FAILOVER] {papel}: {modelo_titular} excluido "
            f"(doc {doc_chars:,} chars > {LIMITE_NIVEL1_CHARS:,}). "
            f"Usando suplente: {FALLBACK_MODEL_NIVEL2}"
        )
        return FALLBACK_MODEL_NIVEL2

    # Nível 3: Emergência Grok
    if doc_chars <= LIMITE_NIVEL3_CHARS:
        logger.warning(
            f"[FAILOVER-EMERGENCIA] {papel}: GPT-4.1 tambem excluido "
            f"(doc {doc_chars:,} chars > {LIMITE_NIVEL2_CHARS:,}). "
            f"Usando emergencia: {FALLBACK_MODEL_NIVEL3}"
        )
        return FALLBACK_MODEL_NIVEL3

    # Nível 4: Impossível
    logger.error(
        f"[FAILOVER-IMPOSSIVEL] {papel}: Documento demasiado grande "
        f"({doc_chars:,} chars > {LIMITE_NIVEL3_CHARS:,}). "
        f"Nenhum modelo consegue processar."
    )
    raise ValueError(
        f"Documento excede o limite maximo de processamento "
        f"({doc_chars:,} caracteres). Por favor divida o documento em partes menores."
    )


def classificar_documento(doc_chars: int) -> dict:
    """
    Classifica o documento por nível e retorna info para aviso ao utilizador.

    Níveis:
        1 = Normal (sem aviso)
        2 = Extenso (aviso informativo, sem confirmação)
        3 = Emergência (aviso + pede confirmação)
        4 = Impossível (bloqueio total)

    Returns:
        dict com: nivel, mensagem, requer_confirmacao, pode_processar
    """
    if doc_chars <= LIMITE_NIVEL1_CHARS:
        return {
            "nivel": 1,
            "mensagem": None,
            "requer_confirmacao": False,
            "pode_processar": True,
        }

    elif doc_chars <= LIMITE_NIVEL2_CHARS:
        return {
            "nivel": 2,
            "mensagem": (
                "Documento extenso detectado. O sistema activou automaticamente "
                "o processamento optimizado para documentos longos."
            ),
            "requer_confirmacao": False,
            "pode_processar": True,
        }

    elif doc_chars <= LIMITE_NIVEL3_CHARS:
        return {
            "nivel": 3,
            "mensagem": (
                "Documento excepcionalmente extenso. O processamento de alta "
                "capacidade sera utilizado. Os resultados poderao apresentar menor "
                "detalhe na consolidacao final. Deseja continuar?"
            ),
            "requer_confirmacao": True,
            "pode_processar": True,
        }

    else:
        paginas_estimadas = doc_chars // 2250
        return {
            "nivel": 4,
            "mensagem": (
                f"Documento excede o limite maximo de processamento "
                f"(~{paginas_estimadas:,} paginas estimadas). "
                f"Por favor divida o documento em partes menores."
            ),
            "requer_confirmacao": False,
            "pode_processar": False,
        }

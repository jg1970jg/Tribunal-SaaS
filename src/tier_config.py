"""
TIER CONFIGURATION - Sistema Bronze/Prata/Ouro
==================================================
Define os tiers disponíveis, modelos associados e preços.
Margem de lucro: 100%
Margem de segurança: 50% (calibrado 15-Fev-2026)

PALAVRAS VETADAS (NUNCA usar):
  ❌ "advogado sénior" / "nível de advogado"
  ❌ Percentagens (95%, 99%, etc.)
  ❌ "falsos positivos"
  ❌ "garantimos" / "sem erros" / "infalível"
  ❌ Números de páginas (< 20, 20-50, > 50)
  ❌ "STJ", "Constitucional", "última instância"
  ❌ "Litígio de alto valor"
  ❌ Versões (4.5, 5.2, etc.)
"""

from typing import Any
from enum import Enum


class TierLevel(str, Enum):
    """Níveis de tier disponíveis."""
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"


# ============================================================
# CUSTOS REAIS DOS MODELOS (em USD)
# ============================================================
# Baseado em análise com cache ativo (47% savings)

MODEL_COSTS = {
    # Extratores (Fase 1) — v4.0
    "haiku-4.5": 0.04,           # E1
    "gemini-3-pro": 0.12,        # E2
    "gpt-5.2": 0.12,             # E3
    "sonnet-4.6": 0.08,          # E4
    "llama-3.3-70b": 0.03,        # E5 Llama 3.3 (Meta)
    "gpt-5-nano": 0.02,            # E6 GPT-5 Nano (visual) — v5.1
    "nemotron-70b": 0.03,         # E7 Nemotron (NVIDIA)

    # Auditores (Fase 3) — v5.0
    "gpt-5.2-audit": 0.15,       # A1
    "gemini-3-pro-audit": 0.15,  # A2
    "claude-sonnet-4.6-audit": 0.10,  # A3
    "qwen3-max-audit": 0.15,    # A4 (Qwen3 Max Thinking: $1.20/$6.00 per M)
    "claude-opus-4-audit": 0.40, # A5 (Elite only)

    # Juízes (Fase 4) — v4.0
    "o1-pro-judge": 2.50,        # J1 (reasoning, expensive)
    "deepseek-r1-judge": 0.15,   # J2 (reasoning)
    "claude-opus-4-judge": 0.45, # J3

    # Conselheiro-Mor (Fase 5)
    "gpt-5.2-presidente": 0.30,
    "claude-opus-4-presidente": 0.50,
    "gpt-5.2-pro-presidente": 0.80,
}


# ============================================================
# CONFIGURAÇÃO DE TIERS
# ============================================================

TIER_CONFIG = {
    # ================================================================
    # STANDARD (Bronze) — v4.0 Handover
    # Fases 1-5 base, GPT-5.2 na síntese
    # ================================================================
    TierLevel.BRONZE: {
        "label": "Standard",
        "icon": "🥉",
        "description": "Análise automática eficiente",
        "color_gradient": "from-amber-600 to-amber-800",
        "color_border": "border-amber-500",
        "color_bg": "bg-gradient-to-br from-amber-50 to-orange-50",

        "models": {
            "president": "gpt-5.2",              # Fase 5: GPT-5.2
            "audit_a5_opus": False,               # Sem A5 Opus
        },

        "estimated_costs": {
            "triage": 0.01,          # Fase 0 (3 IAs baratas) — v5.1 corrigido
            "extraction": 1.50,      # Fase 1 (6 IAs: sem E4 Sonnet, E6=gpt-5-nano) — v5.1
            "aggregation": 0.80,     # Fase 2 (agregador dedup)
            "audit": 2.10,           # Fase 3 (4 IAs: GPT-5.2, Gemini Pro, Sonnet, Qwen3 Max)
            "judgment": 1.00,        # Fase 4 (J3 Sonnet em vez de Opus) — v5.1
            "president": 0.60,       # Fase 5 (GPT-5.2)
        },

        "features": [
            "Análise automática eficiente",
            "6 IAs na extração + 4 auditores",
            "3 juízes de raciocínio",
            "Relatório estruturado em pt-PT",
        ],

        "ideal_cases": {
            "title": "Ideal para:",
            "cases": [
                "Documentos do dia-a-dia",
                "Revisão de conformidade",
                "Casos diretos e claros",
                "Análise com múltiplas IAs",
            ]
        },
        "time_estimate": "15-25 min",
        "included": True,
    },

    # ================================================================
    # PREMIUM (Silver) — v4.0 Handover
    # Fases 1-5 iguais, Opus 4.6 na síntese
    # ================================================================
    TierLevel.SILVER: {
        "label": "Premium",
        "icon": "🥈",
        "description": "Análise profunda com IA avançada",
        "color_gradient": "from-slate-400 to-slate-700",
        "color_border": "border-slate-400",
        "color_bg": "bg-gradient-to-br from-slate-50 to-gray-100",
        "badge": "⭐ RECOMENDADO",

        "models": {
            "president": "claude-opus-4",         # Fase 5: Opus 4.6
            "audit_a5_opus": False,               # Sem A5 Opus
        },

        "estimated_costs": {
            "triage": 0.10,
            "extraction": 3.00,
            "aggregation": 0.80,
            "audit": 2.10,           # Fase 3 (4 IAs: GPT-5.2, Gemini Pro, Sonnet, Qwen3 Max)
            "judgment": 3.50,
            "president": 2.50,       # Opus 4.6 ($15/$75 per M tokens)
        },

        "features": [
            "Opus 4.6 redige o parecer final",
            "Nuance e doutrina superior",
            "7 IAs + 4 auditores + 3 juízes reasoning",
            "Relatório focado e assertivo",
        ],

        "ideal_cases": {
            "title": "Ideal para:",
            "cases": [
                "Documentos importantes",
                "Casos com várias partes",
                "Situações que requerem atenção especial",
                "Quando quer certeza na análise",
            ]
        },
        "time_estimate": "15-25 min",
        "included": False,
    },

    # ================================================================
    # ELITE (Gold) — v4.0 Handover
    # Fases 1-5 + A5 Opus na auditoria + GPT-5.2-Pro na síntese
    # ================================================================
    TierLevel.GOLD: {
        "label": "Elite",
        "icon": "🥇",
        "description": "Máxima qualidade com 5 auditores",
        "color_gradient": "from-yellow-400 to-yellow-700",
        "color_border": "border-yellow-400",
        "color_bg": "bg-gradient-to-br from-yellow-50 to-amber-100",
        "badge": "💎 MÁXIMA QUALIDADE",

        "models": {
            "president": "gpt-5.2-pro",          # Fase 5: GPT-5.2-Pro
            "audit_a5_opus": True,                # A5 Opus como auditor sénior
        },

        "estimated_costs": {
            "triage": 0.10,
            "extraction": 3.00,
            "aggregation": 0.80,
            "audit": 4.10,           # A1-A4 (Qwen3 Max) + A5 Opus ($15/$75 per M tokens)
            "judgment": 3.50,
            "president": 3.50,       # GPT-5.2-Pro ($21/$168 per M tokens)
        },

        "features": [
            "GPT-5.2-Pro redige o parecer final",
            "5 auditores (incluindo Opus como sénior)",
            "Máxima profundidade e redação",
            "Deteta nuances jurídicas subtis",
        ],

        "ideal_cases": {
            "title": "Ideal para:",
            "cases": [
                "Casos de elevada importância",
                "Quando muito está em jogo",
                "Situações mais exigentes",
                "Quando quer a máxima confiança",
            ]
        },
        "time_estimate": "20-30 min",
        "included": False,
    },
}


# ============================================================
# FUNÇÕES DE CÁLCULO
# ============================================================

def calculate_tier_cost(tier: TierLevel, document_tokens: int = 0) -> dict[str, float]:
    """
    Calcula o custo estimado para um tier específico.

    Args:
        tier: Nível do tier
        document_tokens: Tamanho do documento em tokens (para ajuste)

    Returns:
        Dict com custo_real, custo_cliente, bloqueio
    """
    config = TIER_CONFIG[tier]

    # Somar custos de todas as fases
    total_real_cost = sum(config["estimated_costs"].values())

    # Ajuste por tamanho do documento
    size_multiplier = 1.0
    if document_tokens > 50000:
        size_multiplier = 1.3
    elif document_tokens > 30000:
        size_multiplier = 1.15

    total_real_cost *= size_multiplier

    # Margem de lucro: 100% (custo × 2)
    custo_cliente = total_real_cost * 2

    # NOTE: This 'bloqueio' already includes the 50% safety margin.
    # Do NOT pass this to wallet_manager.block_credits() which applies its own SAFETY_MARGIN.
    # Pass 'custo_cliente' instead.
    bloqueio = custo_cliente * 1.50

    return {
        "custo_real": round(total_real_cost, 4),
        "custo_cliente": round(custo_cliente, 4),
        "bloqueio": round(bloqueio, 4),
        "size_multiplier": size_multiplier,
    }


def get_tier_models(tier: TierLevel) -> dict[str, str]:
    """Retorna os modelos associados a um tier."""
    return TIER_CONFIG[tier]["models"].copy()


def _get_models_per_phase(tier_level: TierLevel) -> dict[str, Any]:
    """Retorna modelos detalhados por fase para um tier."""
    try:
        from src.config import LLM_CONFIGS, AUDITOR_MODELS, RELATOR_MODELS
    except ImportError:
        return {}

    config = TIER_CONFIG[tier_level]
    president_key = config["models"].get("president", "gpt-5.2")
    president_model = get_openrouter_model(president_key)

    return {
        "extractors": [
            {"id": cfg["id"], "model": cfg["model"], "role": cfg["role"]}
            for cfg in LLM_CONFIGS
        ],
        "auditors": [
            {"id": f"A{i+1}", "model": m}
            for i, m in enumerate(AUDITOR_MODELS)
        ],
        "judges": [
            {"id": f"J{i+1}", "model": m}
            for i, m in enumerate(RELATOR_MODELS)
        ],
        "president": president_model,
        "a5_opus": config["models"].get("audit_a5_opus", False),
    }


def get_all_tiers_info() -> list[dict[str, Any]]:
    """Retorna informação de todos os tiers para o frontend."""
    result = []
    for tier_level in [TierLevel.BRONZE, TierLevel.SILVER, TierLevel.GOLD]:
        config = TIER_CONFIG[tier_level]
        costs = calculate_tier_cost(tier_level)

        result.append({
            "tier": tier_level.value,
            "label": config["label"],
            "icon": config["icon"],
            "description": config["description"],
            "color_gradient": config["color_gradient"],
            "color_border": config["color_border"],
            "color_bg": config["color_bg"],
            "badge": config.get("badge"),
            "features": config["features"],
            "ideal_cases": config["ideal_cases"],
            "time_estimate": config["time_estimate"],
            "included": config["included"],
            "custo_real": costs["custo_real"],
            "custo_cliente": costs["custo_cliente"],
            "bloqueio": costs["bloqueio"],
            "models_per_phase": _get_models_per_phase(tier_level),
        })

    return result


def validate_tier_selection(selection: dict[str, str]) -> bool:
    """
    Valida que a seleção de tiers por fase é válida.
    """
    required_phases = ["extraction", "audit", "judgment", "president"]
    for phase in required_phases:
        if phase not in selection:
            return False
        tier = selection[phase]
        if tier not in [t.value for t in TierLevel]:
            return False
    return True


def calculate_custom_selection_cost(
    selection: dict[str, str],
    document_tokens: int = 0
) -> dict[str, float]:
    """
    Calcula o custo para uma seleção personalizada de tiers por fase.
    """
    if not validate_tier_selection(selection):
        raise ValueError("Seleção de tiers inválida")

    tiers_selected = [TierLevel(t) for t in selection.values()]
    tier_order = {TierLevel.BRONZE: 0, TierLevel.SILVER: 1, TierLevel.GOLD: 2}
    max_tier = max(tiers_selected, key=lambda t: tier_order[t])

    return calculate_tier_cost(max_tier, document_tokens)


# ============================================================
# MAPEAMENTO DE MODELOS PARA OPENROUTER
# ============================================================

OPENROUTER_MODEL_MAPPING = {
    # Anthropic
    "sonnet-4.6": "anthropic/claude-sonnet-4.6",
    "claude-opus-4": "anthropic/claude-opus-4.6",
    "haiku-4.5": "anthropic/claude-haiku-4.5",
    "haiku-3.5": "anthropic/claude-3-5-haiku",
    # OpenAI
    "gpt-5.2": "openai/gpt-5.2",
    "gpt-5.2-pro": "openai/gpt-5.2-pro",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-4.1": "openai/gpt-4.1",
    "o1-pro": "openai/o1-pro",
    "deepseek-reasoner": "deepseek/deepseek-r1",
    # Google
    "gemini-3-pro-preview": "google/gemini-3-pro-preview",
    "gemini-3-flash-preview": "google/gemini-3-flash-preview",
    # DeepSeek
    "deepseek": "deepseek/deepseek-chat",
    "deepseek-r1": "deepseek/deepseek-r1",
    # Meta
    "llama-3.1-405b": "meta-llama/llama-3.1-405b-instruct",
    "llama-3.1-8b": "meta-llama/llama-3.1-8b-instruct",
    "llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct",      # E5
    # OpenAI (extração)
    "gpt-5-nano": "openai/gpt-5-nano",                          # v5.1: E6 visual
    "gpt-5-mini": "openai/gpt-5-mini",                          # v5.1: Suplente universal
    # Amazon (DEPRECATED)
    "nova-pro": "amazon/nova-pro-v1",                            # DEPRECATED: E6 visual
    # NVIDIA
    "nemotron-70b": "nvidia/llama-3.1-nemotron-70b-instruct",  # E7
    # Mistral / Qwen
    "mistral-medium-3": "mistralai/mistral-medium-3",
    "qwen-vl-72b": "qwen/qwen2.5-vl-72b-instruct",
    "qwen3-max-thinking": "qwen/qwen3-max-thinking",    # v5.0: A4 Advogado do Diabo
    # v5.0: Substitutos de auditores
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    "grok-4": "x-ai/grok-4",
}


def get_openrouter_model(model_key: str) -> str:
    """Converte chave de modelo para ID do OpenRouter."""
    return OPENROUTER_MODEL_MAPPING.get(model_key, model_key)


if __name__ == "__main__":
    print("=== TIER CONFIGURATION ===\n")

    for tier in [TierLevel.BRONZE, TierLevel.SILVER, TierLevel.GOLD]:
        config = TIER_CONFIG[tier]
        costs = calculate_tier_cost(tier, document_tokens=30000)

        print(f"{config['icon']} {config['label'].upper()}")
        print(f"  Custo Real: ${costs['custo_real']:.2f}")
        print(f"  Custo Cliente: ${costs['custo_cliente']:.2f}")
        print(f"  Bloqueio (50%): ${costs['bloqueio']:.2f}")
        print(f"  Size Multiplier: {costs['size_multiplier']}")
        print()

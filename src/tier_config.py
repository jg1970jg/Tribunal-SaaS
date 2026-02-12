# -*- coding: utf-8 -*-
"""
TIER CONFIGURATION - Sistema Bronze/Prata/Ouro
==================================================
Define os tiers disponíveis, modelos associados e preços.
Margem de lucro: 100%
Margem de segurança: 25%
"""

from typing import Dict, List, Any
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
    # Extratores (Fase 1)
    "gemini-3-flash-preview": 0.08,
    "gpt-4o": 0.09,
    "sonnet-3.5": 0.08,
    "sonnet-4.5": 0.08,
    "deepseek": 0.06,
    "claude-opus-4": 0.21,  # Com cache
    
    # Auditores (Fase 2)
    "gpt-5.2": 0.12,
    "gemini-3-pro-preview": 0.15,
    "grok-4.1": 0.13,
    "claude-sonnet-4.5": 0.10,  # Com cache
    "claude-opus-4-audit": 0.40,  # Com cache
    "gpt-5.2-pro": 0.60,
    
    # Juízes (Fase 3)
    "gpt-5.2-judge": 0.10,
    "gemini-3-pro-judge": 0.14,
    "claude-sonnet-4.5-judge": 0.10,  # Com cache
    "claude-opus-4-judge": 0.38,  # Com cache
    
    # Presidente (Fase 4)
    "gpt-5.2-presidente": 0.30,
    "gpt-5.2-pro-presidente": 0.60,
}


# ============================================================
# CONFIGURAÇÃO DE TIERS
# ============================================================

TIER_CONFIG = {
    TierLevel.BRONZE: {
        "label": "Standard",
        "icon": "🥉",
        "description": "Análise automática básica",
        "color_gradient": "from-amber-600 to-amber-800",
        "color_border": "border-amber-500",
        "color_bg": "bg-gradient-to-br from-amber-50 to-orange-50",
        
        # Modelos por fase
        "models": {
            "extraction": "sonnet-4.5",  # E1
            "audit_chief": "gpt-5.2",
            "audit_claude": "sonnet-4.5",  # A2
            "judgment_claude": "sonnet-4.5",  # J2
            "president": "gpt-5.2",
        },
        
        # Custos estimados (por fase)
        "estimated_costs": {
            "extraction": 0.51,  # 5 extratores (E1=0.08, resto=0.43)
            "audit": 0.90,  # 4 auditores (A2=0.10, resto=0.80)
            "judgment": 0.96,  # 3 juízes (J2=0.10, resto=0.86)
            "president": 0.30,
        },
        
        # Features
        "features": [
            "Análise automática básica",
            "Identifica problemas óbvios",
            "Relatório estruturado",
        ],
        
        # Casos ideais
        "ideal_cases": {
            "title": "Ideal para: Documentos simples",
            "cases": [
                "Contratos standard",
                "Petições básicas (< 20 páginas)",
                "Casos sem complexidade técnica",
            ]
        },
        
        "time_estimate": "2-3 min",
        "included": True,  # Tier base incluído
    },
    
    TierLevel.SILVER: {
        "label": "Premium",
        "icon": "🥈",
        "description": "Análise profunda com IA avançada",
        "color_gradient": "from-slate-400 to-slate-700",
        "color_border": "border-slate-400",
        "color_bg": "bg-gradient-to-br from-slate-50 to-gray-100",
        "badge": "⭐ RECOMENDADO",
        
        # Modelos por fase (Opus nas fases críticas)
        "models": {
            "extraction": "claude-opus-4",  # UPGRADE E1
            "audit_chief": "gpt-5.2",
            "audit_claude": "claude-opus-4",  # UPGRADE A2
            "judgment_claude": "claude-opus-4",  # UPGRADE J2
            "president": "gpt-5.2",
        },
        
        # Custos estimados
        "estimated_costs": {
            "extraction": 0.64,  # E1 upgrade: +0.13 (0.21 vs 0.08)
            "audit": 1.22,  # A2 upgrade: +0.30 (0.40 vs 0.10)
            "judgment": 1.24,  # J2 upgrade: +0.28 (0.38 vs 0.10)
            "president": 0.30,
        },
        
        "features": [
            "Análise profunda com IA avançada (Claude Opus 4)",
            "Encontra problemas que Standard não vê",
            "Relatório focado e assertivo",
            "Menor taxa de 'falsos positivos'",
        ],
        
        "ideal_cases": {
            "title": "Ideal para: Documentos complexos",
            "cases": [
                "Acórdãos de tribunais superiores",
                "Processos com 20-50 páginas",
                "Casos com múltiplas partes envolvidas",
            ]
        },
        
        "time_estimate": "3-4 min",
        "included": False,
    },
    
    TierLevel.GOLD: {
        "label": "Elite",
        "icon": "🥇",
        "description": "IA de última geração",
        "color_gradient": "from-yellow-400 to-yellow-700",
        "color_border": "border-yellow-400",
        "color_bg": "bg-gradient-to-br from-yellow-50 to-amber-100",
        "badge": "💎 MÁXIMA QUALIDADE",
        
        # Modelos por fase (Tudo premium)
        "models": {
            "extraction": "claude-opus-4",
            "audit_chief": "gpt-5.2-pro",  # UPGRADE
            "audit_claude": "claude-opus-4",
            "judgment_claude": "claude-opus-4",
            "president": "gpt-5.2-pro",  # UPGRADE
        },
        
        # Custos estimados
        "estimated_costs": {
            "extraction": 0.64,  # Igual a Silver
            "audit": 1.42,  # Chief upgrade: +0.20 (0.60 vs 0.40 no resto)
            "judgment": 1.24,  # Igual a Silver
            "president": 0.60,  # Upgrade: +0.30
        },
        
        "features": [
            "IA de última geração (GPT-5.2-PRO + Claude Opus 4)",
            "Análise ao nível de advogado sénior",
            "Deteta nuances jurídicas subtis",
            "Relatório completo e fundamentado",
            "Confiança máxima (95%+)",
        ],
        
        "ideal_cases": {
            "title": "Ideal para: Casos críticos e litígio",
            "cases": [
                "Processos > 50 páginas",
                "Recursos para STJ/Tribunal Constitucional",
                "Casos com valores elevados em disputa",
            ]
        },
        
        "time_estimate": "4-5 min",
        "included": False,
    },
}


# ============================================================
# FUNÇÕES DE CÁLCULO
# ============================================================

def calculate_tier_cost(tier: TierLevel, document_tokens: int = 0) -> Dict[str, float]:
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
        size_multiplier = 1.3  # +30% para documentos muito grandes
    elif document_tokens > 30000:
        size_multiplier = 1.15  # +15% para documentos grandes
    
    total_real_cost *= size_multiplier
    
    # Margem de lucro: 100% (custo × 2)
    custo_cliente = total_real_cost * 2
    
    # Margem de segurança: +25% para bloqueio
    bloqueio = custo_cliente * 1.25
    
    return {
        "custo_real": round(total_real_cost, 4),
        "custo_cliente": round(custo_cliente, 4),
        "bloqueio": round(bloqueio, 4),
        "size_multiplier": size_multiplier,
    }


def get_tier_models(tier: TierLevel) -> Dict[str, str]:
    """Retorna os modelos associados a um tier."""
    return TIER_CONFIG[tier]["models"].copy()


def get_all_tiers_info() -> List[Dict[str, Any]]:
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
        })
    
    return result


def validate_tier_selection(selection: Dict[str, str]) -> bool:
    """
    Valida que a seleção de tiers por fase é válida.
    
    Args:
        selection: Dict com {fase: tier_level}
        
    Returns:
        True se válido, False caso contrário
    """
    required_phases = ["extraction", "audit", "judgment", "decision"]
    
    for phase in required_phases:
        if phase not in selection:
            return False
        
        tier = selection[phase]
        if tier not in [t.value for t in TierLevel]:
            return False
    
    return True


def calculate_custom_selection_cost(
    selection: Dict[str, str],
    document_tokens: int = 0
) -> Dict[str, float]:
    """
    Calcula o custo para uma seleção personalizada de tiers por fase.
    
    Args:
        selection: Dict com {extraction: tier, audit: tier, judgment: tier, decision: tier}
        document_tokens: Tamanho do documento
    
    Returns:
        Dict com custo_real, custo_cliente, bloqueio
    """
    if not validate_tier_selection(selection):
        raise ValueError("Seleção de tiers inválida")
    
    # Para simplificar, vamos calcular baseado no tier mais alto selecionado
    # (na prática poderíamos fazer um cálculo mais granular)
    tiers_selected = [TierLevel(t) for t in selection.values()]
    
    # Ordenar por custo (Bronze < Silver < Gold)
    tier_order = {TierLevel.BRONZE: 0, TierLevel.SILVER: 1, TierLevel.GOLD: 2}
    max_tier = max(tiers_selected, key=lambda t: tier_order[t])
    
    return calculate_tier_cost(max_tier, document_tokens)


# ============================================================
# MAPEAMENTO DE MODELOS PARA OPENROUTER
# ============================================================

OPENROUTER_MODEL_MAPPING = {
    # Extratores
    "sonnet-4.5": "anthropic/claude-sonnet-4-5-20250929",
    "sonnet-3.5": "anthropic/claude-3.5-sonnet",
    "claude-opus-4": "anthropic/claude-opus-4-5-20251101",
    "gemini-3-flash-preview": "google/gemini-3-flash-preview",
    "gpt-4o": "openai/gpt-4o",
    "deepseek": "deepseek/deepseek-chat",
    
    # Auditores e Juízes
    "gpt-5.2": "openai/gpt-5.2",
    "gpt-5.2-pro": "openai/gpt-5.2-pro",
    "claude-sonnet-4.5": "anthropic/claude-sonnet-4-5-20250929",
    "claude-opus-4-audit": "anthropic/claude-opus-4-5-20251101",
    "claude-opus-4-judge": "anthropic/claude-opus-4-5-20251101",
    "gemini-3-pro-preview": "google/gemini-3-pro-preview",
    "grok-4.1": "x-ai/grok-4.1",
    
    # Presidente
    "gpt-5.2-presidente": "openai/gpt-5.2",
    "gpt-5.2-pro-presidente": "openai/gpt-5.2-pro",
}


def get_openrouter_model(model_key: str) -> str:
    """Converte chave de modelo para ID do OpenRouter."""
    return OPENROUTER_MODEL_MAPPING.get(model_key, model_key)


if __name__ == "__main__":
    # Testes
    print("=== TIER CONFIGURATION ===\n")
    
    for tier in [TierLevel.BRONZE, TierLevel.SILVER, TierLevel.GOLD]:
        config = TIER_CONFIG[tier]
        costs = calculate_tier_cost(tier, document_tokens=30000)
        
        print(f"{config['icon']} {config['label'].upper()}")
        print(f"  Custo Real: ${costs['custo_real']:.2f}")
        print(f"  Custo Cliente: ${costs['custo_cliente']:.2f}")
        print(f"  Bloqueio (25%): ${costs['bloqueio']:.2f}")
        print(f"  Size Multiplier: {costs['size_multiplier']}")
        print()

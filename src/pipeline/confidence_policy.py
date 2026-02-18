# -*- coding: utf-8 -*-
"""
Policy Determinística de Confiança para o Pipeline do Tribunal.

Define regras claras e determinísticas para calcular penalidades de
confiança baseadas em erros de integridade, cobertura e parsing.

REGRAS:
1. Cada tipo de erro tem penalidade fixa definida
2. Penalidades são cumulativas até teto máximo
3. Erros severos impõem teto máximo na confidence
4. Tudo é configurável mas com defaults seguros

MUDANÇAS (v2.1):
- NOVO: OFFSET_IMPRECISE (penalty mínima — excerpt existe perto)
- NOVO: OFFSET_WRONG (penalty baixa — excerpt existe mas offset errado)
- AJUSTADO: EXCERPT_MISMATCH agora mais grave (só para invenções reais)
- AJUSTADO: Penalties gerais menos agressivas (evitar falsos INCONCLUSIVOs)
- AJUSTADO: global_max_penalty 0.50→0.40, severe_ceiling 0.75→0.80
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================================================
# TIPOS DE ERROS E PENALIDADES
# ============================================================================

class ErrorCategory(str, Enum):
    """Categorias de erros que afetam confiança."""
    INTEGRITY = "integrity"       # Erros de integridade (citations, excerpts)
    COVERAGE = "coverage"         # Erros de cobertura (páginas, chars)
    PARSING = "parsing"           # Erros de parsing (ERROR_RECOVERED)
    CONSISTENCY = "consistency"   # Erros de consistência (doc_ids, timestamps)
    REFERENCE = "reference"       # Erros de referência (item_ids inexistentes)


@dataclass
class PenaltyRule:
    """Regra de penalidade para um tipo de erro."""
    error_type: str
    category: ErrorCategory
    penalty_per_occurrence: float  # 0.0-1.0 penalidade por ocorrência
    max_penalty: float             # Penalidade máxima cumulativa deste tipo
    severity_ceiling: Optional[float] = None  # Se definido, impõe teto na confidence final
    description: str = ""


# Regras padrão de penalidade
DEFAULT_PENALTY_RULES: Dict[str, PenaltyRule] = {

    # ── Erros de Integridade ──

    "RANGE_INVALID": PenaltyRule(
        error_type="RANGE_INVALID",
        category=ErrorCategory.INTEGRITY,
        penalty_per_occurrence=0.03,        # AJUSTADO: era 0.05
        max_penalty=0.15,                   # AJUSTADO: era 0.20
        severity_ceiling=0.80,              # AJUSTADO: era 0.75
        description="Start/end char inválidos"
    ),

    "PAGE_MISMATCH": PenaltyRule(
        error_type="PAGE_MISMATCH",
        category=ErrorCategory.INTEGRITY,
        penalty_per_occurrence=0.01,        # AJUSTADO: era 0.02
        max_penalty=0.08,                   # AJUSTADO: era 0.15
        description="Página não corresponde ao offset"
    ),

    # NOVO: Excerpt encontrado perto do range (±100-200 chars)
    # Isto é o caso mais comum — o modelo acertou o texto mas errou o offset ligeiramente
    "OFFSET_IMPRECISE": PenaltyRule(
        error_type="OFFSET_IMPRECISE",
        category=ErrorCategory.INTEGRITY,
        penalty_per_occurrence=0.005,       # NOVO: quase zero
        max_penalty=0.03,
        description="Excerpt encontrado perto do range especificado (±200 chars)"
    ),

    # NOVO: Excerpt existe no documento mas offset completamente errado
    # O modelo encontrou texto real mas as coordenadas estão erradas
    "OFFSET_WRONG": PenaltyRule(
        error_type="OFFSET_WRONG",
        category=ErrorCategory.INTEGRITY,
        penalty_per_occurrence=0.01,        # NOVO: muito menos que EXCERPT_MISMATCH
        max_penalty=0.08,
        description="Excerpt existe no documento mas offset errado"
    ),

    # AJUSTADO: EXCERPT_MISMATCH agora é só para invenções reais
    # (quando o texto NÃO existe em lado nenhum do documento)
    "EXCERPT_MISMATCH": PenaltyRule(
        error_type="EXCERPT_MISMATCH",
        category=ErrorCategory.INTEGRITY,
        penalty_per_occurrence=0.05,        # AJUSTADO: era 0.03, agora mais grave
        max_penalty=0.25,                   # AJUSTADO: era 0.20
        severity_ceiling=0.65,              # NOVO: ceiling para invenções confirmadas
        description="Excerpt NÃO encontrado no documento (possível invenção)"
    ),

    "MISSING_CITATION": PenaltyRule(
        error_type="MISSING_CITATION",
        category=ErrorCategory.INTEGRITY,
        penalty_per_occurrence=0.01,        # AJUSTADO: era 0.02
        max_penalty=0.08,                   # AJUSTADO: era 0.15
        description="Finding/Point sem citações"
    ),

    "SEM_PROVA_DETERMINANTE": PenaltyRule(
        error_type="SEM_PROVA_DETERMINANTE",
        category=ErrorCategory.INTEGRITY,
        penalty_per_occurrence=0.10,        # AJUSTADO: era 0.15
        max_penalty=0.25,                   # AJUSTADO: era 0.30
        severity_ceiling=0.65,              # AJUSTADO: era 0.60
        description="Ponto DETERMINANTE sem prova documental (citations)"
    ),

    # ── Erros de Cobertura ──

    "PAGES_MISSING": PenaltyRule(
        error_type="PAGES_MISSING",
        category=ErrorCategory.COVERAGE,
        penalty_per_occurrence=0.02,        # Por página
        max_penalty=0.20,
        description="Páginas não processadas"
    ),

    "PAGES_UNREADABLE": PenaltyRule(
        error_type="PAGES_UNREADABLE",
        category=ErrorCategory.COVERAGE,
        penalty_per_occurrence=0.01,        # Por página ilegível
        max_penalty=0.15,
        description="Páginas ilegíveis"
    ),

    "COVERAGE_LOW": PenaltyRule(
        error_type="COVERAGE_LOW",
        category=ErrorCategory.COVERAGE,
        penalty_per_occurrence=0.10,        # Uma vez se cobertura < 95%
        max_penalty=0.15,
        description="Cobertura de caracteres baixa (<95%)"
    ),

    "COVERAGE_GAPS": PenaltyRule(
        error_type="COVERAGE_GAPS",
        category=ErrorCategory.COVERAGE,
        penalty_per_occurrence=0.01,        # Por gap > 100 chars
        max_penalty=0.10,
        description="Gaps não cobertos no documento"
    ),

    # ── Erros de Parsing ──

    "ERROR_RECOVERED": PenaltyRule(
        error_type="ERROR_RECOVERED",
        category=ErrorCategory.PARSING,
        penalty_per_occurrence=0.08,
        max_penalty=0.25,
        severity_ceiling=0.70,              # Parsing falhou é grave
        description="JSON inválido, relatório mínimo criado"
    ),

    "PARSE_WARNING": PenaltyRule(
        error_type="PARSE_WARNING",
        category=ErrorCategory.PARSING,
        penalty_per_occurrence=0.02,
        max_penalty=0.10,
        description="Warning durante parsing"
    ),

    # ── Erros de Consistência ──

    "DOC_ID_INVALID": PenaltyRule(
        error_type="DOC_ID_INVALID",
        category=ErrorCategory.CONSISTENCY,
        penalty_per_occurrence=0.05,
        max_penalty=0.20,
        severity_ceiling=0.75,
        description="doc_id referenciado não existe"
    ),

    "TIMESTAMP_INVALID": PenaltyRule(
        error_type="TIMESTAMP_INVALID",
        category=ErrorCategory.CONSISTENCY,
        penalty_per_occurrence=0.01,
        max_penalty=0.05,
        description="Timestamp fora da janela do run"
    ),

    "COUNT_MISMATCH": PenaltyRule(
        error_type="COUNT_MISMATCH",
        category=ErrorCategory.CONSISTENCY,
        penalty_per_occurrence=0.02,
        max_penalty=0.10,
        description="Contagem não bate (citations, items, etc)"
    ),

    # ── Erros de Referência ──

    "ITEM_NOT_FOUND": PenaltyRule(
        error_type="ITEM_NOT_FOUND",
        category=ErrorCategory.REFERENCE,
        penalty_per_occurrence=0.01,
        max_penalty=0.10,
        description="evidence_item_id não encontrado"
    ),

    "FINDING_NOT_FOUND": PenaltyRule(
        error_type="FINDING_NOT_FOUND",
        category=ErrorCategory.REFERENCE,
        penalty_per_occurrence=0.02,
        max_penalty=0.10,
        description="finding_ref não encontrado"
    ),
}


# ============================================================================
# RESULTADO DE PENALTY
# ============================================================================

@dataclass
class PenaltyBreakdown:
    """Breakdown detalhado de penalidades por categoria."""
    category: ErrorCategory
    total_penalty: float
    occurrences: int
    capped_at: float
    error_types: Dict[str, int] = field(default_factory=dict)


@dataclass
class ConfidencePenaltyResult:
    """
    Resultado do cálculo de penalidade de confiança.
    """
    # Penalidade total (soma de todas as categorias)
    total_penalty: float = 0.0

    # Teto de confiança imposto (se houver erro severo)
    confidence_ceiling: Optional[float] = None

    # Confiança ajustada (original - penalty, respeitando ceiling)
    adjusted_confidence: float = 1.0

    # Breakdown por categoria
    by_category: Dict[str, PenaltyBreakdown] = field(default_factory=dict)

    # Lista de erros que causaram penalidade
    penalties_applied: List[Dict] = field(default_factory=list)

    # Resumo
    is_severely_penalized: bool = False
    dominant_category: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "total_penalty": round(self.total_penalty, 4),
            "confidence_ceiling": self.confidence_ceiling,
            "adjusted_confidence": round(self.adjusted_confidence, 4),
            "is_severely_penalized": self.is_severely_penalized,
            "dominant_category": self.dominant_category,
            "by_category": {
                cat: {
                    "penalty": round(breakdown.total_penalty, 4),
                    "occurrences": breakdown.occurrences,
                    "capped_at": breakdown.capped_at,
                    "error_types": breakdown.error_types,
                }
                for cat, breakdown in self.by_category.items()
            },
            "penalties_count": len(self.penalties_applied),
        }


# ============================================================================
# CALCULADOR DE PENALIDADES
# ============================================================================

class ConfidencePolicyCalculator:
    """
    Calculador de penalidades de confiança.

    Usa regras determinísticas para calcular penalidades baseadas em
    erros detectados pelo IntegrityValidator, MetaIntegrity e parsing.
    """

    def __init__(
        self,
        rules: Optional[Dict[str, PenaltyRule]] = None,
        global_max_penalty: float = 0.40,  # AJUSTADO: era 0.50
        severe_ceiling: float = 0.80,      # AJUSTADO: era 0.75
    ):
        """
        Args:
            rules: Regras de penalidade (default se None)
            global_max_penalty: Penalidade máxima acumulada
            severe_ceiling: Teto de confiança para erros severos
        """
        self.rules = rules or DEFAULT_PENALTY_RULES.copy()
        self.global_max_penalty = global_max_penalty
        self.severe_ceiling = severe_ceiling

    def compute_penalty(
        self,
        integrity_report: Optional[Any] = None,
        coverage_report: Optional[Dict] = None,
        errors_list: Optional[List[str]] = None,
        original_confidence: float = 1.0
    ) -> ConfidencePenaltyResult:
        """
        Calcula penalidade total baseada em múltiplas fontes.

        Args:
            integrity_report: IntegrityReport do validator
            coverage_report: Dict do coverage_report.json
            errors_list: Lista de strings de erro (ex: report.errors)
            original_confidence: Confiança original a ajustar

        Returns:
            ConfidencePenaltyResult com breakdown completo
        """
        # Contadores por tipo de erro
        error_counts: Dict[str, int] = {}

        # 1. Processar IntegrityReport
        if integrity_report is not None:
            self._count_integrity_errors(integrity_report, error_counts)

        # 2. Processar Coverage Report
        if coverage_report is not None:
            self._count_coverage_errors(coverage_report, error_counts)

        # 3. Processar lista de erros (strings)
        if errors_list:
            self._count_string_errors(errors_list, error_counts)

        # 4. Calcular penalidades
        return self._calculate_penalties(error_counts, original_confidence)

    def _count_integrity_errors(self, report: Any, counts: Dict[str, int]):
        """Conta erros de um IntegrityReport."""
        if isinstance(report, dict):
            top_errors = report.get("top_errors", [])
            if top_errors:
                # Use top_errors as primary source (avoid double counting)
                for err in top_errors:
                    error_type = err.get("error_type", "UNKNOWN")
                    counts[error_type] = counts.get(error_type, 0) + 1
            else:
                # Fallback to individual field counts only if no top_errors
                citations = report.get("citations", {})
                if citations.get("invalid", 0) > 0:
                    counts["RANGE_INVALID"] = counts.get("RANGE_INVALID", 0) + citations["invalid"]
                excerpts = report.get("excerpts", {})
                if excerpts.get("mismatch", 0) > 0:
                    counts["EXCERPT_MISMATCH"] = counts.get("EXCERPT_MISMATCH", 0) + excerpts["mismatch"]
                pages = report.get("pages", {})
                if pages.get("mismatch", 0) > 0:
                    counts["PAGE_MISMATCH"] = counts.get("PAGE_MISMATCH", 0) + pages["mismatch"]
        elif hasattr(report, 'errors'):
            for err in report.errors:
                if hasattr(err, 'error_type'):
                    error_type = err.error_type
                elif isinstance(err, dict):
                    error_type = err.get("error_type", "UNKNOWN")
                else:
                    continue
                counts[error_type] = counts.get(error_type, 0) + 1

    def _count_coverage_errors(self, coverage: Dict, counts: Dict[str, int]):
        """Conta erros de um coverage_report.json."""
        # Cobertura baixa
        coverage_percent = coverage.get("coverage_percent", 100)
        if coverage_percent < 95.0:
            counts["COVERAGE_LOW"] = counts.get("COVERAGE_LOW", 0) + 1

        # Gaps
        gaps = coverage.get("gaps", [])
        significant_gaps = [g for g in gaps if g.get("length", 0) >= 100]
        if significant_gaps:
            counts["COVERAGE_GAPS"] = counts.get("COVERAGE_GAPS", 0) + len(significant_gaps)

        # Páginas faltando
        pages_missing = coverage.get("pages_missing", 0)
        if pages_missing > 0:
            counts["PAGES_MISSING"] = counts.get("PAGES_MISSING", 0) + pages_missing

        # Páginas ilegíveis
        pages_unreadable = coverage.get("pages_unreadable", 0)
        if pages_unreadable > 0:
            counts["PAGES_UNREADABLE"] = counts.get("PAGES_UNREADABLE", 0) + pages_unreadable

    def _count_string_errors(self, errors: List[str], counts: Dict[str, int]):
        """Conta erros de uma lista de strings."""
        for error in errors:
            if not error:
                continue

            error_upper = error.upper()
            error_lower = error.lower()

            # Primeiro: verificar se o erro começa com um tipo conhecido nas rules
            matched_rule = False
            for rule_key in self.rules.keys():
                if error_upper.startswith(rule_key):
                    counts[rule_key] = counts.get(rule_key, 0) + 1
                    matched_rule = True
                    break

            if matched_rule:
                continue

            # Fallback: detectar tipo de erro pela string (padrões legados)
            if "error_recovered" in error_lower:
                counts["ERROR_RECOVERED"] = counts.get("ERROR_RECOVERED", 0) + 1
            elif "offset_imprecise" in error_lower:
                counts["OFFSET_IMPRECISE"] = counts.get("OFFSET_IMPRECISE", 0) + 1
            elif "offset_wrong" in error_lower:
                counts["OFFSET_WRONG"] = counts.get("OFFSET_WRONG", 0) + 1
            elif "integrity_warning" in error_lower:
                # Extrair tipo específico se possível
                if "page_mismatch" in error_lower:
                    counts["PAGE_MISMATCH"] = counts.get("PAGE_MISMATCH", 0) + 1
                elif "excerpt_mismatch" in error_lower:
                    counts["EXCERPT_MISMATCH"] = counts.get("EXCERPT_MISMATCH", 0) + 1
                elif "range_invalid" in error_lower:
                    counts["RANGE_INVALID"] = counts.get("RANGE_INVALID", 0) + 1
                elif "offset_imprecise" in error_lower:
                    counts["OFFSET_IMPRECISE"] = counts.get("OFFSET_IMPRECISE", 0) + 1
                elif "offset_wrong" in error_lower:
                    counts["OFFSET_WRONG"] = counts.get("OFFSET_WRONG", 0) + 1
                elif "item_not_found" in error_lower:
                    counts["ITEM_NOT_FOUND"] = counts.get("ITEM_NOT_FOUND", 0) + 1
                else:
                    counts["PARSE_WARNING"] = counts.get("PARSE_WARNING", 0) + 1
            elif "warning" in error_lower:
                counts["PARSE_WARNING"] = counts.get("PARSE_WARNING", 0) + 1

    def _calculate_penalties(
        self,
        error_counts: Dict[str, int],
        original_confidence: float
    ) -> ConfidencePenaltyResult:
        """Calcula penalidades finais."""
        result = ConfidencePenaltyResult()
        result.by_category = {}

        total_penalty = 0.0
        ceiling = None
        penalties_applied = []

        # Agrupar por categoria
        category_penalties: Dict[ErrorCategory, List[Tuple[str, int, float]]] = {}

        for error_type, count in error_counts.items():
            if error_type not in self.rules:
                logger.debug(f"Tipo de erro desconhecido (ignorado): {error_type}")
                continue

            rule = self.rules[error_type]

            # Calcular penalidade para este tipo
            raw_penalty = count * rule.penalty_per_occurrence
            capped_penalty = min(raw_penalty, rule.max_penalty)

            if capped_penalty > 0:
                penalties_applied.append({
                    "error_type": error_type,
                    "occurrences": count,
                    "penalty_per": rule.penalty_per_occurrence,
                    "raw_penalty": raw_penalty,
                    "capped_penalty": capped_penalty,
                    "category": rule.category.value,
                })

                # Acumular por categoria
                if rule.category not in category_penalties:
                    category_penalties[rule.category] = []
                category_penalties[rule.category].append((error_type, count, capped_penalty))

                total_penalty += capped_penalty

                # Verificar ceiling
                if rule.severity_ceiling is not None:
                    if ceiling is None:
                        ceiling = rule.severity_ceiling
                    else:
                        ceiling = min(ceiling, rule.severity_ceiling)

        # Aplicar cap global
        total_penalty = min(total_penalty, self.global_max_penalty)

        # Construir breakdown por categoria
        dominant_cat = None
        max_cat_penalty = 0.0

        for cat, items in category_penalties.items():
            cat_total = sum(p[2] for p in items)
            cat_occurrences = sum(p[1] for p in items)
            cat_types = {p[0]: p[1] for p in items}

            breakdown = PenaltyBreakdown(
                category=cat,
                total_penalty=cat_total,
                occurrences=cat_occurrences,
                capped_at=self._get_category_max(cat),
                error_types=cat_types,
            )
            result.by_category[cat.value] = breakdown

            if cat_total > max_cat_penalty:
                max_cat_penalty = cat_total
                dominant_cat = cat.value

        # Calcular confiança ajustada
        adjusted = original_confidence - total_penalty
        if ceiling is not None:
            adjusted = min(adjusted, ceiling)
        adjusted = max(0.0, min(1.0, adjusted))

        # Preencher resultado
        result.total_penalty = total_penalty
        result.confidence_ceiling = ceiling
        result.adjusted_confidence = adjusted
        result.penalties_applied = penalties_applied
        result.is_severely_penalized = ceiling is not None or total_penalty > 0.15
        result.dominant_category = dominant_cat

        logger.debug(
            f"Penalty calculated: total={total_penalty:.3f}, "
            f"ceiling={ceiling}, adjusted={adjusted:.3f}"
        )

        return result

    def _get_category_max(self, category: ErrorCategory) -> float:
        """Retorna penalidade máxima para uma categoria."""
        total = 0.0
        for rule in self.rules.values():
            if rule.category == category:
                total += rule.max_penalty
        return min(total, self.global_max_penalty)


# ============================================================================
# FUNÇÕES DE CONVENIÊNCIA
# ============================================================================

def compute_penalty(
    integrity_report: Optional[Any] = None,
    coverage_report: Optional[Dict] = None,
    errors_list: Optional[List[str]] = None,
    original_confidence: float = 1.0
) -> ConfidencePenaltyResult:
    """
    Função de conveniência para calcular penalidade.
    Usa configuração default do ConfidencePolicyCalculator.
    """
    calculator = ConfidencePolicyCalculator()
    return calculator.compute_penalty(
        integrity_report=integrity_report,
        coverage_report=coverage_report,
        errors_list=errors_list,
        original_confidence=original_confidence,
    )


def apply_penalty_to_confidence(
    original_confidence: float,
    penalty_result: ConfidencePenaltyResult
) -> float:
    """
    Aplica penalidade a uma confiança.

    Args:
        original_confidence: Confiança original (0.0-1.0)
        penalty_result: Resultado do compute_penalty

    Returns:
        Confiança ajustada
    """
    adjusted = original_confidence - penalty_result.total_penalty

    if penalty_result.confidence_ceiling is not None:
        adjusted = min(adjusted, penalty_result.confidence_ceiling)

    return max(0.0, min(1.0, adjusted))



# -*- coding: utf-8 -*-
"""
Validador de Integridade para o Pipeline do Tribunal.

Valida automaticamente citations, offsets, page_num e excerpts
sem abortar o pipeline - apenas adiciona warnings e reduz confidence.

REGRAS:
1. Nunca abortar - sempre recuperar e reportar
2. INTEGRITY_WARNING em errors[] para problemas detectados
3. Relatório JSON completo por run

INTEGRAÇÃO:
- Usa text_normalize.py para normalização consistente
- Usa confidence_policy.py para penalidades determinísticas

CHANGELOG:
- 2026-02-11: Fix — BUSCA GLOBAL antes de EXCERPT_MISMATCH
              Hierarquia: range → ±200 (OFFSET_IMPRECISE) → global (OFFSET_WRONG) → EXCERPT_MISMATCH
- 2026-02-10: Fix MISSING_CITATION - não penalizar quando legal_basis existe
- 2026-02-10: Fix MISSING_RATIONALE - reduzir falsos positivos
- 2026-02-10: Reduzir penalties para evitar confiança artificialmente baixa
- 2026-02-10: Fix Bug #11 — EXCERPT_MISMATCH: thresholds tolerantes (0.4/0.3), janela ±200 chars, severity INFO
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any, Set

from src.config import LOG_LEVEL, OUTPUT_DIR

# Importar normalização unificada
from src.pipeline.text_normalize import (
    normalize_for_matching,
    text_contains_normalized,
    text_similarity_normalized,
    normalize_excerpt_for_debug,
    NormalizationConfig,
)

logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger(__name__)


# ============================================================================
# DATACLASSES PARA RELATÓRIO
# ============================================================================

@dataclass
class ValidationError:
    """Erro de validação individual."""
    error_type: str
    severity: str
    message: str
    doc_id: Optional[str] = None
    page_num: Optional[int] = None
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    source: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "error_type": self.error_type,
            "severity": self.severity,
            "message": self.message,
            "doc_id": self.doc_id,
            "page_num": self.page_num,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "expected": self.expected[:100] if self.expected else None,
            "actual": self.actual[:100] if self.actual else None,
            "source": self.source,
        }


@dataclass
class IntegrityReport:
    """Relatório completo de integridade de um run."""
    run_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    citations_total: int = 0
    citations_valid: int = 0
    citations_invalid: int = 0
    excerpts_checked: int = 0
    excerpts_matched: int = 0
    excerpts_mismatch: int = 0
    # NOVO: contadores separados para offset imprecise/wrong
    excerpts_offset_imprecise: int = 0
    excerpts_offset_wrong: int = 0
    spans_total: int = 0
    spans_valid: int = 0
    spans_out_of_range: int = 0
    pages_checked: int = 0
    pages_valid: int = 0
    pages_mismatch: int = 0
    items_referenced: int = 0
    items_found: int = 0
    items_not_found: int = 0
    phase2_errors: int = 0
    phase3_errors: int = 0
    phase4_errors: int = 0
    errors: List[ValidationError] = field(default_factory=list)
    is_valid: bool = True
    overall_confidence_penalty: float = 0.0

    def add_error(self, error: ValidationError):
        """Adiciona erro e atualiza contagens."""
        self.errors.append(error)
        if error.severity == "ERROR":
            self.is_valid = False
            self.overall_confidence_penalty = min(1.0, self.overall_confidence_penalty + 0.05)
        elif error.severity == "WARNING":
            self.overall_confidence_penalty = min(1.0, self.overall_confidence_penalty + 0.02)

    def to_dict(self) -> Dict:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "summary": {
                "is_valid": self.is_valid,
                "overall_confidence_penalty": round(self.overall_confidence_penalty, 3),
                "total_errors": len(self.errors),
                "errors_by_severity": {
                    "ERROR": len([e for e in self.errors if e.severity == "ERROR"]),
                    "WARNING": len([e for e in self.errors if e.severity == "WARNING"]),
                    "INFO": len([e for e in self.errors if e.severity == "INFO"]),
                },
            },
            "citations": {"total": self.citations_total, "valid": self.citations_valid, "invalid": self.citations_invalid},
            "excerpts": {
                "checked": self.excerpts_checked,
                "matched": self.excerpts_matched,
                "mismatch": self.excerpts_mismatch,
                "offset_imprecise": self.excerpts_offset_imprecise,
                "offset_wrong": self.excerpts_offset_wrong,
            },
            "spans": {"total": self.spans_total, "valid": self.spans_valid, "out_of_range": self.spans_out_of_range},
            "pages": {"checked": self.pages_checked, "valid": self.pages_valid, "mismatch": self.pages_mismatch},
            "evidence_items": {"referenced": self.items_referenced, "found": self.items_found, "not_found": self.items_not_found},
            "by_phase": {"phase2_auditors": self.phase2_errors, "phase3_judges": self.phase3_errors, "phase4_president": self.phase4_errors},
            "top_errors": [e.to_dict() for e in self.errors[:100]],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, output_dir: Optional[Path] = None) -> Path:
        """Guarda relatório em ficheiro JSON."""
        if output_dir is None:
            output_dir = OUTPUT_DIR / self.run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / "integrity_report.json"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.to_json())
        logger.info(f"Relatório de integridade guardado: {filepath}")
        return filepath


# ============================================================================
# FUNÇÕES DE NORMALIZAÇÃO (delegam para text_normalize.py)
# ============================================================================

def normalize_text_for_comparison(text: str) -> str:
    """Normaliza texto para comparação flexível."""
    return normalize_for_matching(text, NormalizationConfig.default())


def text_similarity(text1: str, text2: str) -> float:
    """Calcula similaridade entre dois textos (0.0 - 1.0)."""
    return text_similarity_normalized(text1, text2)


def text_contains(haystack: str, needle: str, threshold: float = 0.7) -> bool:
    """Verifica se haystack contém needle (com tolerância)."""
    return text_contains_normalized(haystack, needle, threshold)


# ============================================================================
# VALIDADORES PRINCIPAIS
# ============================================================================

def validate_citation(
    citation: Dict,
    document_text: str,
    total_chars: int,
    page_mapper: Optional[Any] = None,
    source: str = ""
) -> Tuple[bool, List[ValidationError]]:
    """
    Valida uma citation individual.

    Verifica:
    1. Ranges start_char/end_char válidos
    2. page_num consistente com mapper (se existir)
    3. excerpt bate com texto no documento

    HIERARQUIA DE VALIDAÇÃO DE EXCERPT (v2.1):
    a) Range exacto → OK (sem penalty)
    b) Janela ±200 chars → OFFSET_IMPRECISE (penalty mínima 0.005)
    c) BUSCA GLOBAL no documento → OFFSET_WRONG (penalty baixa 0.01)
    d) NÃO encontrado → EXCERPT_MISMATCH (penalty alta 0.05, é invenção)
    """
    errors = []
    is_valid = True

    doc_id = citation.get("doc_id", "unknown")
    start_char = citation.get("start_char", 0)
    end_char = citation.get("end_char", 0)
    page_num = citation.get("page_num")
    excerpt = citation.get("excerpt", "")

    # 1. Validar ranges
    if start_char < 0:
        errors.append(ValidationError(
            error_type="RANGE_INVALID",
            severity="ERROR",
            message=f"start_char negativo: {start_char}",
            doc_id=doc_id, start_char=start_char, end_char=end_char, source=source,
        ))
        is_valid = False

    if end_char < start_char:
        errors.append(ValidationError(
            error_type="RANGE_INVALID",
            severity="ERROR",
            message=f"end_char ({end_char}) < start_char ({start_char})",
            doc_id=doc_id, start_char=start_char, end_char=end_char, source=source,
        ))
        is_valid = False

    if end_char > total_chars:
        errors.append(ValidationError(
            error_type="RANGE_INVALID",
            severity="WARNING",
            message=f"end_char ({end_char}) > total_chars ({total_chars})",
            doc_id=doc_id, start_char=start_char, end_char=end_char, source=source,
        ))

    # 2. Validar page_num contra mapper
    if page_mapper is not None and page_num is not None:
        expected_page = page_mapper.get_page(start_char)
        if expected_page is not None and expected_page != page_num:
            errors.append(ValidationError(
                error_type="PAGE_MISMATCH",
                severity="WARNING",
                message=f"page_num ({page_num}) != esperado ({expected_page}) para offset {start_char}",
                doc_id=doc_id, page_num=page_num, start_char=start_char,
                expected=str(expected_page), actual=str(page_num), source=source,
            ))

    # 3. Validar excerpt — HIERARQUIA DE 4 NÍVEIS
    if excerpt and document_text and start_char >= 0 and end_char <= len(document_text):
        config = NormalizationConfig.ocr_tolerant()

        # ── Nível A: Range exacto ──
        actual_text = document_text[start_char:end_char]
        match_result, match_debug = text_contains_normalized(
            actual_text, excerpt, threshold=0.4, config=config, return_debug=True
        )

        if not match_result:
            # ── Nível B: Janela expandida ±200 chars → OFFSET_IMPRECISE ──
            expanded_start = max(0, start_char - 200)
            expanded_end = min(len(document_text), end_char + 200)
            expanded_text = document_text[expanded_start:expanded_end]

            expanded_match, expanded_debug = text_contains_normalized(
                expanded_text, excerpt, threshold=0.3, config=config, return_debug=True
            )

            if expanded_match:
                # Encontrou perto — offset impreciso, texto real
                errors.append(ValidationError(
                    error_type="OFFSET_IMPRECISE",
                    severity="INFO",
                    message=f"excerpt encontrado na janela ±200 chars (offset impreciso)",
                    doc_id=doc_id, page_num=page_num,
                    start_char=start_char, end_char=end_char,
                    expected=excerpt[:100], actual=actual_text[:100],
                    source=source,
                ))
            else:
                # ── Nível C: BUSCA GLOBAL no documento → OFFSET_WRONG ──
                # Limitar busca global a excerpts com tamanho razoável (evitar false positives)
                global_match = False
                if len(excerpt) >= 15:
                    global_match, global_debug = text_contains_normalized(
                        document_text, excerpt, threshold=0.3, config=config, return_debug=True
                    )

                if global_match:
                    # Texto existe no documento mas offset completamente errado
                    errors.append(ValidationError(
                        error_type="OFFSET_WRONG",
                        severity="INFO",
                        message=f"excerpt encontrado no documento (offset errado, texto real)",
                        doc_id=doc_id, page_num=page_num,
                        start_char=start_char, end_char=end_char,
                        expected=excerpt[:100], actual=actual_text[:100],
                        source=source,
                    ))
                else:
                    # ── Nível D: NÃO encontrado → EXCERPT_MISMATCH (possível invenção) ──
                    debug_info = normalize_excerpt_for_debug(excerpt, actual_text)
                    errors.append(ValidationError(
                        error_type="EXCERPT_MISMATCH",
                        severity="WARNING",
                        message=f"excerpt NÃO encontrado no documento (possível invenção, match_ratio={match_debug.get('match_ratio', 0):.2f})",
                        doc_id=doc_id, page_num=page_num,
                        start_char=start_char, end_char=end_char,
                        expected=excerpt[:100], actual=actual_text[:100],
                        source=source,
                    ))

    return is_valid, errors


def validate_audit_report(
    report: Any,
    unified_result: Optional[Any] = None,
    document_text: str = "",
    total_chars: int = 0,
    page_mapper: Optional[Any] = None
) -> Tuple[bool, List[ValidationError], float]:
    """Valida um AuditReport completo."""
    errors = []
    is_valid = True
    confidence_penalty = 0.0

    auditor_id = getattr(report, 'auditor_id', 'unknown')

    valid_item_ids: Set[str] = set()
    if unified_result is not None:
        union_items = getattr(unified_result, 'union_items', [])
        for item in union_items:
            valid_item_ids.add(getattr(item, 'item_id', ''))

    findings = getattr(report, 'findings', [])
    for finding in findings:
        finding_id = getattr(finding, 'finding_id', 'unknown')
        citations = getattr(finding, 'citations', [])

        if not citations:
            errors.append(ValidationError(
                error_type="MISSING_CITATION",
                severity="WARNING",
                message=f"Finding '{finding_id}' sem citations",
                source=auditor_id,
            ))
            confidence_penalty += 0.02

        for citation in citations:
            if hasattr(citation, 'to_dict'):
                citation_dict = citation.to_dict()
            elif isinstance(citation, dict):
                citation_dict = citation
            else:
                continue

            citation_valid, citation_errors = validate_citation(
                citation_dict, document_text, total_chars, page_mapper,
                source=auditor_id,
            )
            errors.extend(citation_errors)
            if not citation_valid:
                is_valid = False
                confidence_penalty += 0.03

        evidence_ids = getattr(finding, 'evidence_item_ids', [])
        for item_id in evidence_ids:
            if valid_item_ids and item_id not in valid_item_ids:
                # FIX 2026-02-14: Fuzzy match — LLMs frequentemente truncam ou alteram item_ids
                # Verificar se existe um item_id que começa com o mesmo prefixo (≥10 chars)
                fuzzy_match = False
                if len(item_id) >= 10:
                    prefix = item_id[:10]
                    fuzzy_match = any(vid.startswith(prefix) or prefix in vid for vid in valid_item_ids)
                if not fuzzy_match:
                    errors.append(ValidationError(
                        error_type="ITEM_NOT_FOUND",
                        severity="WARNING",
                        message=f"evidence_item_id '{item_id}' não encontrado em union_items",
                        source=auditor_id,
                    ))
                    confidence_penalty += 0.01

    return is_valid, errors, min(confidence_penalty, 0.3)


def validate_judge_opinion(
    opinion: Any,
    unified_result: Optional[Any] = None,
    document_text: str = "",
    total_chars: int = 0,
    page_mapper: Optional[Any] = None
) -> Tuple[bool, List[ValidationError], float]:
    """
    Valida um JudgeOpinion completo.
    FIX 2026-02-10: Não penalizar quando legal_basis existe.
    """
    errors = []
    is_valid = True
    confidence_penalty = 0.0

    judge_id = getattr(opinion, 'judge_id', 'unknown')
    decision_points = getattr(opinion, 'decision_points', [])

    for point in decision_points:
        point_id = getattr(point, 'point_id', 'unknown')
        citations = getattr(point, 'citations', [])

        for citation in citations:
            if hasattr(citation, 'to_dict'):
                citation_dict = citation.to_dict()
            elif isinstance(citation, dict):
                citation_dict = citation
            else:
                continue

            citation_valid, citation_errors = validate_citation(
                citation_dict, document_text, total_chars, page_mapper,
                source=judge_id,
            )
            errors.extend(citation_errors)
            if not citation_valid:
                is_valid = False
                confidence_penalty += 0.03

        is_determinant = getattr(point, 'is_determinant', False)
        if is_determinant and not citations:
            legal_basis = getattr(point, 'legal_basis', [])
            if legal_basis:
                errors.append(ValidationError(
                    error_type="SEM_PROVA_DETERMINANTE",
                    severity="WARNING",
                    message=f"Ponto DETERMINANTE '{point_id}' sem citations mas com legal_basis: {', '.join(legal_basis[:3])}",
                    source=judge_id,
                    expected="Citations com offsets",
                    actual=f"legal_basis: {', '.join(legal_basis[:3])}",
                ))
                confidence_penalty += 0.05
            else:
                errors.append(ValidationError(
                    error_type="SEM_PROVA_DETERMINANTE",
                    severity="ERROR",
                    message=f"Ponto DETERMINANTE '{point_id}' sem citations nem legal_basis",
                    source=judge_id,
                    expected="Pelo menos 1 citation ou legal_basis",
                    actual="0 citations, 0 legal_basis",
                ))
                is_valid = False
                confidence_penalty += 0.15
        elif not citations:
            legal_basis = getattr(point, 'legal_basis', [])
            if not legal_basis:
                errors.append(ValidationError(
                    error_type="MISSING_CITATION",
                    severity="WARNING",
                    message=f"JudgePoint '{point_id}' sem citations nem legal_basis",
                    source=judge_id,
                ))
                confidence_penalty += 0.01

        rationale = getattr(point, 'rationale', '')
        if not rationale:
            errors.append(ValidationError(
                error_type="MISSING_RATIONALE",
                severity="INFO",
                message=f"JudgePoint '{point_id}' sem fundamentação textual",
                source=judge_id,
            ))

    disagreements = getattr(opinion, 'disagreements', [])
    for disagreement in disagreements:
        citations = getattr(disagreement, 'citations', [])
        for citation in citations:
            if hasattr(citation, 'to_dict'):
                citation_dict = citation.to_dict()
            elif isinstance(citation, dict):
                citation_dict = citation
            else:
                continue

            citation_valid, citation_errors = validate_citation(
                citation_dict, document_text, total_chars, page_mapper,
                source=judge_id,
            )
            errors.extend(citation_errors)

    return is_valid, errors, min(confidence_penalty, 0.3)


def validate_final_decision(
    decision: Any,
    unified_result: Optional[Any] = None,
    document_text: str = "",
    total_chars: int = 0,
    page_mapper: Optional[Any] = None
) -> Tuple[bool, List[ValidationError], float]:
    """Valida um FinalDecision completo."""
    errors = []
    is_valid = True
    confidence_penalty = 0.0

    proofs = getattr(decision, 'proofs', [])
    for proof in proofs:
        if hasattr(proof, 'to_dict'):
            proof_dict = proof.to_dict()
        elif isinstance(proof, dict):
            proof_dict = proof
        else:
            continue

        citation_valid, citation_errors = validate_citation(
            proof_dict, document_text, total_chars, page_mapper,
            source="presidente",
        )
        errors.extend(citation_errors)
        if not citation_valid:
            is_valid = False
            confidence_penalty += 0.05

    points = getattr(decision, 'decision_points_final', [])
    for point in points:
        citations = getattr(point, 'citations', [])
        for citation in citations:
            if hasattr(citation, 'to_dict'):
                citation_dict = citation.to_dict()
            elif isinstance(citation, dict):
                citation_dict = citation
            else:
                continue

            citation_valid, citation_errors = validate_citation(
                citation_dict, document_text, total_chars, page_mapper,
                source="presidente",
            )
            errors.extend(citation_errors)

    final_answer = getattr(decision, 'final_answer', '')
    if not final_answer or len(final_answer) < 20:
        errors.append(ValidationError(
            error_type="MISSING_ANSWER",
            severity="WARNING",
            message="FinalDecision com final_answer vazio ou muito curto",
            source="presidente",
        ))
        confidence_penalty += 0.1

    confidence = getattr(decision, 'confidence', 0.8)
    if confidence < 0.5:
        errors.append(ValidationError(
            error_type="LOW_CONFIDENCE",
            severity="INFO",
            message=f"FinalDecision com confidence baixo: {confidence:.2f}",
            source="presidente",
        ))

    return is_valid, errors, min(confidence_penalty, 0.5)


# ============================================================================
# INTEGRADOR DE VALIDAÇÃO
# ============================================================================

class IntegrityValidator:
    """
    Validador de integridade para todo o pipeline.

    Uso:
        validator = IntegrityValidator(run_id, document_text, page_mapper)
        report = validator.validate_and_annotate_audit(report, unified_result)
        opinion = validator.validate_and_annotate_judge(opinion, unified_result)
        decision = validator.validate_and_annotate_decision(decision, unified_result)
        validator.save_report()
    """

    def __init__(self, run_id: str, document_text: str = "",
                 total_chars: int = 0, page_mapper: Optional[Any] = None,
                 unified_result: Optional[Any] = None):
        self.run_id = run_id
        self.document_text = document_text
        self.total_chars = total_chars or len(document_text)
        self.page_mapper = page_mapper
        self.unified_result = unified_result
        self.report = IntegrityReport(run_id=run_id)

    def validate_and_annotate_audit(self, audit_report: Any, unified_result: Optional[Any] = None) -> Any:
        """Valida AuditReport e adiciona warnings aos errors[]."""
        result = unified_result or self.unified_result

        is_valid, errors, penalty = validate_audit_report(
            audit_report, result, self.document_text, self.total_chars, self.page_mapper,
        )

        for error in errors:
            self.report.add_error(error)
        self._update_counts_from_errors(errors, "phase2")

        existing_errors = getattr(audit_report, 'errors', [])
        if not isinstance(existing_errors, list):
            existing_errors = []

        for error in errors:
            warning_msg = f"INTEGRITY_WARNING: [{error.error_type}] {error.message}"
            if warning_msg not in existing_errors:
                existing_errors.append(warning_msg)

        try:
            audit_report.errors = existing_errors
        except AttributeError:
            pass

        logger.info(f"Validação {audit_report.auditor_id}: {len(errors)} erros, penalty={penalty:.2f}")
        return audit_report

    def validate_and_annotate_judge(self, judge_opinion: Any, unified_result: Optional[Any] = None) -> Any:
        """Valida JudgeOpinion e adiciona warnings aos errors[]."""
        result = unified_result or self.unified_result

        is_valid, errors, penalty = validate_judge_opinion(
            judge_opinion, result, self.document_text, self.total_chars, self.page_mapper,
        )

        for error in errors:
            self.report.add_error(error)
        self._update_counts_from_errors(errors, "phase3")

        existing_errors = getattr(judge_opinion, 'errors', [])
        if not isinstance(existing_errors, list):
            existing_errors = []

        for error in errors:
            warning_msg = f"INTEGRITY_WARNING: [{error.error_type}] {error.message}"
            if warning_msg not in existing_errors:
                existing_errors.append(warning_msg)

        try:
            judge_opinion.errors = existing_errors
        except AttributeError:
            pass

        # FIX 2026-02-10: Só ajustar confidence dos pontos se penalty significativo
        if penalty > 0.05 and hasattr(judge_opinion, 'decision_points'):
            for point in judge_opinion.decision_points:
                if hasattr(point, 'confidence'):
                    original = point.confidence
                    point.confidence = max(0.1, original - penalty)

        logger.info(f"Validação {judge_opinion.judge_id}: {len(errors)} erros, penalty={penalty:.2f}")
        return judge_opinion

    def validate_and_annotate_decision(self, final_decision: Any, unified_result: Optional[Any] = None) -> Any:
        """Valida FinalDecision e adiciona warnings aos errors[]."""
        result = unified_result or self.unified_result

        is_valid, errors, penalty = validate_final_decision(
            final_decision, result, self.document_text, self.total_chars, self.page_mapper,
        )

        for error in errors:
            self.report.add_error(error)
        self._update_counts_from_errors(errors, "phase4")

        existing_errors = getattr(final_decision, 'errors', [])
        if not isinstance(existing_errors, list):
            existing_errors = []

        for error in errors:
            warning_msg = f"INTEGRITY_WARNING: [{error.error_type}] {error.message}"
            if warning_msg not in existing_errors:
                existing_errors.append(warning_msg)

        try:
            final_decision.errors = existing_errors
        except AttributeError:
            pass

        if penalty > 0 and hasattr(final_decision, 'confidence'):
            original = final_decision.confidence
            final_decision.confidence = max(0.1, original - penalty)

        logger.info(f"Validação Presidente: {len(errors)} erros, penalty={penalty:.2f}")
        return final_decision

    def _update_counts_from_errors(self, errors: List[ValidationError], phase: str):
        """Atualiza contagens do relatório."""
        for error in errors:
            if phase == "phase2":
                self.report.phase2_errors += 1
            elif phase == "phase3":
                self.report.phase3_errors += 1
            elif phase == "phase4":
                self.report.phase4_errors += 1

            if error.error_type == "RANGE_INVALID":
                self.report.spans_out_of_range += 1
            elif error.error_type == "PAGE_MISMATCH":
                self.report.pages_mismatch += 1
            elif error.error_type == "EXCERPT_MISMATCH":
                self.report.excerpts_mismatch += 1
            elif error.error_type == "OFFSET_IMPRECISE":
                self.report.excerpts_offset_imprecise += 1
            elif error.error_type == "OFFSET_WRONG":
                self.report.excerpts_offset_wrong += 1
            elif error.error_type == "MISSING_CITATION":
                self.report.citations_invalid += 1
            elif error.error_type == "ITEM_NOT_FOUND":
                self.report.items_not_found += 1

    def finalize_counts(self, citations_total: int = 0, excerpts_checked: int = 0,
                        spans_total: int = 0, pages_checked: int = 0,
                        items_referenced: int = 0):
        """Finaliza contagens do relatório."""
        self.report.citations_total = citations_total
        self.report.citations_valid = citations_total - self.report.citations_invalid
        self.report.excerpts_checked = excerpts_checked
        self.report.excerpts_matched = excerpts_checked - self.report.excerpts_mismatch - self.report.excerpts_offset_imprecise - self.report.excerpts_offset_wrong
        self.report.spans_total = spans_total
        self.report.spans_valid = spans_total - self.report.spans_out_of_range
        self.report.pages_checked = pages_checked
        self.report.pages_valid = pages_checked - self.report.pages_mismatch
        self.report.items_referenced = items_referenced
        self.report.items_found = items_referenced - self.report.items_not_found

    def get_report(self) -> IntegrityReport:
        """Retorna o relatório de integridade."""
        return self.report

    def save_report(self, output_dir: Optional[Path] = None) -> Path:
        """Guarda o relatório em ficheiro."""
        return self.report.save(output_dir)


# ============================================================================
# WRAPPERS PARA INTEGRAÇÃO COM PARSERS
# ============================================================================

def parse_audit_report_with_validation(
    output: str, auditor_id: str, model_name: str, run_id: str,
    validator: Optional[IntegrityValidator] = None,
    unified_result: Optional[Any] = None
) -> Any:
    """Wrapper que parseia e valida AuditReport."""
    from src.pipeline.schema_audit import parse_audit_report
    report = parse_audit_report(output, auditor_id, model_name, run_id)
    if validator is not None:
        report = validator.validate_and_annotate_audit(report, unified_result)
    return report


def parse_judge_opinion_with_validation(
    output: str, judge_id: str, model_name: str, run_id: str,
    validator: Optional[IntegrityValidator] = None,
    unified_result: Optional[Any] = None
) -> Any:
    """Wrapper que parseia e valida JudgeOpinion."""
    from src.pipeline.schema_audit import parse_judge_opinion
    opinion = parse_judge_opinion(output, judge_id, model_name, run_id)
    if validator is not None:
        opinion = validator.validate_and_annotate_judge(opinion, unified_result)
    return opinion


def parse_final_decision_with_validation(
    output: str, model_name: str, run_id: str,
    validator: Optional[IntegrityValidator] = None,
    unified_result: Optional[Any] = None
) -> Any:
    """Wrapper que parseia e valida FinalDecision."""
    from src.pipeline.schema_audit import parse_final_decision
    decision = parse_final_decision(output, model_name, run_id)
    if validator is not None:
        decision = validator.validate_and_annotate_decision(decision, unified_result)
    return decision


# ============================================================================
# TESTE
# ============================================================================

if __name__ == "__main__":
    print("=== Teste IntegrityValidator ===\n")

    document_text = """[Página 1]
O contrato de arrendamento foi celebrado em 15 de Janeiro de 2024.
O valor mensal da renda é de €850,00 (oitocentos e cinquenta euros).

[Página 2]
As partes acordaram um prazo de 2 (dois) anos.
Nos termos do artigo 1022º do Código Civil.

[Página 3]
O inquilino compromete-se a pagar a renda até ao dia 8 de cada mês.
"""

    validator = IntegrityValidator(run_id="test_run_001", document_text=document_text)

    citation_ok = {
        "doc_id": "doc_test",
        "start_char": 10,
        "end_char": 80,
        "page_num": 1,
        "excerpt": "contrato de arrendamento foi celebrado em 15 de Janeiro",
    }

    is_valid, errors = validate_citation(citation_ok, document_text, len(document_text), source="test")
    print(f"Citation OK: valid={is_valid}, errors={len(errors)}")

    citation_bad = {
        "doc_id": "doc_test",
        "start_char": 1000,
        "end_char": 500,
        "page_num": 99,
        "excerpt": "texto que não existe",
    }

    is_valid, errors = validate_citation(citation_bad, document_text, len(document_text), source="test")
    print(f"Citation BAD: valid={is_valid}, errors={len(errors)}")
    for err in errors:
        print(f"  - [{err.error_type}] {err.message}")

    validator.report.citations_total = 2
    validator.report.citations_invalid = 1
    for err in errors:
        validator.report.add_error(err)

    print(f"\nRelatório:")
    print(validator.report.to_json())

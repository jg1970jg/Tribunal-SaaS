# -*- coding: utf-8 -*-
"""
CONSENSUS ENGINE - Validação Determinística + Consenso Adaptativo.

3 Fases com activação automática por threshold de runs:
  Fase A (sempre): Citation determinístico, JSON compliance, Canonical doc_id
  Fase B (≥10 runs): Normalização de severidade, Contradiction scan, Omission detection
  Fase C (≥30 runs): Score contínuo + política discreta, Peso histórico, Re-query

Princípio: outputs probabilísticos dos LLMs são validados por camadas determinísticas.
"""

import json
import logging
import re
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

from src.config import (
    LOG_LEVEL,
    HISTORICO_DIR,
    CITATION_FUZZY_THRESHOLD,
    CITATION_LENGTH_TOLERANCE,
    SEVERITY_LEVELS,
    SEVERITY_CRITICAL_KEYWORDS,
    SEVERITY_NEVER_REDUCE_BELOW,
    PHASE_B_MIN_RUNS,
    PHASE_C_MIN_RUNS,
    HISTORICAL_WINDOW,
    CONSENSUS_CERTIFIED_MIN_AUDITORS,
    CONSENSUS_PROBABLE_MIN_AUDITORS,
)

logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger(__name__)


# ============================================================================
# UTILIDADES
# ============================================================================

def count_historical_runs() -> int:
    """Conta o número de runs no directório histórico."""
    try:
        return len(list(HISTORICO_DIR.glob("*.json")))
    except Exception:
        return 0


def generate_canonical_doc_id(file_hash: str) -> str:
    """Gera doc_id canónico a partir do hash do ficheiro."""
    if not file_hash:
        return "doc_unknown"
    return f"doc_{file_hash[:12]}"


# ============================================================================
# FASE A: CITATION DETERMINÍSTICO (sempre activa)
# ============================================================================

@dataclass
class CitationValidationResult:
    """Resultado da validação de uma citation."""
    original_excerpt: str
    original_start_char: int
    original_end_char: int
    original_page_num: Optional[int]
    # Resultado
    status: str  # VALID_EXACT, VALID_FUZZY, PAGE_MISMATCH, INVALID_AMBIGUOUS, INVALID
    calculated_start_char: int = -1
    calculated_end_char: int = -1
    calculated_page_num: Optional[int] = None
    match_ratio: float = 0.0
    notes: str = ""


def validate_citation_2pass(
    excerpt: str,
    declared_page: Optional[int],
    canonical_text: str,
    page_texts: Dict[int, str],
    page_offsets: Dict[int, int],
    fuzzy_threshold: float = CITATION_FUZZY_THRESHOLD,
    length_tolerance: float = CITATION_LENGTH_TOLERANCE,
) -> CitationValidationResult:
    """
    Validação de citation em 2 passes:
      Pass 1: Exact/fuzzy na página declarada
      Pass 2: Exact/fuzzy no documento inteiro + PAGE_MISMATCH
      Pass 3: INVALID
    """
    result = CitationValidationResult(
        original_excerpt=excerpt,
        original_start_char=0,
        original_end_char=0,
        original_page_num=declared_page,
    )

    if not excerpt or not excerpt.strip():
        result.status = "INVALID"
        result.notes = "Excerpt vazio"
        return result

    excerpt_clean = excerpt.strip()

    # --- Pass 1: Busca na página declarada ---
    if declared_page and declared_page in page_texts:
        page_text = page_texts[declared_page]
        page_offset = page_offsets.get(declared_page, 0)

        match = _find_best_match(excerpt_clean, page_text, fuzzy_threshold, length_tolerance)
        if match:
            result.status = "VALID_EXACT" if match["ratio"] >= 0.99 else "VALID_FUZZY"
            result.calculated_start_char = page_offset + match["start"]
            result.calculated_end_char = page_offset + match["end"]
            result.calculated_page_num = declared_page
            result.match_ratio = match["ratio"]
            return result

    # --- Pass 2: Busca no documento inteiro ---
    matches = _find_all_matches(excerpt_clean, canonical_text, fuzzy_threshold, length_tolerance)

    if len(matches) == 1:
        m = matches[0]
        real_page = _offset_to_page(m["start"], page_offsets)
        if declared_page and real_page != declared_page:
            result.status = "PAGE_MISMATCH"
            result.notes = f"Encontrado na página {real_page}, declarado na página {declared_page}"
        else:
            result.status = "VALID_EXACT" if m["ratio"] >= 0.99 else "VALID_FUZZY"
        result.calculated_start_char = m["start"]
        result.calculated_end_char = m["end"]
        result.calculated_page_num = real_page
        result.match_ratio = m["ratio"]
        return result

    if len(matches) > 1:
        result.status = "INVALID_AMBIGUOUS"
        result.notes = f"{len(matches)} matches encontrados no documento"
        best = max(matches, key=lambda m: m["ratio"])
        result.calculated_start_char = best["start"]
        result.calculated_end_char = best["end"]
        result.calculated_page_num = _offset_to_page(best["start"], page_offsets)
        result.match_ratio = best["ratio"]
        return result

    # --- Pass 3: Nada encontrado ---
    result.status = "INVALID"
    result.notes = "Excerpt não encontrado no documento"
    return result


def _find_best_match(
    excerpt: str, text: str,
    threshold: float, length_tolerance: float
) -> Optional[Dict]:
    """Encontra o melhor match de excerpt em text."""
    idx = text.find(excerpt)
    if idx >= 0:
        return {"start": idx, "end": idx + len(excerpt), "ratio": 1.0}

    idx = text.lower().find(excerpt.lower())
    if idx >= 0:
        return {"start": idx, "end": idx + len(excerpt), "ratio": 0.99}

    excerpt_len = len(excerpt)
    min_len = int(excerpt_len * (1.0 - length_tolerance))
    max_len = int(excerpt_len * (1.0 + length_tolerance))

    best_ratio = 0.0
    best_match = None

    for window_size in range(min_len, max_len + 1):
        for start in range(0, len(text) - window_size + 1, max(1, window_size // 4)):
            candidate = text[start:start + window_size]
            ratio = SequenceMatcher(None, excerpt.lower(), candidate.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = {"start": start, "end": start + window_size, "ratio": ratio}

    if best_match and best_ratio >= threshold:
        return best_match
    return None


def _find_all_matches(
    excerpt: str, text: str,
    threshold: float, length_tolerance: float
) -> List[Dict]:
    """Encontra todos os matches de excerpt em text."""
    matches = []

    start_pos = 0
    while True:
        idx = text.lower().find(excerpt.lower(), start_pos)
        if idx < 0:
            break
        ratio = 1.0 if text[idx:idx+len(excerpt)] == excerpt else 0.99
        matches.append({"start": idx, "end": idx + len(excerpt), "ratio": ratio})
        start_pos = idx + 1

    if matches:
        return matches

    best = _find_best_match(excerpt, text, threshold, length_tolerance)
    if best:
        return [best]
    return []


def _offset_to_page(offset: int, page_offsets: Dict[int, int]) -> Optional[int]:
    """Converte offset absoluto para número de página."""
    best_page = None
    best_offset = -1
    for page_num, page_start in sorted(page_offsets.items()):
        if page_start <= offset and page_start > best_offset:
            best_page = page_num
            best_offset = page_start
    return best_page


def validate_all_citations(
    audit_reports: list,
    canonical_text: str,
    page_texts: Dict[int, str],
    page_offsets: Dict[int, int],
    canonical_doc_id: str,
) -> Dict:
    """Valida todas as citations de todos os audit reports."""
    results = {
        "auditor_scores": {},
        "total_citations": 0,
        "valid_citations": 0,
        "invalid_citations": 0,
        "page_mismatches": 0,
        "ambiguous_citations": 0,
        "details": [],
    }

    for report in audit_reports:
        auditor_id = report.auditor_id
        auditor_total = 0
        auditor_valid = 0
        auditor_details = []

        for finding in report.findings:
            for citation in finding.citations:
                auditor_total += 1
                results["total_citations"] += 1

                validation = validate_citation_2pass(
                    excerpt=citation.excerpt,
                    declared_page=citation.page_num,
                    canonical_text=canonical_text,
                    page_texts=page_texts,
                    page_offsets=page_offsets,
                )

                detail = {
                    "auditor_id": auditor_id,
                    "finding_id": finding.finding_id,
                    "status": validation.status,
                    "match_ratio": validation.match_ratio,
                    "original_excerpt": validation.original_excerpt[:100],
                    "notes": validation.notes,
                }

                if validation.status in ("VALID_EXACT", "VALID_FUZZY", "PAGE_MISMATCH"):
                    citation.start_char = validation.calculated_start_char
                    citation.end_char = validation.calculated_end_char
                    if validation.calculated_page_num:
                        citation.page_num = validation.calculated_page_num
                    auditor_valid += 1
                    results["valid_citations"] += 1
                elif validation.status == "INVALID_AMBIGUOUS":
                    results["ambiguous_citations"] += 1
                    if validation.calculated_start_char >= 0:
                        citation.start_char = validation.calculated_start_char
                        citation.end_char = validation.calculated_end_char
                else:
                    results["invalid_citations"] += 1

                if validation.status == "PAGE_MISMATCH":
                    results["page_mismatches"] += 1

                citation.doc_id = canonical_doc_id
                auditor_details.append(detail)

        citation_validity = auditor_valid / auditor_total if auditor_total > 0 else 1.0
        results["auditor_scores"][auditor_id] = {
            "total_citations": auditor_total,
            "valid_citations": auditor_valid,
            "citation_validity_score": round(citation_validity, 4),
        }
        results["details"].extend(auditor_details)

    return results


# ============================================================================
# FASE A: JSON COMPLIANCE SCORING
# ============================================================================

def calculate_format_compliance(audit_reports: list) -> Dict[str, float]:
    """Calcula score de compliance de formato JSON por auditor."""
    scores = {}
    for report in audit_reports:
        parse_errors = [e for e in report.errors if "Parse" in e or "parse" in e]
        has_parse_failure = len(parse_errors) > 0
        if has_parse_failure:
            scores[report.auditor_id] = 0.7
        else:
            scores[report.auditor_id] = 1.0
    return scores


# ============================================================================
# FASE B: NORMALIZAÇÃO DE SEVERIDADE (activa após PHASE_B_MIN_RUNS)
# ============================================================================

NORMAS_IMPERATIVAS_PATTERNS = [
    r'\b(?:Lei|DL|Decreto[- ]Lei)\s*n\.?[º°]?\s*\d+',
    r'\bCódigo\s+(?:Civil|Penal|Processo|Trabalho)',
    r'\bCPC\b|\bCC\b|\bCPA\b|\bCPT\b',
    r'\bNRAU\b',
    r'\bRGPD\b',
]

ELEMENTOS_ESSENCIAIS_KEYWORDS = [
    "partes", "objeto", "renda", "prazo", "preço", "assinatura",
    "identificação", "nif", "morada", "licença de utilização",
    "certificado energético", "caução",
]


def normalize_severity(
    finding_claim: str,
    finding_severity: str,
    citations: list,
    never_below: str = SEVERITY_NEVER_REDUCE_BELOW,
) -> Tuple[str, str]:
    """Normaliza severidade de um finding usando critérios objectivos."""
    severity_order = {s: i for i, s in enumerate(SEVERITY_LEVELS)}
    original_idx = severity_order.get(finding_severity, 1)
    min_idx = severity_order.get(never_below, 1)

    claim_lower = finding_claim.lower()

    has_imperative_law = False
    has_valid_citation = False
    for cit in citations:
        excerpt = getattr(cit, 'excerpt', '') or ''
        for pattern in NORMAS_IMPERATIVAS_PATTERNS:
            if re.search(pattern, excerpt, re.IGNORECASE):
                has_imperative_law = True
                break
        if hasattr(cit, 'start_char') and cit.start_char > 0:
            has_valid_citation = True

    is_essential_element = any(kw in claim_lower for kw in ELEMENTOS_ESSENCIAIS_KEYWORDS)
    has_critical_keyword = any(kw in claim_lower for kw in SEVERITY_CRITICAL_KEYWORDS)

    if has_imperative_law and is_essential_element:
        normalized = "critico"
        reason = "Norma imperativa + elemento essencial"
    elif has_imperative_law or (has_critical_keyword and has_valid_citation):
        normalized = max("alto", finding_severity, key=lambda s: severity_order.get(s, 0))
        reason = "Norma imperativa ou keyword crítica com citation válida"
    elif has_valid_citation:
        normalized = max(finding_severity, "medio", key=lambda s: severity_order.get(s, 0))
        reason = "Citation válida"
    else:
        normalized = finding_severity
        reason = "Sem base para reclassificar"

    normalized_idx = severity_order.get(normalized, original_idx)
    if normalized_idx < min_idx and original_idx >= min_idx:
        normalized = never_below
        reason += f" (floor: {never_below})"

    if severity_order.get(normalized, 0) < original_idx:
        if not (finding_severity == "critico" and normalized == "alto"):
            normalized = finding_severity
            reason = "Mantido original"

    return normalized, reason


def normalize_all_severities(audit_reports: list) -> Dict:
    """Normaliza severidade de todos os findings em todos os reports."""
    normalizations = {}

    for report in audit_reports:
        for finding in report.findings:
            original = finding.severity.value if hasattr(finding.severity, 'value') else str(finding.severity)
            normalized, reason = normalize_severity(
                finding_claim=finding.claim,
                finding_severity=original,
                citations=finding.citations,
            )
            normalizations[f"{report.auditor_id}:{finding.finding_id}"] = {
                "auditor_id": report.auditor_id,
                "finding_id": finding.finding_id,
                "original_severity": original,
                "normalized_severity": normalized,
                "reason": reason,
            }

    return normalizations


# ============================================================================
# FASE B: CONTRADICTION SCAN (activa após PHASE_B_MIN_RUNS)
# ============================================================================

def find_contradictions(audit_reports: list, severity_normalizations: Dict) -> List[Dict]:
    """Detecta contradições entre auditores baseado em overlap de offsets."""
    contradictions = []
    finding_clusters = _build_finding_clusters(audit_reports)

    for cluster_id, cluster_findings in finding_clusters.items():
        if len(cluster_findings) < 2:
            continue

        severities = {}
        for cf in cluster_findings:
            key = f"{cf['auditor_id']}:{cf['finding_id']}"
            norm = severity_normalizations.get(key, {})
            sev = norm.get("normalized_severity", cf["severity"])
            severities[cf["auditor_id"]] = {
                "severity": sev,
                "finding_id": cf["finding_id"],
                "claim": cf["claim"],
            }

        severity_order = {s: i for i, s in enumerate(SEVERITY_LEVELS)}
        sev_values = [severity_order.get(s["severity"], 1) for s in severities.values()]

        if not sev_values:
            continue

        max_sev = max(sev_values)
        min_sev = min(sev_values)

        if max_sev == 3 and min_sev <= 1:
            contradictions.append({
                "cluster_id": cluster_id,
                "type": "SEVERITY_CONFLICT",
                "auditors": severities,
                "severity_range": f"{SEVERITY_LEVELS[min_sev]} vs {SEVERITY_LEVELS[max_sev]}",
                "requires_review": True,
            })

    return contradictions


def _build_finding_clusters(audit_reports: list) -> Dict[str, List[Dict]]:
    """Agrupa findings de diferentes auditores por overlap de offsets (>=30%)."""
    all_findings = []
    for report in audit_reports:
        for finding in report.findings:
            offsets = []
            for cit in finding.citations:
                if cit.start_char > 0 and cit.end_char > cit.start_char:
                    offsets.append((cit.start_char, cit.end_char))

            sev = finding.severity.value if hasattr(finding.severity, 'value') else str(finding.severity)
            all_findings.append({
                "auditor_id": report.auditor_id,
                "finding_id": finding.finding_id,
                "claim": finding.claim[:200],
                "severity": sev,
                "offsets": offsets,
            })

    clusters = {}
    cluster_counter = 0
    assigned = set()

    for i, f1 in enumerate(all_findings):
        if i in assigned:
            continue

        cluster_id = f"cluster_{cluster_counter}"
        clusters[cluster_id] = [f1]
        assigned.add(i)

        for j, f2 in enumerate(all_findings):
            if j in assigned or f1["auditor_id"] == f2["auditor_id"]:
                continue
            if _offsets_overlap(f1["offsets"], f2["offsets"], min_overlap=0.3):
                clusters[cluster_id].append(f2)
                assigned.add(j)

        cluster_counter += 1

    return clusters


def _offsets_overlap(offsets1: List[Tuple], offsets2: List[Tuple], min_overlap: float) -> bool:
    """Verifica se dois conjuntos de offsets têm overlap >= min_overlap."""
    for s1, e1 in offsets1:
        for s2, e2 in offsets2:
            overlap_start = max(s1, s2)
            overlap_end = min(e1, e2)
            if overlap_end > overlap_start:
                overlap_len = overlap_end - overlap_start
                min_range = min(e1 - s1, e2 - s2)
                if min_range > 0 and overlap_len / min_range >= min_overlap:
                    return True
    return False


# ============================================================================
# FASE B: OMISSION DETECTION (activa após PHASE_B_MIN_RUNS)
# ============================================================================

def detect_omissions(audit_reports: list, finding_clusters: Dict) -> List[Dict]:
    """Detecta omissões entre auditores."""
    omissions = []
    auditor_ids = set(r.auditor_id for r in audit_reports)

    for cluster_id, cluster_findings in finding_clusters.items():
        participating_auditors = set(f["auditor_id"] for f in cluster_findings)
        missing_auditors = auditor_ids - participating_auditors

        if not missing_auditors:
            continue

        severity_order = {s: i for i, s in enumerate(SEVERITY_LEVELS)}
        max_severity = max(
            (severity_order.get(f["severity"], 0) for f in cluster_findings),
            default=0
        )

        if max_severity >= 2:
            omissions.append({
                "cluster_id": cluster_id,
                "type": "OMISSION_SUSPECTED" if max_severity >= 3 else "OMISSION_POSSIBLE",
                "present_auditors": list(participating_auditors),
                "missing_auditors": list(missing_auditors),
                "max_severity": SEVERITY_LEVELS[max_severity],
                "claims": [f["claim"][:200] for f in cluster_findings],
                "requires_requery": max_severity >= 3,
            })

    return omissions


# ============================================================================
# FASE C: SCORE CONTÍNUO + POLÍTICA DISCRETA (activa após PHASE_C_MIN_RUNS)
# ============================================================================

def calculate_finding_score(
    finding_cluster: List[Dict],
    citation_validity_scores: Dict[str, float],
    format_compliance_scores: Dict[str, float],
    severity_normalizations: Dict,
    n_total_auditors: int,
) -> Dict:
    """Calcula score contínuo interno para um cluster de findings."""
    if not finding_cluster:
        return {"score": 0.0, "category": "NAO_FIAVEL", "reason": "Cluster vazio"}

    participating_auditors = set(f["auditor_id"] for f in finding_cluster)
    n_agreeing = len(participating_auditors)

    agreement_ratio = n_agreeing / n_total_auditors if n_total_auditors > 0 else 0

    avg_citation_validity = 0.0
    avg_format_compliance = 0.0
    for aid in participating_auditors:
        cv = citation_validity_scores.get(aid, {})
        avg_citation_validity += cv.get("citation_validity_score", 0.5)
        avg_format_compliance += format_compliance_scores.get(aid, 0.5)
    avg_citation_validity /= max(n_agreeing, 1)
    avg_format_compliance /= max(n_agreeing, 1)

    score = (
        agreement_ratio * 0.50 +
        avg_citation_validity * 0.30 +
        avg_format_compliance * 0.20
    )
    score = round(score, 4)

    if n_agreeing >= CONSENSUS_CERTIFIED_MIN_AUDITORS and avg_citation_validity >= 0.7:
        category = "FACTO_CERTIFICADO"
    elif n_agreeing >= CONSENSUS_PROBABLE_MIN_AUDITORS and avg_citation_validity >= 0.5:
        category = "FACTO_PROVAVEL"
    elif n_agreeing == 1:
        sev = finding_cluster[0].get("severity", "baixo")
        severity_order = {s: i for i, s in enumerate(SEVERITY_LEVELS)}
        if severity_order.get(sev, 0) >= 2:
            category = "FINDING_ISOLADO"
        else:
            category = "NAO_FIAVEL"
    else:
        category = "FACTO_PROVAVEL"

    return {
        "score": score,
        "category": category,
        "n_auditors": n_agreeing,
        "agreement_ratio": round(agreement_ratio, 4),
        "avg_citation_validity": round(avg_citation_validity, 4),
        "avg_format_compliance": round(avg_format_compliance, 4),
        "reason": f"{n_agreeing}/{n_total_auditors} auditores, citation={avg_citation_validity:.2f}",
    }


# ============================================================================
# FASE C: PESO HISTÓRICO ADAPTATIVO (activa após PHASE_C_MIN_RUNS)
# ============================================================================

def load_historical_metrics(window: int = HISTORICAL_WINDOW) -> Dict[str, List[Dict]]:
    """Carrega métricas históricas dos últimos N runs."""
    metrics_by_auditor = {}

    try:
        consensus_files = sorted(HISTORICO_DIR.glob("*_consensus_metrics.json"))[-window:]
        for fpath in consensus_files:
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for auditor_id, scores in data.get("auditor_scores", {}).items():
                    if auditor_id not in metrics_by_auditor:
                        metrics_by_auditor[auditor_id] = []
                    metrics_by_auditor[auditor_id].append(scores)
            except Exception:
                continue
    except Exception:
        pass

    return metrics_by_auditor


def calculate_adaptive_weights(historical_metrics: Dict[str, List[Dict]]) -> Dict[str, float]:
    """Calcula pesos adaptativos baseados na performance histórica."""
    weights = {}

    for auditor_id, runs in historical_metrics.items():
        if not runs:
            weights[auditor_id] = 1.0
            continue

        validity_scores = [r.get("citation_validity_score", 0.5) for r in runs]
        avg_validity = sum(validity_scores) / len(validity_scores)
        weights[auditor_id] = round(max(0.3, avg_validity), 4)

    return weights


# ============================================================================
# FASE C: RE-QUERY IDENTIFICATION (activa após PHASE_C_MIN_RUNS)
# ============================================================================

def identify_requery_candidates(
    contradictions: List[Dict],
    omissions: List[Dict],
    max_per_run: int,
    max_per_auditor: int,
) -> List[Dict]:
    """Identifica candidatos a re-query baseado em conflitos e omissões."""
    candidates = []
    auditor_counts = {}

    for contradiction in contradictions:
        if not contradiction.get("requires_review"):
            continue
        for auditor_id, info in contradiction.get("auditors", {}).items():
            if info.get("severity") != "critico":
                if auditor_counts.get(auditor_id, 0) < max_per_auditor:
                    candidates.append({
                        "type": "CONTRADICTION_REQUERY",
                        "auditor_id": auditor_id,
                        "cluster_id": contradiction["cluster_id"],
                        "question": f"Reavalia o seguinte facto. Outro auditor classificou como crítico: {info.get('claim', '')[:200]}",
                        "priority": 1,
                    })
                    auditor_counts[auditor_id] = auditor_counts.get(auditor_id, 0) + 1

    for omission in omissions:
        if not omission.get("requires_requery"):
            continue
        for auditor_id in omission.get("missing_auditors", []):
            if auditor_counts.get(auditor_id, 0) < max_per_auditor:
                claims = omission.get("claims", [""])
                candidates.append({
                    "type": "OMISSION_REQUERY",
                    "auditor_id": auditor_id,
                    "cluster_id": omission["cluster_id"],
                    "question": f"Verificaste este aspecto? Outro(s) auditor(es) identificaram: {claims[0][:200]}",
                    "priority": 2,
                })
                auditor_counts[auditor_id] = auditor_counts.get(auditor_id, 0) + 1

    candidates.sort(key=lambda c: c["priority"])
    return candidates[:max_per_run]


# ============================================================================
# ORQUESTRADOR PRINCIPAL
# ============================================================================

def run_consensus_engine(
    audit_reports: list,
    canonical_text: str,
    page_texts: Dict[int, str],
    page_offsets: Dict[int, int],
    file_hash: str,
    output_dir: Path,
) -> Dict:
    """
    Executa o consensus engine completo (Fases A + B + C).
    Fases B e C activam automaticamente por threshold de runs.
    """
    run_count = count_historical_runs()
    canonical_doc_id = generate_canonical_doc_id(file_hash)
    n_auditors = len(audit_reports)

    logger.info(f"[CONSENSUS] Iniciando consensus engine (run histórico #{run_count + 1})")
    logger.info(f"[CONSENSUS] Canonical doc_id: {canonical_doc_id}")
    logger.info(f"[CONSENSUS] Fase A: ACTIVA | Fase B: {'ACTIVA' if run_count >= PHASE_B_MIN_RUNS else f'RECOLHA (faltam {PHASE_B_MIN_RUNS - run_count} runs)'} | Fase C: {'ACTIVA' if run_count >= PHASE_C_MIN_RUNS else f'RECOLHA (faltam {PHASE_C_MIN_RUNS - run_count} runs)'}")

    result = {
        "run_count": run_count + 1,
        "canonical_doc_id": canonical_doc_id,
        "phases_active": {
            "A": True,
            "B": run_count >= PHASE_B_MIN_RUNS,
            "C": run_count >= PHASE_C_MIN_RUNS,
        },
        "timestamp": datetime.now().isoformat(),
    }

    # =====================================================================
    # FASE A: SEMPRE ACTIVA
    # =====================================================================

    logger.info("[CONSENSUS] Fase A1: Validação de citations (2-pass)...")
    citation_results = validate_all_citations(
        audit_reports=audit_reports,
        canonical_text=canonical_text,
        page_texts=page_texts,
        page_offsets=page_offsets,
        canonical_doc_id=canonical_doc_id,
    )
    result["citation_validation"] = {
        "auditor_scores": citation_results["auditor_scores"],
        "total": citation_results["total_citations"],
        "valid": citation_results["valid_citations"],
        "invalid": citation_results["invalid_citations"],
        "page_mismatches": citation_results["page_mismatches"],
        "ambiguous": citation_results["ambiguous_citations"],
    }
    logger.info(
        f"[CONSENSUS] Citations: {citation_results['valid_citations']}/{citation_results['total_citations']} válidas, "
        f"{citation_results['invalid_citations']} inválidas, {citation_results['page_mismatches']} page mismatches"
    )

    logger.info("[CONSENSUS] Fase A2: Format compliance scoring...")
    format_scores = calculate_format_compliance(audit_reports)
    result["format_compliance"] = format_scores

    layer1_metrics = {
        "auditor_scores": citation_results["auditor_scores"],
        "format_compliance": format_scores,
    }
    result["layer1_metrics"] = layer1_metrics

    # =====================================================================
    # FASE B: SEMPRE CALCULA, SÓ APLICA APÓS THRESHOLD
    # =====================================================================

    logger.info("[CONSENSUS] Fase B1: Normalização de severidade...")
    severity_normalizations = normalize_all_severities(audit_reports)
    result["severity_normalizations"] = severity_normalizations

    changes = sum(1 for v in severity_normalizations.values()
                  if v["original_severity"] != v["normalized_severity"])
    logger.info(f"[CONSENSUS] Severidade: {changes}/{len(severity_normalizations)} findings reclassificados")

    logger.info("[CONSENSUS] Fase B2: Contradiction scan...")
    finding_clusters = _build_finding_clusters(audit_reports)
    contradictions = find_contradictions(audit_reports, severity_normalizations)
    result["contradictions"] = contradictions

    if contradictions:
        logger.warning(f"[CONSENSUS] {len(contradictions)} contradição(ões) detectada(s)")
    else:
        logger.info("[CONSENSUS] 0 contradições detectadas")

    logger.info("[CONSENSUS] Fase B3: Omission detection...")
    omissions = detect_omissions(audit_reports, finding_clusters)
    result["omissions"] = omissions

    suspected = [o for o in omissions if o["type"] == "OMISSION_SUSPECTED"]
    if suspected:
        logger.warning(f"[CONSENSUS] {len(suspected)} omissão(ões) suspeita(s)")

    if run_count >= PHASE_B_MIN_RUNS:
        result["phase_b_applied"] = True
        logger.info("[CONSENSUS] Fase B: FLAGS APLICADOS (threshold atingido)")
    else:
        result["phase_b_applied"] = False
        logger.info(f"[CONSENSUS] Fase B: dados recolhidos (threshold {PHASE_B_MIN_RUNS} não atingido, actual: {run_count})")

    # =====================================================================
    # FASE C: SEMPRE CALCULA, SÓ APLICA APÓS THRESHOLD
    # =====================================================================

    logger.info("[CONSENSUS] Fase C1: Score contínuo por cluster...")
    cluster_scores = {}
    for cluster_id, cluster_findings in finding_clusters.items():
        score = calculate_finding_score(
            finding_cluster=cluster_findings,
            citation_validity_scores=citation_results["auditor_scores"],
            format_compliance_scores=format_scores,
            severity_normalizations=severity_normalizations,
            n_total_auditors=n_auditors,
        )
        cluster_scores[cluster_id] = score
    result["cluster_scores"] = cluster_scores

    categories = {}
    for cs in cluster_scores.values():
        cat = cs["category"]
        categories[cat] = categories.get(cat, 0) + 1
    result["category_summary"] = categories
    logger.info(f"[CONSENSUS] Categorias: {categories}")

    historical_metrics = load_historical_metrics()
    adaptive_weights = calculate_adaptive_weights(historical_metrics)
    result["adaptive_weights"] = adaptive_weights

    requery_candidates = identify_requery_candidates(
        contradictions=contradictions,
        omissions=omissions,
        max_per_run=3,
        max_per_auditor=1,
    )
    result["requery_candidates"] = requery_candidates

    if run_count >= PHASE_C_MIN_RUNS:
        result["phase_c_applied"] = True
        logger.info(f"[CONSENSUS] Fase C: ACTIVA (threshold {PHASE_C_MIN_RUNS} atingido)")
        if requery_candidates:
            logger.info(f"[CONSENSUS] {len(requery_candidates)} re-query(ies) identificada(s)")
        if adaptive_weights:
            logger.info(f"[CONSENSUS] Pesos adaptativos: {adaptive_weights}")
    else:
        result["phase_c_applied"] = False
        logger.info(f"[CONSENSUS] Fase C: dados recolhidos (threshold {PHASE_C_MIN_RUNS} não atingido, actual: {run_count})")

    # =====================================================================
    # GUARDAR MÉTRICAS
    # =====================================================================

    consensus_path = output_dir / "consensus_engine_report.json"
    try:
        save_data = {
            k: v for k, v in result.items()
            if k != "citation_validation" or k == "citation_validation"
        }
        if "citation_validation" in save_data:
            save_data["citation_validation"] = {
                k: v for k, v in save_data["citation_validation"].items()
            }

        with open(consensus_path, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"[CONSENSUS] Relatório guardado: {consensus_path}")
    except Exception as e:
        logger.error(f"[CONSENSUS] Erro ao guardar relatório: {e}")

    try:
        metrics_path = HISTORICO_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_consensus_metrics.json"
        metrics_data = {
            "auditor_scores": citation_results["auditor_scores"],
            "format_compliance": format_scores,
            "run_count": run_count + 1,
            "timestamp": datetime.now().isoformat(),
        }
        with open(metrics_path, 'w', encoding='utf-8') as f:
            json.dump(metrics_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[CONSENSUS] Erro ao guardar métricas históricas: {e}")

    return result

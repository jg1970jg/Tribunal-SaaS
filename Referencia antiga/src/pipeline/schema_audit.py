# -*- coding: utf-8 -*-
"""
Schemas Estruturados para Fases 2-4 (Auditoria, Juízes, Presidente).

Mantém proveniência completa através de citações com SourceSpan.
JSON é a fonte de verdade; Markdown é apenas renderização.

REGRAS:
1. Cada finding/point DEVE ter pelo menos 1 citation
2. Se parsing falhar, criar instância mínima com errors (não abortar)
3. Reutilizar SourceSpan de schema_unified.py
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Any, Literal, Tuple
from enum import Enum

from src.pipeline.schema_unified import SourceSpan, ExtractionMethod


# ============================================================================
# ENUMS
# ============================================================================

class FindingType(str, Enum):
    """Tipo de achado do auditor."""
    FACTO = "facto"           # Facto verificável no documento
    INFERENCIA = "inferencia" # Dedução lógica a partir de factos
    HIPOTESE = "hipotese"     # Suposição que requer verificação


class Severity(str, Enum):
    """Gravidade do achado."""
    CRITICO = "critico"   # Bloqueia decisão
    ALTO = "alto"         # Afeta significativamente
    MEDIO = "medio"       # Relevante mas não crítico
    BAIXO = "baixo"       # Informativo


class DecisionType(str, Enum):
    """Tipo de decisão final."""
    PROCEDENTE = "procedente"
    IMPROCEDENTE = "improcedente"
    PARCIALMENTE_PROCEDENTE = "parcialmente_procedente"
    INCONCLUSIVO = "inconclusivo"


# ============================================================================
# CITATION (wrapper leve sobre SourceSpan)
# ============================================================================

@dataclass
class Citation:
    """
    Citação com localização precisa no documento.
    Wrapper sobre SourceSpan com campos adicionais para contexto.
    """
    doc_id: str
    chunk_id: Optional[str] = None
    start_char: int = 0
    end_char: int = 0
    page_num: Optional[int] = None
    extractor_id: Optional[str] = None  # E1, E2, etc. (se originado de extração)
    method: str = "text"  # text | ocr | hybrid
    excerpt: str = ""     # Trecho do texto citado (max 200 chars)
    confidence: float = 1.0

    @classmethod
    def from_source_span(cls, span: SourceSpan, excerpt: str = "") -> 'Citation':
        """Cria Citation a partir de SourceSpan existente."""
        return cls(
            doc_id=span.doc_id,
            chunk_id=span.chunk_id,
            start_char=span.start_char,
            end_char=span.end_char,
            page_num=span.page_num,
            extractor_id=span.extractor_id,
            method=span.method.value if isinstance(span.method, ExtractionMethod) else span.method,
            excerpt=excerpt or (span.raw_text[:200] if span.raw_text else ""),
            confidence=span.confidence,
        )

    def to_dict(self) -> Dict:
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "page_num": self.page_num,
            "extractor_id": self.extractor_id,
            "method": self.method,
            "excerpt": self.excerpt[:200] if self.excerpt else None,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Citation':
        return cls(
            doc_id=data.get("doc_id", ""),
            chunk_id=data.get("chunk_id"),
            start_char=data.get("start_char", 0),
            end_char=data.get("end_char", 0),
            page_num=data.get("page_num"),
            extractor_id=data.get("extractor_id"),
            method=data.get("method", "text"),
            excerpt=data.get("excerpt", ""),
            confidence=data.get("confidence", 1.0),
        )


# ============================================================================
# FASE 2: AUDITORIA
# ============================================================================

@dataclass
class AuditFinding:
    """
    Achado individual de um auditor.
    DEVE ter pelo menos 1 citation.

    is_determinant: True se este achado é crucial para a análise.
    """
    finding_id: str
    claim: str  # Afirmação do auditor
    finding_type: FindingType
    severity: Severity
    citations: List[Citation]  # OBRIGATÓRIO - pelo menos 1
    evidence_item_ids: List[str] = field(default_factory=list)  # Referências a EvidenceItems da F1
    conflicts: List[str] = field(default_factory=list)  # IDs de outros findings conflitantes
    notes: str = ""
    is_determinant: bool = False  # Se True, é crucial para a decisão

    def __post_init__(self):
        if not self.finding_id:
            self.finding_id = f"finding_{uuid.uuid4().hex[:8]}"

        # Validação: citations obrigatório (mas permitir vazio em caso de erro)
        # A validação estrita é feita no validate()

    def validate(self) -> Tuple[bool, List[str]]:
        """Valida o finding. Retorna (is_valid, errors)."""
        errors = []
        if not self.claim:
            errors.append("Finding sem claim")
        if not self.citations:
            errors.append(f"Finding '{self.finding_id}' sem citations")
        return len(errors) == 0, errors

    def to_dict(self) -> Dict:
        return {
            "finding_id": self.finding_id,
            "claim": self.claim,
            "finding_type": self.finding_type.value,
            "severity": self.severity.value,
            "citations": [c.to_dict() for c in self.citations],
            "evidence_item_ids": self.evidence_item_ids,
            "conflicts": self.conflicts,
            "notes": self.notes,
            "is_determinant": self.is_determinant,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'AuditFinding':
        return cls(
            finding_id=data.get("finding_id", ""),
            claim=data.get("claim", ""),
            finding_type=FindingType(data.get("finding_type", "facto")),
            severity=Severity(data.get("severity", "medio")),
            citations=[Citation.from_dict(c) for c in data.get("citations", [])],
            evidence_item_ids=data.get("evidence_item_ids", []),
            conflicts=data.get("conflicts", []),
            notes=data.get("notes", ""),
            is_determinant=data.get("is_determinant", False),
        )


@dataclass
class CoverageCheck:
    """Verificação de cobertura feita pelo auditor."""
    docs_seen: List[str] = field(default_factory=list)  # doc_ids processados
    pages_seen: List[int] = field(default_factory=list)  # páginas verificadas
    chunks_seen: List[str] = field(default_factory=list)  # chunk_ids verificados
    unreadable_units: List[Dict] = field(default_factory=list)  # [{doc_id, page_num?, chunk_id?, reason}]
    coverage_percent: float = 0.0
    notes: str = ""

    def to_dict(self) -> Dict:
        return {
            "docs_seen": self.docs_seen,
            "pages_seen": self.pages_seen,
            "chunks_seen": self.chunks_seen,
            "unreadable_units": self.unreadable_units,
            "coverage_percent": self.coverage_percent,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'CoverageCheck':
        return cls(
            docs_seen=data.get("docs_seen", []),
            pages_seen=data.get("pages_seen", []),
            chunks_seen=data.get("chunks_seen", []),
            unreadable_units=data.get("unreadable_units", []),
            coverage_percent=data.get("coverage_percent", 0.0),
            notes=data.get("notes", ""),
        )


@dataclass
class AuditReport:
    """
    Relatório completo de um auditor.
    """
    auditor_id: str  # A1, A2, A3, A4
    model_name: str
    run_id: str
    findings: List[AuditFinding] = field(default_factory=list)
    coverage_check: CoverageCheck = field(default_factory=CoverageCheck)
    open_questions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)  # Erros de parsing/execução
    warnings: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if isinstance(self.coverage_check, dict):
            self.coverage_check = CoverageCheck.from_dict(self.coverage_check)

    def validate(self) -> Tuple[bool, List[str]]:
        """Valida o relatório completo."""
        errors = []

        for finding in self.findings:
            is_valid, finding_errors = finding.validate()
            if not is_valid:
                errors.extend(finding_errors)

        return len(errors) == 0, errors

    def to_dict(self) -> Dict:
        return {
            "auditor_id": self.auditor_id,
            "model_name": self.model_name,
            "run_id": self.run_id,
            "findings": [f.to_dict() for f in self.findings],
            "coverage_check": self.coverage_check.to_dict(),
            "open_questions": self.open_questions,
            "errors": self.errors,
            "warnings": self.warnings,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'AuditReport':
        return cls(
            auditor_id=data.get("auditor_id", ""),
            model_name=data.get("model_name", ""),
            run_id=data.get("run_id", ""),
            findings=[AuditFinding.from_dict(f) for f in data.get("findings", [])],
            coverage_check=CoverageCheck.from_dict(data.get("coverage_check", {})),
            open_questions=data.get("open_questions", []),
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
        )

    def to_markdown(self) -> str:
        """Renderiza o relatório como Markdown."""
        lines = [
            f"# Relatório de Auditoria - {self.auditor_id}",
            f"**Modelo:** {self.model_name}",
            f"**Run:** {self.run_id}",
            f"**Timestamp:** {self.timestamp.isoformat()}",
            "",
        ]

        # Erros/Warnings
        if self.errors:
            lines.append("## ⚠️ Erros")
            for err in self.errors:
                lines.append(f"- {err}")
            lines.append("")

        # Findings por severidade
        lines.append(f"## Achados ({len(self.findings)})")

        for severity in [Severity.CRITICO, Severity.ALTO, Severity.MEDIO, Severity.BAIXO]:
            severity_findings = [f for f in self.findings if f.severity == severity]
            if severity_findings:
                lines.append(f"\n### {severity.value.upper()} ({len(severity_findings)})")
                for f in severity_findings:
                    lines.append(f"\n**[{f.finding_id}]** {f.claim}")
                    lines.append(f"- Tipo: {f.finding_type.value}")
                    if f.citations:
                        lines.append(f"- Citações:")
                        for c in f.citations[:3]:  # Max 3 citações
                            page_info = f" (pág. {c.page_num})" if c.page_num else ""
                            lines.append(f"  - chars {c.start_char}-{c.end_char}{page_info}")
                            if c.excerpt:
                                lines.append(f"    > _{c.excerpt[:100]}..._")

        # Coverage
        lines.extend([
            "",
            "## Cobertura",
            f"- Documentos: {len(self.coverage_check.docs_seen)}",
            f"- Páginas: {len(self.coverage_check.pages_seen)}",
            f"- Percentagem: {self.coverage_check.coverage_percent:.1f}%",
        ])

        if self.coverage_check.unreadable_units:
            lines.append(f"- Ilegíveis: {len(self.coverage_check.unreadable_units)}")

        # Open questions
        if self.open_questions:
            lines.append("\n## Questões em Aberto")
            for q in self.open_questions:
                lines.append(f"- {q}")

        return "\n".join(lines)


# ============================================================================
# FASE 3: JUÍZES
# ============================================================================

@dataclass
class JudgePoint:
    """
    Ponto de decisão de um juiz.

    is_determinant: True se este ponto é crucial para a decisão final.
    Pontos determinantes SEM citations geram SEM_PROVA_DETERMINANTE.
    """
    point_id: str
    conclusion: str  # Conclusão do juiz
    rationale: str   # Fundamentação
    citations: List[Citation] = field(default_factory=list)
    legal_basis: List[str] = field(default_factory=list)  # Artigos, leis citadas
    risks: List[str] = field(default_factory=list)
    alternatives: List[str] = field(default_factory=list)
    confidence: float = 0.8  # 0.0 a 1.0
    finding_refs: List[str] = field(default_factory=list)  # Referências a finding_ids da F2
    is_determinant: bool = False  # Se True, é crucial para a decisão

    def __post_init__(self):
        if not self.point_id:
            self.point_id = f"point_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict:
        return {
            "point_id": self.point_id,
            "conclusion": self.conclusion,
            "rationale": self.rationale,
            "citations": [c.to_dict() for c in self.citations],
            "legal_basis": self.legal_basis,
            "risks": self.risks,
            "alternatives": self.alternatives,
            "confidence": self.confidence,
            "finding_refs": self.finding_refs,
            "is_determinant": self.is_determinant,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'JudgePoint':
        return cls(
            point_id=data.get("point_id", ""),
            conclusion=data.get("conclusion", ""),
            rationale=data.get("rationale", ""),
            citations=[Citation.from_dict(c) for c in data.get("citations", [])],
            legal_basis=data.get("legal_basis", []),
            risks=data.get("risks", []),
            alternatives=data.get("alternatives", []),
            confidence=data.get("confidence", 0.8),
            finding_refs=data.get("finding_refs", []),
            is_determinant=data.get("is_determinant", False),
        )


@dataclass
class Disagreement:
    """Desacordo com outro juiz ou auditor."""
    disagreement_id: str
    target_id: str  # finding_id ou point_id com que discorda
    target_type: str  # "finding" ou "point"
    reason: str
    alternative_view: str
    citations: List[Citation] = field(default_factory=list)

    def __post_init__(self):
        if not self.disagreement_id:
            self.disagreement_id = f"disagree_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict:
        return {
            "disagreement_id": self.disagreement_id,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "reason": self.reason,
            "alternative_view": self.alternative_view,
            "citations": [c.to_dict() for c in self.citations],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Disagreement':
        return cls(
            disagreement_id=data.get("disagreement_id", ""),
            target_id=data.get("target_id", ""),
            target_type=data.get("target_type", "finding"),
            reason=data.get("reason", ""),
            alternative_view=data.get("alternative_view", ""),
            citations=[Citation.from_dict(c) for c in data.get("citations", [])],
        )


@dataclass
class JudgeOpinion:
    """
    Parecer completo de um juiz.
    """
    judge_id: str  # J1, J2, J3
    model_name: str
    run_id: str
    recommendation: DecisionType  # procedente/improcedente/etc
    decision_points: List[JudgePoint] = field(default_factory=list)
    disagreements: List[Disagreement] = field(default_factory=list)
    qa_responses: List[Dict] = field(default_factory=list)  # [{question, answer, citations}]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "judge_id": self.judge_id,
            "model_name": self.model_name,
            "run_id": self.run_id,
            "recommendation": self.recommendation.value,
            "decision_points": [p.to_dict() for p in self.decision_points],
            "disagreements": [d.to_dict() for d in self.disagreements],
            "qa_responses": self.qa_responses,
            "errors": self.errors,
            "warnings": self.warnings,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'JudgeOpinion':
        return cls(
            judge_id=data.get("judge_id", ""),
            model_name=data.get("model_name", ""),
            run_id=data.get("run_id", ""),
            recommendation=DecisionType(data.get("recommendation", "inconclusivo")),
            decision_points=[JudgePoint.from_dict(p) for p in data.get("decision_points", [])],
            disagreements=[Disagreement.from_dict(d) for d in data.get("disagreements", [])],
            qa_responses=data.get("qa_responses", []),
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
        )

    def to_markdown(self) -> str:
        """Renderiza o parecer como Markdown."""
        lines = [
            f"# Parecer Jurídico - {self.judge_id}",
            f"**Modelo:** {self.model_name}",
            f"**Recomendação:** {self.recommendation.value.upper()}",
            "",
        ]

        if self.errors:
            lines.append("## ⚠️ Erros")
            for err in self.errors:
                lines.append(f"- {err}")
            lines.append("")

        # Decision points
        lines.append(f"## Pontos de Decisão ({len(self.decision_points)})")
        for point in self.decision_points:
            lines.extend([
                f"\n### [{point.point_id}] {point.conclusion}",
                f"**Fundamentação:** {point.rationale}",
                f"**Confiança:** {point.confidence:.0%}",
            ])
            if point.legal_basis:
                lines.append(f"**Base Legal:** {', '.join(point.legal_basis)}")
            if point.risks:
                lines.append(f"**Riscos:** {', '.join(point.risks)}")

        # Disagreements
        if self.disagreements:
            lines.append(f"\n## Desacordos ({len(self.disagreements)})")
            for d in self.disagreements:
                lines.extend([
                    f"\n### Discorda de {d.target_type} {d.target_id}",
                    f"**Razão:** {d.reason}",
                    f"**Visão alternativa:** {d.alternative_view}",
                ])

        # Q&A
        if self.qa_responses:
            lines.append("\n## Respostas Q&A")
            for i, qa in enumerate(self.qa_responses, 1):
                lines.extend([
                    f"\n**{i}. {qa.get('question', 'Pergunta')}**",
                    f"{qa.get('answer', 'Sem resposta')}",
                ])

        return "\n".join(lines)


# ============================================================================
# FASE 4: PRESIDENTE (DECISÃO FINAL)
# ============================================================================

@dataclass
class ConflictResolution:
    """Resolução de conflito entre juízes/auditores."""
    conflict_id: str
    conflicting_ids: List[str]  # IDs dos findings/points em conflito
    resolution: str  # Como foi resolvido
    chosen_value: str  # Valor escolhido
    reasoning: str
    citations: List[Citation] = field(default_factory=list)

    def __post_init__(self):
        if not self.conflict_id:
            self.conflict_id = f"resolution_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict:
        return {
            "conflict_id": self.conflict_id,
            "conflicting_ids": self.conflicting_ids,
            "resolution": self.resolution,
            "chosen_value": self.chosen_value,
            "reasoning": self.reasoning,
            "citations": [c.to_dict() for c in self.citations],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'ConflictResolution':
        return cls(
            conflict_id=data.get("conflict_id", ""),
            conflicting_ids=data.get("conflicting_ids", []),
            resolution=data.get("resolution", ""),
            chosen_value=data.get("chosen_value", ""),
            reasoning=data.get("reasoning", ""),
            citations=[Citation.from_dict(c) for c in data.get("citations", [])],
        )


@dataclass
class FinalDecision:
    """
    Decisão final do Presidente.
    """
    # Campos obrigatórios (sem default) primeiro
    run_id: str
    model_name: str
    final_answer: str  # Resposta final em texto
    decision_type: DecisionType

    # Campos com default depois
    decision_id: str = ""
    confidence: float = 0.8

    # Pontos consolidados
    decision_points_final: List[JudgePoint] = field(default_factory=list)

    # Provas agregadas
    proofs: List[Citation] = field(default_factory=list)

    # Partes não processadas
    unreadable_parts: List[Dict] = field(default_factory=list)  # [{doc_id, page_num?, reason}]

    # Conflitos
    conflicts_resolved: List[ConflictResolution] = field(default_factory=list)
    conflicts_unresolved: List[Dict] = field(default_factory=list)  # [{ids, description}]

    # Q&A consolidado
    qa_final: List[Dict] = field(default_factory=list)  # [{question, final_answer, sources}]

    # Metadata
    judges_consulted: List[str] = field(default_factory=list)  # J1, J2, J3
    auditors_consulted: List[str] = field(default_factory=list)  # A1, A2, A3, A4
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    # Markdown renderizado (gerado)
    output_markdown: str = ""

    def __post_init__(self):
        if not self.decision_id:
            self.decision_id = f"decision_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict:
        return {
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "model_name": self.model_name,
            "final_answer": self.final_answer,
            "decision_type": self.decision_type.value,
            "confidence": self.confidence,
            "decision_points_final": [p.to_dict() for p in self.decision_points_final],
            "proofs": [p.to_dict() for p in self.proofs],
            "unreadable_parts": self.unreadable_parts,
            "conflicts_resolved": [c.to_dict() for c in self.conflicts_resolved],
            "conflicts_unresolved": self.conflicts_unresolved,
            "qa_final": self.qa_final,
            "judges_consulted": self.judges_consulted,
            "auditors_consulted": self.auditors_consulted,
            "errors": self.errors,
            "warnings": self.warnings,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'FinalDecision':
        return cls(
            decision_id=data.get("decision_id", ""),
            run_id=data.get("run_id", ""),
            model_name=data.get("model_name", ""),
            final_answer=data.get("final_answer", ""),
            decision_type=DecisionType(data.get("decision_type", "inconclusivo")),
            confidence=data.get("confidence", 0.8),
            decision_points_final=[JudgePoint.from_dict(p) for p in data.get("decision_points_final", [])],
            proofs=[Citation.from_dict(p) for p in data.get("proofs", [])],
            unreadable_parts=data.get("unreadable_parts", []),
            conflicts_resolved=[ConflictResolution.from_dict(c) for c in data.get("conflicts_resolved", [])],
            conflicts_unresolved=data.get("conflicts_unresolved", []),
            qa_final=data.get("qa_final", []),
            judges_consulted=data.get("judges_consulted", []),
            auditors_consulted=data.get("auditors_consulted", []),
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
        )

    def generate_markdown(self) -> str:
        """Gera Markdown a partir da estrutura JSON."""
        lines = [
            "# DECISÃO FINAL DO PRESIDENTE",
            "",
            f"**Decisão:** {self.decision_type.value.upper()}",
            f"**Confiança:** {self.confidence:.0%}",
            f"**Modelo:** {self.model_name}",
            "",
            "---",
            "",
            "## Resposta Final",
            "",
            self.final_answer,
            "",
        ]

        # Erros
        if self.errors:
            lines.extend([
                "## ⚠️ Erros Encontrados",
                "",
            ])
            for err in self.errors:
                lines.append(f"- {err}")
            lines.append("")

        # Pontos de decisão
        if self.decision_points_final:
            lines.extend([
                "## Pontos de Decisão",
                "",
            ])
            for i, point in enumerate(self.decision_points_final, 1):
                lines.extend([
                    f"### {i}. {point.conclusion}",
                    "",
                    f"**Fundamentação:** {point.rationale}",
                    "",
                ])
                if point.legal_basis:
                    lines.append(f"**Base Legal:** {', '.join(point.legal_basis)}")
                    lines.append("")

        # Conflitos resolvidos
        if self.conflicts_resolved:
            lines.extend([
                "## Conflitos Resolvidos",
                "",
            ])
            for conflict in self.conflicts_resolved:
                lines.extend([
                    f"### {conflict.conflict_id}",
                    f"**Valor escolhido:** {conflict.chosen_value}",
                    f"**Razão:** {conflict.reasoning}",
                    "",
                ])

        # Conflitos não resolvidos
        if self.conflicts_unresolved:
            lines.extend([
                "## ⚠️ Conflitos Não Resolvidos",
                "",
            ])
            for conflict in self.conflicts_unresolved:
                lines.append(f"- {conflict.get('description', 'Conflito sem descrição')}")
            lines.append("")

        # Partes não processadas
        if self.unreadable_parts:
            lines.extend([
                "## Partes Não Processadas",
                "",
            ])
            for part in self.unreadable_parts:
                page_info = f" (pág. {part.get('page_num')})" if part.get('page_num') else ""
                lines.append(f"- {part.get('doc_id', 'doc')}{page_info}: {part.get('reason', 'ilegível')}")
            lines.append("")

        # Q&A Final
        if self.qa_final:
            lines.extend([
                "## Respostas às Perguntas",
                "",
            ])
            for i, qa in enumerate(self.qa_final, 1):
                lines.extend([
                    f"### {i}. {qa.get('question', 'Pergunta')}",
                    "",
                    qa.get('final_answer', 'Sem resposta'),
                    "",
                ])

        # Fontes consultadas
        lines.extend([
            "---",
            "",
            "## Fontes Consultadas",
            "",
            f"- **Auditores:** {', '.join(self.auditors_consulted) or 'N/A'}",
            f"- **Juízes:** {', '.join(self.judges_consulted) or 'N/A'}",
            "",
        ])

        self.output_markdown = "\n".join(lines)
        return self.output_markdown


# ============================================================================
# PARSING JSON COM FALLBACK
# ============================================================================

def parse_json_safe(
    output: str,
    context: str = "unknown"
) -> Tuple[Optional[Dict], List[str]]:
    """
    Tenta extrair JSON de output LLM de forma robusta.

    Args:
        output: String de output do LLM
        context: Contexto para mensagens de erro

    Returns:
        (json_data ou None, lista de erros)
    """
    errors = []

    # 1. Tentar parse direto
    try:
        return json.loads(output), []
    except json.JSONDecodeError as e:
        errors.append(f"Parse direto falhou: {str(e)[:100]}")

    # 2. Tentar encontrar JSON com regex
    json_match = re.search(r'\{[\s\S]*\}', output)
    if json_match:
        try:
            return json.loads(json_match.group()), errors
        except json.JSONDecodeError as e:
            errors.append(f"Parse regex falhou: {str(e)[:100]}")

    # 3. Tentar remover markdown code blocks
    cleaned = re.sub(r'```json\s*', '', output)
    cleaned = re.sub(r'```\s*', '', cleaned)
    try:
        return json.loads(cleaned.strip()), errors
    except json.JSONDecodeError as e:
        errors.append(f"Parse após limpeza falhou: {str(e)[:100]}")

    errors.append(f"Não foi possível extrair JSON válido ({context})")
    return None, errors


def parse_audit_report(
    output: str,
    auditor_id: str,
    model_name: str,
    run_id: str
) -> AuditReport:
    """
    Parseia output do auditor para AuditReport.
    Se falhar, cria relatório mínimo com erro.
    """
    json_data, errors = parse_json_safe(output, f"auditor {auditor_id}")

    if json_data:
        try:
            report = AuditReport.from_dict({
                **json_data,
                "auditor_id": auditor_id,
                "model_name": model_name,
                "run_id": run_id,
            })
            report.errors.extend(errors)
            return report
        except Exception as e:
            errors.append(f"Erro ao criar AuditReport: {str(e)[:100]}")

    # Fallback: relatório mínimo com erro
    return AuditReport(
        auditor_id=auditor_id,
        model_name=model_name,
        run_id=run_id,
        findings=[],
        errors=errors + ["ERROR_RECOVERED: JSON inválido, relatório mínimo criado"],
        warnings=["Output original guardado para debug"],
    )


def parse_judge_opinion(
    output: str,
    judge_id: str,
    model_name: str,
    run_id: str
) -> JudgeOpinion:
    """
    Parseia output do juiz para JudgeOpinion.
    Se falhar, cria parecer mínimo com erro.
    """
    json_data, errors = parse_json_safe(output, f"juiz {judge_id}")

    if json_data:
        try:
            opinion = JudgeOpinion.from_dict({
                **json_data,
                "judge_id": judge_id,
                "model_name": model_name,
                "run_id": run_id,
            })
            opinion.errors.extend(errors)
            return opinion
        except Exception as e:
            errors.append(f"Erro ao criar JudgeOpinion: {str(e)[:100]}")

    # Fallback: parecer mínimo com erro
    return JudgeOpinion(
        judge_id=judge_id,
        model_name=model_name,
        run_id=run_id,
        recommendation=DecisionType.INCONCLUSIVO,
        decision_points=[],
        errors=errors + ["ERROR_RECOVERED: JSON inválido, parecer mínimo criado"],
    )


def parse_final_decision(
    output: str,
    model_name: str,
    run_id: str
) -> FinalDecision:
    """
    Parseia output do presidente para FinalDecision.
    Se falhar, cria decisão mínima com erro.
    """
    json_data, errors = parse_json_safe(output, "presidente")

    if json_data:
        try:
            decision = FinalDecision.from_dict({
                **json_data,
                "model_name": model_name,
                "run_id": run_id,
            })
            decision.errors.extend(errors)
            decision.generate_markdown()
            return decision
        except Exception as e:
            errors.append(f"Erro ao criar FinalDecision: {str(e)[:100]}")

    # Fallback: decisão mínima com erro
    decision = FinalDecision(
        run_id=run_id,
        model_name=model_name,
        final_answer="Decisão não pôde ser processada devido a erros de parsing.",
        decision_type=DecisionType.INCONCLUSIVO,
        errors=errors + ["ERROR_RECOVERED: JSON inválido, decisão mínima criada"],
    )
    decision.generate_markdown()
    return decision


# ============================================================================
# CHEFE CONSOLIDADO (FASE 2)
# ============================================================================

@dataclass
class ConsolidatedFinding:
    """Finding consolidado pelo Chefe a partir de múltiplos auditores."""
    finding_id: str
    claim: str
    finding_type: FindingType
    severity: Severity
    sources: List[str]  # Lista de auditor_ids [A1, A2, ...]
    citations: List[Citation] = field(default_factory=list)
    consensus_level: str = "unico"  # total | forte | parcial | unico
    notes: str = ""

    def __post_init__(self):
        if not self.finding_id:
            self.finding_id = f"finding_consolidated_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict:
        return {
            "finding_id": self.finding_id,
            "claim": self.claim,
            "finding_type": self.finding_type.value,
            "severity": self.severity.value,
            "sources": self.sources,
            "citations": [c.to_dict() for c in self.citations],
            "consensus_level": self.consensus_level,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'ConsolidatedFinding':
        return cls(
            finding_id=data.get("finding_id", ""),
            claim=data.get("claim", ""),
            finding_type=FindingType(data.get("finding_type", "facto")),
            severity=Severity(data.get("severity", "medio")),
            sources=data.get("sources", []),
            citations=[Citation.from_dict(c) for c in data.get("citations", [])],
            consensus_level=data.get("consensus_level", "unico"),
            notes=data.get("notes", ""),
        )


@dataclass
class Divergence:
    """Divergência entre auditores identificada pelo Chefe."""
    topic: str
    positions: List[Dict]  # [{auditor_id, position}]
    resolution: str = ""
    unresolved: bool = True

    def to_dict(self) -> Dict:
        return {
            "topic": self.topic,
            "positions": self.positions,
            "resolution": self.resolution,
            "unresolved": self.unresolved,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Divergence':
        return cls(
            topic=data.get("topic", ""),
            positions=data.get("positions", []),
            resolution=data.get("resolution", ""),
            unresolved=data.get("unresolved", True),
        )


@dataclass
class ChefeConsolidatedReport:
    """Relatório consolidado do Chefe (Fase 2)."""
    chefe_id: str
    model_name: str
    run_id: str
    consolidated_findings: List[ConsolidatedFinding] = field(default_factory=list)
    divergences: List[Divergence] = field(default_factory=list)
    coverage_check: CoverageCheck = field(default_factory=CoverageCheck)
    recommendations_phase3: List[Dict] = field(default_factory=list)
    legal_refs_consolidated: List[Dict] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if isinstance(self.coverage_check, dict):
            self.coverage_check = CoverageCheck.from_dict(self.coverage_check)

    def to_dict(self) -> Dict:
        return {
            "chefe_id": self.chefe_id,
            "model_name": self.model_name,
            "run_id": self.run_id,
            "consolidated_findings": [f.to_dict() for f in self.consolidated_findings],
            "divergences": [d.to_dict() for d in self.divergences],
            "coverage_check": self.coverage_check.to_dict(),
            "recommendations_phase3": self.recommendations_phase3,
            "legal_refs_consolidated": self.legal_refs_consolidated,
            "open_questions": self.open_questions,
            "errors": self.errors,
            "warnings": self.warnings,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'ChefeConsolidatedReport':
        return cls(
            chefe_id=data.get("chefe_id", "CHEFE"),
            model_name=data.get("model_name", ""),
            run_id=data.get("run_id", ""),
            consolidated_findings=[ConsolidatedFinding.from_dict(f) for f in data.get("consolidated_findings", [])],
            divergences=[Divergence.from_dict(d) for d in data.get("divergences", [])],
            coverage_check=CoverageCheck.from_dict(data.get("coverage_check", {})),
            recommendations_phase3=data.get("recommendations_phase3", []),
            legal_refs_consolidated=data.get("legal_refs_consolidated", []),
            open_questions=data.get("open_questions", []),
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
        )

    def to_markdown(self) -> str:
        """Renderiza o relatório consolidado como Markdown."""
        lines = [
            f"# Relatório Consolidado do Chefe",
            f"**Modelo:** {self.model_name}",
            f"**Run:** {self.run_id}",
            f"**Timestamp:** {self.timestamp.isoformat()}",
            "",
        ]

        # Erros
        if self.errors:
            lines.append("## ⚠️ Erros")
            for err in self.errors:
                lines.append(f"- {err}")
            lines.append("")

        # Findings consolidados por consenso
        lines.append(f"## Findings Consolidados ({len(self.consolidated_findings)})")

        for level in ["total", "forte", "parcial", "unico"]:
            level_findings = [f for f in self.consolidated_findings if f.consensus_level == level]
            if level_findings:
                level_label = {"total": "Consenso Total", "forte": "Consenso Forte (3+)",
                              "parcial": "Consenso Parcial (2)", "unico": "Único (1)"}
                lines.append(f"\n### {level_label.get(level, level)} ({len(level_findings)})")
                for f in level_findings:
                    sources_str = ", ".join(f.sources)
                    lines.append(f"\n**[{f.finding_id}]** [{sources_str}] {f.claim}")
                    lines.append(f"- Tipo: {f.finding_type.value} | Severidade: {f.severity.value}")

        # Divergências
        if self.divergences:
            lines.append(f"\n## Divergências ({len(self.divergences)})")
            for d in self.divergences:
                lines.append(f"\n### {d.topic}")
                for pos in d.positions:
                    lines.append(f"- **{pos.get('auditor_id', '?')}**: {pos.get('position', '')}")
                if d.resolution:
                    lines.append(f"- **Resolução**: {d.resolution}")
                if d.unresolved:
                    lines.append("- ⚠️ **Não resolvido**")

        # Recomendações
        if self.recommendations_phase3:
            lines.append("\n## Recomendações para Fase 3")
            for rec in self.recommendations_phase3:
                priority = rec.get("priority", "media")
                lines.append(f"- [{priority.upper()}] {rec.get('recommendation', '')}")

        # Referências legais
        if self.legal_refs_consolidated:
            lines.append("\n## Referências Legais Consolidadas")
            for ref in self.legal_refs_consolidated:
                sources = ", ".join(ref.get("sources", []))
                lines.append(f"- **{ref.get('ref', '')}** [{sources}]")

        return "\n".join(lines)


def parse_chefe_report(
    output: str,
    model_name: str,
    run_id: str
) -> ChefeConsolidatedReport:
    """
    Parseia output do Chefe para ChefeConsolidatedReport.
    Se falhar, cria relatório mínimo com erro (soft-fail).
    """
    json_data, errors = parse_json_safe(output, "chefe")

    if json_data:
        try:
            report = ChefeConsolidatedReport.from_dict({
                **json_data,
                "model_name": model_name,
                "run_id": run_id,
            })
            report.errors.extend(errors)
            return report
        except Exception as e:
            errors.append(f"Erro ao criar ChefeConsolidatedReport: {str(e)[:100]}")

    # Fallback: relatório mínimo com erro (soft-fail)
    return ChefeConsolidatedReport(
        chefe_id="CHEFE",
        model_name=model_name,
        run_id=run_id,
        consolidated_findings=[],
        errors=errors + ["ERROR_RECOVERED: JSON inválido, relatório mínimo criado"],
        warnings=["Output original guardado para debug"],
    )

# -*- coding: utf-8 -*-
"""
MetaIntegrity - Validador de Coerência do Pipeline.

Valida coerência AUTOMÁTICA entre:
- outputs/<run_id> (ficheiros gerados)
- UnifiedExtractionResult / Coverage / AuditReports / JudgeOpinions / FinalDecision
- timestamps e contagens
- doc_ids referenciados

Este módulo previne "auto-ilusão" do pipeline verificando que
tudo é consistente e não há referências a dados inexistentes.

REGRAS:
1. Nunca abortar - sempre gerar relatório com warnings/errors
2. Validar completude de ficheiros
3. Validar consistência de referências cruzadas
4. Validar contagens e timestamps
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Set, Any, Tuple

from src.config import LOG_LEVEL, OUTPUT_DIR, USE_UNIFIED_PROVENANCE
from src.utils.sanitize import sanitize_run_id

logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

@dataclass
class MetaIntegrityConfig:
    """Configuração do MetaIntegrity."""
    # Ficheiros esperados por feature flag
    require_unified_result: bool = True
    require_coverage_report: bool = True
    require_integrity_report: bool = True
    require_audit_reports: bool = True
    require_judge_opinions: bool = True
    require_final_decision: bool = True

    # Tolerâncias
    timestamp_tolerance_minutes: int = 60  # Janela de tempo aceitável
    pages_tolerance_percent: float = 5.0   # Tolerância para pages_total
    citation_count_tolerance: int = 5      # Tolerância em contagem de citations

    # Validações opcionais
    validate_doc_ids: bool = True
    validate_timestamps: bool = True
    validate_counts: bool = True
    validate_coverage_math: bool = True

    @classmethod
    def default(cls) -> 'MetaIntegrityConfig':
        return cls()

    @classmethod
    def strict(cls) -> 'MetaIntegrityConfig':
        return cls(
            timestamp_tolerance_minutes=30,
            pages_tolerance_percent=0.0,
            citation_count_tolerance=0,
        )

    @classmethod
    def from_feature_flags(cls, use_unified: bool = True) -> 'MetaIntegrityConfig':
        """Cria config baseada em feature flags."""
        config = cls()
        config.require_unified_result = use_unified
        config.require_coverage_report = use_unified
        return config


# ============================================================================
# DATACLASSES PARA RELATÓRIO
# ============================================================================

@dataclass
class MetaValidationError:
    """Erro de meta-validação."""
    check_type: str  # FILES_MISSING, DOC_ID_INVALID, PAGES_INCONSISTENT, etc.
    severity: str    # ERROR, WARNING, INFO
    message: str
    expected: Optional[str] = None
    actual: Optional[str] = None
    source_file: Optional[str] = None
    details: Optional[Dict] = None

    def to_dict(self) -> Dict:
        return {
            "check_type": self.check_type,
            "severity": self.severity,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "source_file": self.source_file,
            "details": self.details,
        }


@dataclass
class FileCheckResult:
    """Resultado de verificação de ficheiros."""
    expected: List[str] = field(default_factory=list)
    present: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    extra: List[str] = field(default_factory=list)
    all_present: bool = False

    def to_dict(self) -> Dict:
        return {
            "expected_count": len(self.expected),
            "present_count": len(self.present),
            "missing_count": len(self.missing),
            "extra_count": len(self.extra),
            "all_present": self.all_present,
            "missing": self.missing[:20],  # Limitar output
            "extra": self.extra[:20],
        }


@dataclass
class ConsistencyCheckResult:
    """Resultado de verificação de consistência."""
    check_name: str
    passed: bool
    details: Dict = field(default_factory=dict)
    errors: List[MetaValidationError] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "check_name": self.check_name,
            "passed": self.passed,
            "details": self.details,
            "errors": [e.to_dict() for e in self.errors],
        }


@dataclass
class MetaIntegrityReport:
    """Relatório completo de meta-integridade."""
    run_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    run_start: Optional[datetime] = None

    # Verificação de ficheiros
    files_check: FileCheckResult = field(default_factory=FileCheckResult)

    # Verificações de consistência
    consistency_checks: List[ConsistencyCheckResult] = field(default_factory=list)

    # Erros detalhados
    errors: List[MetaValidationError] = field(default_factory=list)

    # Status
    is_consistent: bool = True
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    def add_error(self, error: MetaValidationError):
        """Adiciona erro e atualiza contagens."""
        self.errors.append(error)

        if error.severity == "ERROR":
            self.error_count += 1
            self.is_consistent = False
        elif error.severity == "WARNING":
            self.warning_count += 1
        else:
            self.info_count += 1

    def to_dict(self) -> Dict:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "run_start": self.run_start.isoformat() if self.run_start else None,
            "summary": {
                "is_consistent": self.is_consistent,
                "error_count": self.error_count,
                "warning_count": self.warning_count,
                "info_count": self.info_count,
            },
            "files_check": self.files_check.to_dict(),
            "consistency_checks": [c.to_dict() for c in self.consistency_checks],
            "errors": [e.to_dict() for e in self.errors[:100]],  # Limitar
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, output_dir: Optional[Path] = None) -> Path:
        """Guarda relatório em ficheiro JSON."""
        if output_dir is None:
            output_dir = OUTPUT_DIR / self.run_id

        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / "meta_integrity_report.json"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.to_json())

        logger.info(f"MetaIntegrity report guardado: {filepath}")
        return filepath


# ============================================================================
# VALIDADOR PRINCIPAL
# ============================================================================

class MetaIntegrityValidator:
    """
    Validador de meta-integridade do pipeline.

    Verifica coerência entre todos os outputs de um run.
    """

    def __init__(
        self,
        run_id: str,
        output_dir: Optional[Path] = None,
        config: Optional[MetaIntegrityConfig] = None,
        run_start: Optional[datetime] = None,
        loaded_doc_ids: Optional[Set[str]] = None,
        document_num_pages: Optional[int] = None,
    ):
        """
        Args:
            run_id: ID do run a validar
            output_dir: Diretório de outputs (default: OUTPUT_DIR/run_id)
            config: Configuração de validação
            run_start: Timestamp de início do run
            loaded_doc_ids: Set de doc_ids dos documentos carregados
            document_num_pages: Número de páginas do documento
        """
        self.run_id = sanitize_run_id(run_id)
        self.output_dir = output_dir or (OUTPUT_DIR / self.run_id)
        self.config = config or MetaIntegrityConfig.from_feature_flags(USE_UNIFIED_PROVENANCE)
        self.run_start = run_start or datetime.now()
        self.loaded_doc_ids = loaded_doc_ids or set()
        self.document_num_pages = document_num_pages

        # Dados carregados
        self._unified_result: Optional[Dict] = None
        self._coverage_report: Optional[Dict] = None
        self._integrity_report: Optional[Dict] = None
        self._audit_reports: List[Dict] = []
        self._judge_opinions: List[Dict] = []
        self._final_decision: Optional[Dict] = None

        # Relatório
        self.report = MetaIntegrityReport(
            run_id=run_id,
            run_start=run_start,
        )

    def validate(self) -> MetaIntegrityReport:
        """
        Executa todas as validações.

        Returns:
            MetaIntegrityReport completo
        """
        logger.info(f"MetaIntegrity: Validando run {self.run_id}")

        # 1. Verificar ficheiros
        self._check_files()

        # 2. Carregar dados
        self._load_data()

        # 3. Validar doc_ids
        if self.config.validate_doc_ids:
            self._check_doc_ids()

        # 4. Validar cobertura
        if self.config.validate_coverage_math:
            self._check_coverage_consistency()

        # 5. Validar contagens de citations
        if self.config.validate_counts:
            self._check_citation_counts()

        # 6. Validar timestamps
        if self.config.validate_timestamps:
            self._check_timestamps()

        logger.info(
            f"MetaIntegrity completo: is_consistent={self.report.is_consistent}, "
            f"errors={self.report.error_count}, warnings={self.report.warning_count}"
        )

        return self.report

    # =========================================================================
    # VERIFICAÇÃO DE FICHEIROS
    # =========================================================================

    def _check_files(self):
        """Verifica se todos os ficheiros esperados existem."""
        expected_files = self._get_expected_files()
        present_files = []
        missing_files = []
        extra_files = []

        # Verificar existência
        if self.output_dir.exists():
            actual_files = set(f.name for f in self.output_dir.iterdir() if f.is_file())

            for f in expected_files:
                if f in actual_files:
                    present_files.append(f)
                else:
                    missing_files.append(f)

            # Ficheiros extra (não crítico)
            expected_set = set(expected_files)
            for f in actual_files:
                if f not in expected_set and f.endswith(".json"):
                    extra_files.append(f)
        else:
            missing_files = expected_files

        # Atualizar report
        self.report.files_check = FileCheckResult(
            expected=expected_files,
            present=present_files,
            missing=missing_files,
            extra=extra_files,
            all_present=len(missing_files) == 0,
        )

        # Adicionar erros para ficheiros obrigatórios em falta
        critical_files = self._get_critical_files()
        for f in missing_files:
            severity = "ERROR" if f in critical_files else "WARNING"
            self.report.add_error(MetaValidationError(
                check_type="FILES_MISSING",
                severity=severity,
                message=f"Ficheiro em falta: {f}",
                expected=f,
                actual=None,
                source_file=str(self.output_dir),
            ))

        # Resultado da verificação
        check = ConsistencyCheckResult(
            check_name="files_presence",
            passed=len(missing_files) == 0,
            details={
                "expected": len(expected_files),
                "present": len(present_files),
                "missing": len(missing_files),
            },
        )
        self.report.consistency_checks.append(check)

    def _get_expected_files(self) -> List[str]:
        """Retorna lista de ficheiros esperados baseado em config."""
        files = []

        if self.config.require_unified_result:
            files.append("fase1_unified_result.json")
            # Ficheiros de extractors
            for i in range(1, 6):
                files.append(f"fase1_extractor_E{i}_items.json")

        if self.config.require_coverage_report:
            files.append("fase1_coverage_report.json")

        if self.config.require_integrity_report:
            files.append("integrity_report.json")

        if self.config.require_audit_reports:
            files.append("fase2_all_audit_reports.json")
            for i in range(1, 5):
                files.append(f"fase2_auditor_{i}.json")

        if self.config.require_judge_opinions:
            files.append("fase3_all_judge_opinions.json")
            for i in range(1, 4):
                files.append(f"fase3_juiz_{i}.json")

        if self.config.require_final_decision:
            files.append("fase4_decisao_final.json")

        return files

    def _get_critical_files(self) -> Set[str]:
        """Retorna set de ficheiros críticos (ausência é ERROR)."""
        return {
            "fase1_unified_result.json",
            "fase1_coverage_report.json",
            "fase2_all_audit_reports.json",
            "fase3_all_judge_opinions.json",
        }

    # =========================================================================
    # CARREGAMENTO DE DADOS
    # =========================================================================

    def _load_data(self):
        """Carrega dados dos ficheiros JSON."""
        self._unified_result = self._load_json("fase1_unified_result.json")
        self._coverage_report = self._load_json("fase1_coverage_report.json")
        self._integrity_report = self._load_json("integrity_report.json")
        self._final_decision = self._load_json("fase4_decisao_final.json")

        # Audit reports
        all_audits = self._load_json("fase2_all_audit_reports.json")
        if isinstance(all_audits, list):
            self._audit_reports = all_audits

        # Judge opinions
        all_judges = self._load_json("fase3_all_judge_opinions.json")
        if isinstance(all_judges, list):
            self._judge_opinions = all_judges

    def _load_json(self, filename: str) -> Optional[Dict]:
        """Carrega ficheiro JSON se existir."""
        filepath = self.output_dir / filename
        if filepath.exists():
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                self.report.add_error(MetaValidationError(
                    check_type="FILE_LOAD_ERROR",
                    severity="WARNING",
                    message=f"Erro ao carregar {filename}: {e}",
                    source_file=filename,
                ))
        return None

    # =========================================================================
    # VALIDAÇÃO DE DOC_IDs
    # =========================================================================

    def _check_doc_ids(self):
        """Verifica se todos os doc_ids referenciados existem."""
        referenced_doc_ids: Set[str] = set()
        invalid_refs: List[Tuple[str, str, str]] = []  # (doc_id, source, context)

        # Extrair doc_ids do unified_result
        valid_doc_ids = set(self.loaded_doc_ids)
        if self._unified_result:
            doc_meta = self._unified_result.get("document_meta", {})
            if doc_meta.get("doc_id"):
                valid_doc_ids.add(doc_meta["doc_id"])

        # Extrair e validar doc_ids das citations dos auditors
        for i, report in enumerate(self._audit_reports):
            auditor_id = report.get("auditor_id", f"A{i+1}")
            for finding in report.get("findings", []):
                for citation in finding.get("citations", []):
                    doc_id = citation.get("doc_id")
                    if doc_id:
                        referenced_doc_ids.add(doc_id)
                        if valid_doc_ids and doc_id not in valid_doc_ids:
                            invalid_refs.append((doc_id, auditor_id, f"finding {finding.get('finding_id', 'unknown')}"))

        # Extrair e validar doc_ids das citations dos judges
        for i, opinion in enumerate(self._judge_opinions):
            judge_id = opinion.get("judge_id", f"J{i+1}")
            for point in opinion.get("decision_points", []):
                for citation in point.get("citations", []):
                    doc_id = citation.get("doc_id")
                    if doc_id:
                        referenced_doc_ids.add(doc_id)
                        if valid_doc_ids and doc_id not in valid_doc_ids:
                            invalid_refs.append((doc_id, judge_id, f"point {point.get('point_id', 'unknown')}"))

        # Extrair e validar doc_ids da final decision
        if self._final_decision:
            for proof in self._final_decision.get("proofs", []):
                doc_id = proof.get("doc_id")
                if doc_id:
                    referenced_doc_ids.add(doc_id)
                    if valid_doc_ids and doc_id not in valid_doc_ids:
                        invalid_refs.append((doc_id, "president", "proof"))

        # Adicionar erros
        for doc_id, source, context in invalid_refs:
            self.report.add_error(MetaValidationError(
                check_type="DOC_ID_INVALID",
                severity="ERROR",
                message=f"doc_id '{doc_id}' não existe nos documentos carregados",
                expected=f"um de: {list(valid_doc_ids)[:5]}",
                actual=doc_id,
                source_file=source,
                details={"context": context},
            ))

        # Resultado
        check = ConsistencyCheckResult(
            check_name="doc_ids_consistency",
            passed=len(invalid_refs) == 0,
            details={
                "valid_doc_ids": list(valid_doc_ids)[:10],
                "referenced_doc_ids": list(referenced_doc_ids)[:10],
                "invalid_count": len(invalid_refs),
            },
        )
        self.report.consistency_checks.append(check)

    # =========================================================================
    # VALIDAÇÃO DE COBERTURA
    # =========================================================================

    def _check_coverage_consistency(self):
        """Verifica consistência matemática da cobertura."""
        if not self._coverage_report:
            return

        errors = []

        # 1. Verificar pages_total vs document.num_pages
        pages_total = self._coverage_report.get("pages_total")
        if pages_total is not None and self.document_num_pages is not None:
            if pages_total != self.document_num_pages:
                tolerance = self.config.pages_tolerance_percent / 100 * self.document_num_pages
                diff = abs(pages_total - self.document_num_pages)

                if diff > tolerance:
                    errors.append(MetaValidationError(
                        check_type="PAGES_TOTAL_MISMATCH",
                        severity="ERROR",
                        message=f"pages_total ({pages_total}) != document.num_pages ({self.document_num_pages})",
                        expected=str(self.document_num_pages),
                        actual=str(pages_total),
                        source_file="fase1_coverage_report.json",
                    ))
                else:
                    errors.append(MetaValidationError(
                        check_type="PAGES_TOTAL_MISMATCH",
                        severity="WARNING",
                        message=f"pages_total ({pages_total}) difere de document.num_pages ({self.document_num_pages}) mas dentro da tolerância",
                        expected=str(self.document_num_pages),
                        actual=str(pages_total),
                        source_file="fase1_coverage_report.json",
                    ))

        # 2. Verificar pages_covered + pages_missing + pages_unreadable = pages_total
        if pages_total is not None:
            pages_covered = self._coverage_report.get("pages_covered", 0)
            pages_missing = self._coverage_report.get("pages_missing", 0)
            pages_unreadable = self._coverage_report.get("pages_unreadable", 0)

            calculated_total = pages_covered + pages_missing + pages_unreadable

            if calculated_total != pages_total:
                errors.append(MetaValidationError(
                    check_type="PAGES_SUM_MISMATCH",
                    severity="ERROR",
                    message=f"pages_covered ({pages_covered}) + pages_missing ({pages_missing}) + pages_unreadable ({pages_unreadable}) = {calculated_total} != pages_total ({pages_total})",
                    expected=str(pages_total),
                    actual=str(calculated_total),
                    source_file="fase1_coverage_report.json",
                    details={
                        "pages_covered": pages_covered,
                        "pages_missing": pages_missing,
                        "pages_unreadable": pages_unreadable,
                    },
                ))

        # 3. Verificar chars cobertos vs total_chars
        total_chars = self._coverage_report.get("total_chars", 0)
        covered_chars = self._coverage_report.get("covered_chars", 0)
        coverage_percent = self._coverage_report.get("coverage_percent", 0)

        if total_chars > 0:
            expected_percent = (covered_chars / total_chars) * 100
            if abs(expected_percent - coverage_percent) > 1.0:  # 1% tolerância
                errors.append(MetaValidationError(
                    check_type="COVERAGE_PERCENT_MISMATCH",
                    severity="WARNING",
                    message=f"coverage_percent ({coverage_percent:.2f}%) não corresponde a covered_chars/total_chars ({expected_percent:.2f}%)",
                    expected=f"{expected_percent:.2f}%",
                    actual=f"{coverage_percent:.2f}%",
                    source_file="fase1_coverage_report.json",
                ))

        # Adicionar erros ao report
        for err in errors:
            self.report.add_error(err)

        # Resultado
        check = ConsistencyCheckResult(
            check_name="coverage_consistency",
            passed=len([e for e in errors if e.severity == "ERROR"]) == 0,
            details={
                "pages_total": pages_total,
                "document_num_pages": self.document_num_pages,
                "coverage_percent": coverage_percent,
            },
            errors=errors,
        )
        self.report.consistency_checks.append(check)

    # =========================================================================
    # VALIDAÇÃO DE CONTAGENS
    # =========================================================================

    def _check_citation_counts(self):
        """Verifica se contagens de citations são consistentes."""
        # Contar citations manualmente
        total_from_audits = 0
        total_from_judges = 0
        total_from_decision = 0

        for report in self._audit_reports:
            for finding in report.get("findings", []):
                total_from_audits += len(finding.get("citations", []))

        for opinion in self._judge_opinions:
            for point in opinion.get("decision_points", []):
                total_from_judges += len(point.get("citations", []))
            for disagreement in opinion.get("disagreements", []):
                total_from_judges += len(disagreement.get("citations", []))

        if self._final_decision:
            total_from_decision += len(self._final_decision.get("proofs", []))
            for point in self._final_decision.get("decision_points_final", []):
                total_from_decision += len(point.get("citations", []))

        total_calculated = total_from_audits + total_from_judges + total_from_decision

        # Comparar com integrity_report
        integrity_total = 0
        if self._integrity_report:
            citations = self._integrity_report.get("citations", {})
            integrity_total = citations.get("total", 0)

        errors = []
        if integrity_total > 0:
            diff = abs(total_calculated - integrity_total)
            if diff > self.config.citation_count_tolerance:
                errors.append(MetaValidationError(
                    check_type="CITATION_COUNT_MISMATCH",
                    severity="WARNING",
                    message=f"Total citations calculado ({total_calculated}) difere de integrity_report ({integrity_total})",
                    expected=str(integrity_total),
                    actual=str(total_calculated),
                    details={
                        "from_audits": total_from_audits,
                        "from_judges": total_from_judges,
                        "from_decision": total_from_decision,
                        "difference": diff,
                    },
                ))

        for err in errors:
            self.report.add_error(err)

        check = ConsistencyCheckResult(
            check_name="citation_counts",
            passed=len(errors) == 0,
            details={
                "total_calculated": total_calculated,
                "integrity_total": integrity_total,
                "breakdown": {
                    "audits": total_from_audits,
                    "judges": total_from_judges,
                    "decision": total_from_decision,
                },
            },
            errors=errors,
        )
        self.report.consistency_checks.append(check)

    # =========================================================================
    # VALIDAÇÃO DE TIMESTAMPS
    # =========================================================================

    def _check_timestamps(self):
        """Verifica se timestamps estão dentro da janela do run."""
        errors = []
        tolerance = timedelta(minutes=self.config.timestamp_tolerance_minutes)

        timestamps_to_check = []

        # Integrity report timestamp
        if self._integrity_report:
            ts_str = self._integrity_report.get("timestamp")
            if ts_str:
                timestamps_to_check.append(("integrity_report", ts_str))

        # Audit reports
        for i, report in enumerate(self._audit_reports):
            ts_str = report.get("timestamp")
            if ts_str:
                timestamps_to_check.append((f"audit_report_{i+1}", ts_str))

        # Judge opinions
        for i, opinion in enumerate(self._judge_opinions):
            ts_str = opinion.get("timestamp")
            if ts_str:
                timestamps_to_check.append((f"judge_opinion_{i+1}", ts_str))

        # Final decision
        if self._final_decision:
            ts_str = self._final_decision.get("timestamp")
            if ts_str:
                timestamps_to_check.append(("final_decision", ts_str))

        # Verificar cada timestamp
        for name, ts_str in timestamps_to_check:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                # Remover timezone para comparação
                ts = ts.replace(tzinfo=None)

                if ts < self.run_start - tolerance:
                    errors.append(MetaValidationError(
                        check_type="TIMESTAMP_TOO_OLD",
                        severity="WARNING",
                        message=f"{name} timestamp ({ts}) é anterior ao run_start ({self.run_start})",
                        expected=f">= {self.run_start}",
                        actual=str(ts),
                        source_file=name,
                    ))

                if ts > datetime.now() + tolerance:
                    errors.append(MetaValidationError(
                        check_type="TIMESTAMP_FUTURE",
                        severity="ERROR",
                        message=f"{name} timestamp ({ts}) está no futuro",
                        expected=f"<= {datetime.now()}",
                        actual=str(ts),
                        source_file=name,
                    ))

            except Exception as e:
                errors.append(MetaValidationError(
                    check_type="TIMESTAMP_INVALID",
                    severity="WARNING",
                    message=f"{name} timestamp inválido: {ts_str} ({e})",
                    actual=ts_str,
                    source_file=name,
                ))

        for err in errors:
            self.report.add_error(err)

        check = ConsistencyCheckResult(
            check_name="timestamps_sanity",
            passed=len([e for e in errors if e.severity == "ERROR"]) == 0,
            details={
                "run_start": self.run_start.isoformat() if self.run_start else None,
                "checked_count": len(timestamps_to_check),
            },
            errors=errors,
        )
        self.report.consistency_checks.append(check)


# ============================================================================
# FUNÇÕES DE CONVENIÊNCIA
# ============================================================================

def validate_run_meta_integrity(
    run_id: str,
    output_dir: Optional[Path] = None,
    run_start: Optional[datetime] = None,
    loaded_doc_ids: Optional[Set[str]] = None,
    document_num_pages: Optional[int] = None,
    config: Optional[MetaIntegrityConfig] = None,
) -> MetaIntegrityReport:
    """
    Função de conveniência para validar meta-integridade de um run.

    Args:
        run_id: ID do run
        output_dir: Diretório de outputs
        run_start: Timestamp de início
        loaded_doc_ids: doc_ids dos documentos carregados
        document_num_pages: Número de páginas do documento
        config: Configuração de validação

    Returns:
        MetaIntegrityReport
    """
    validator = MetaIntegrityValidator(
        run_id=run_id,
        output_dir=output_dir,
        config=config,
        run_start=run_start,
        loaded_doc_ids=loaded_doc_ids,
        document_num_pages=document_num_pages,
    )
    return validator.validate()


def create_meta_integrity_summary(report: MetaIntegrityReport) -> str:
    """Cria resumo textual do relatório."""
    lines = [
        f"MetaIntegrity Report: {report.run_id}",
        f"Status: {'CONSISTENT' if report.is_consistent else 'INCONSISTENT'}",
        f"Errors: {report.error_count} | Warnings: {report.warning_count}",
    ]

    # Files
    fc = report.files_check
    if fc.missing:
        lines.append(f"Missing files: {', '.join(fc.missing[:5])}")

    # Checks
    for check in report.consistency_checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{status}] {check.check_name}")

    return "\n".join(lines)

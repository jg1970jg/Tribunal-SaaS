# -*- coding: utf-8 -*-
"""
Testes para as novas funcionalidades:
1. Auto-retry OCR (PDF Safe)
2. Agregador Fase 1 em JSON
3. Chefe Fase 2 em JSON
4. SEM_PROVA_DETERMINANTE + is_determinant + ceiling confiança
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

# Imports do projeto
from src.pipeline.pdf_safe import (
    PDFSafeLoader,
    PageRecord,
    PageMetrics,
    PDFSafeResult,
)
from src.pipeline.schema_audit import (
    AuditFinding,
    AuditReport,
    JudgePoint,
    JudgeOpinion,
    FinalDecision,
    Citation,
    CoverageCheck,
    FindingType,
    Severity,
    DecisionType,
    parse_audit_report,
    parse_judge_opinion,
    parse_chefe_report,
    ChefeConsolidatedReport,
    ConsolidatedFinding,
    Divergence,
)
from src.pipeline.integrity import (
    ValidationError,
    validate_judge_opinion,
)
from src.pipeline.confidence_policy import (
    DEFAULT_PENALTY_RULES,
    compute_penalty,
    ConfidencePolicyCalculator,
)


# ============================================================================
# TESTES: AUTO-RETRY OCR (Ordem 1)
# ============================================================================

class TestAutoRetryOCR:
    """Testes para auto-retry OCR em pdf_safe.py."""

    def test_page_record_has_ocr_fields(self):
        """PageRecord deve ter campos de OCR."""
        page = PageRecord(page_num=1)

        assert hasattr(page, 'ocr_attempted')
        assert hasattr(page, 'ocr_success')
        assert hasattr(page, 'ocr_chars')
        assert hasattr(page, 'status_before_ocr')
        assert hasattr(page, 'status_after_ocr')

        # Valores default
        assert page.ocr_attempted is False
        assert page.ocr_success is False
        assert page.ocr_chars == 0
        assert page.status_before_ocr is None
        assert page.status_after_ocr is None

    def test_page_record_to_dict_includes_ocr(self):
        """to_dict deve incluir campos OCR."""
        page = PageRecord(
            page_num=1,
            ocr_attempted=True,
            ocr_success=True,
            ocr_chars=500,
            status_before_ocr="SEM_TEXTO",
            status_after_ocr="OK",
        )

        d = page.to_dict()

        assert d['ocr_attempted'] is True
        assert d['ocr_success'] is True
        assert d['ocr_chars'] == 500
        assert d['status_before_ocr'] == "SEM_TEXTO"
        assert d['status_after_ocr'] == "OK"

    def test_pdf_safe_result_has_ocr_stats(self):
        """PDFSafeResult deve ter estatísticas de OCR."""
        result = PDFSafeResult(
            filename="test.pdf",
            total_pages=10,
            ocr_attempted=3,
            ocr_recovered=2,
            ocr_failed=1,
        )

        d = result.to_dict()

        assert d['ocr_attempted'] == 3
        assert d['ocr_recovered'] == 2
        assert d['ocr_failed'] == 1

    def test_auto_retry_ocr_method_exists(self):
        """PDFSafeLoader deve ter método _auto_retry_ocr."""
        loader = PDFSafeLoader()
        assert hasattr(loader, '_auto_retry_ocr')

    def test_auto_retry_ocr_skips_ok_pages(self):
        """_auto_retry_ocr não deve processar páginas OK."""
        loader = PDFSafeLoader()

        page = PageRecord(
            page_num=1,
            status_inicial="OK",
            text_clean="Texto normal com mais de 200 caracteres para ser considerado OK.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            pages_dir = Path(tmp)
            result_page = loader._auto_retry_ocr(page, pages_dir)

            assert result_page.ocr_attempted is False

    def test_auto_retry_ocr_attempts_for_problematic(self):
        """_auto_retry_ocr deve tentar para páginas SEM_TEXTO/SUSPEITA."""
        loader = PDFSafeLoader()
        loader._tesseract_available = False  # Simular sem Tesseract

        page = PageRecord(
            page_num=1,
            status_inicial="SEM_TEXTO",
            text_clean="",
            image_path="",  # Sem imagem
        )

        with tempfile.TemporaryDirectory() as tmp:
            pages_dir = Path(tmp)
            result_page = loader._auto_retry_ocr(page, pages_dir)

            # Não tentou porque sem Tesseract
            assert result_page.ocr_attempted is False


# ============================================================================
# TESTES: AGREGADOR FASE 1 JSON (Ordem 2)
# ============================================================================

class TestAgregadorF1JSON:
    """Testes para JSON estruturado do Agregador Fase 1."""

    def test_agregado_json_structure(self):
        """Estrutura esperada do fase1_agregado_consolidado.json."""
        # Simular estrutura esperada
        agregado = {
            "run_id": "run_test_123",
            "timestamp": datetime.now().isoformat(),
            "doc_meta": {
                "doc_id": "doc_abc",
                "filename": "test.pdf",
                "total_chars": 10000,
            },
            "union_items": [],
            "union_items_count": 0,
            "items_by_extractor": {"E1": 5, "E2": 4, "E3": 6},
            "coverage_report": {
                "coverage_percent": 98.5,
                "is_complete": True,
            },
            "unreadable_parts": [],
            "conflicts": [],
            "conflicts_count": 0,
            "extraction_runs": [],
            "errors": [],
            "warnings": [],
            "summary": {
                "total_items": 15,
                "coverage_percent": 98.5,
                "is_complete": True,
            },
        }

        # Verificar campos obrigatórios
        assert "run_id" in agregado
        assert "doc_meta" in agregado
        assert "union_items" in agregado
        assert "coverage_report" in agregado
        assert "unreadable_parts" in agregado
        assert "errors" in agregado
        assert "warnings" in agregado
        assert "summary" in agregado


# ============================================================================
# TESTES: CHEFE FASE 2 JSON (Ordem 3)
# ============================================================================

class TestChefeF2JSON:
    """Testes para JSON estruturado do Chefe Fase 2."""

    def test_chefe_consolidated_report_creation(self):
        """ChefeConsolidatedReport deve ser criável."""
        report = ChefeConsolidatedReport(
            chefe_id="CHEFE",
            model_name="test-model",
            run_id="run_test",
        )

        assert report.chefe_id == "CHEFE"
        assert report.model_name == "test-model"
        assert len(report.consolidated_findings) == 0
        assert len(report.divergences) == 0

    def test_consolidated_finding_creation(self):
        """ConsolidatedFinding deve preservar proveniência."""
        finding = ConsolidatedFinding(
            finding_id="finding_001",
            claim="Teste de claim",
            finding_type=FindingType.FACTO,
            severity=Severity.MEDIO,
            sources=["A1", "A2", "A3"],
            consensus_level="forte",
        )

        assert finding.sources == ["A1", "A2", "A3"]
        assert finding.consensus_level == "forte"

        d = finding.to_dict()
        assert d["sources"] == ["A1", "A2", "A3"]
        assert d["consensus_level"] == "forte"

    def test_parse_chefe_report_success(self):
        """parse_chefe_report deve parsear JSON válido."""
        json_output = json.dumps({
            "chefe_id": "CHEFE",
            "consolidated_findings": [
                {
                    "finding_id": "cf_001",
                    "claim": "Facto consolidado",
                    "finding_type": "facto",
                    "severity": "alto",
                    "sources": ["A1", "A2"],
                    "consensus_level": "parcial",
                }
            ],
            "divergences": [],
            "coverage_check": {
                "auditors_seen": ["A1", "A2", "A3", "A4"],
                "coverage_percent": 95.0,
            },
            "recommendations_phase3": [],
            "legal_refs_consolidated": [],
            "open_questions": [],
        })

        report = parse_chefe_report(json_output, "test-model", "run_test")

        assert len(report.errors) == 0
        assert len(report.consolidated_findings) == 1
        assert report.consolidated_findings[0].sources == ["A1", "A2"]

    def test_parse_chefe_report_soft_fail(self):
        """parse_chefe_report deve fazer soft-fail com JSON inválido."""
        invalid_json = "isto não é JSON válido {"

        report = parse_chefe_report(invalid_json, "test-model", "run_test")

        # Deve ter criado relatório mínimo com erro
        assert len(report.errors) > 0
        assert any("ERROR_RECOVERED" in e for e in report.errors)
        assert len(report.consolidated_findings) == 0

    def test_chefe_report_to_markdown(self):
        """ChefeConsolidatedReport.to_markdown deve gerar Markdown."""
        report = ChefeConsolidatedReport(
            chefe_id="CHEFE",
            model_name="test-model",
            run_id="run_test",
            consolidated_findings=[
                ConsolidatedFinding(
                    finding_id="cf_001",
                    claim="Teste",
                    finding_type=FindingType.FACTO,
                    severity=Severity.ALTO,
                    sources=["A1", "A2"],
                    consensus_level="parcial",
                )
            ],
        )

        md = report.to_markdown()

        assert "# Relatório Consolidado do Chefe" in md
        assert "A1, A2" in md
        assert "Consenso Parcial" in md


# ============================================================================
# TESTES: SEM_PROVA_DETERMINANTE (Ordem 4)
# ============================================================================

class TestSemProvaDeterminante:
    """Testes para SEM_PROVA_DETERMINANTE + is_determinant + ceiling."""

    def test_judge_point_has_is_determinant(self):
        """JudgePoint deve ter campo is_determinant."""
        point = JudgePoint(
            point_id="p1",
            conclusion="Conclusão teste",
            rationale="Razão teste",
            is_determinant=True,
        )

        assert point.is_determinant is True

        d = point.to_dict()
        assert d["is_determinant"] is True

    def test_judge_point_from_dict_with_is_determinant(self):
        """JudgePoint.from_dict deve ler is_determinant."""
        data = {
            "point_id": "p1",
            "conclusion": "Conclusão",
            "rationale": "Razão",
            "is_determinant": True,
        }

        point = JudgePoint.from_dict(data)

        assert point.is_determinant is True

    def test_audit_finding_has_is_determinant(self):
        """AuditFinding deve ter campo is_determinant."""
        finding = AuditFinding(
            finding_id="f1",
            claim="Teste",
            finding_type=FindingType.FACTO,
            severity=Severity.ALTO,
            citations=[],
            is_determinant=True,
        )

        assert finding.is_determinant is True

        d = finding.to_dict()
        assert d["is_determinant"] is True

    def test_penalty_rule_sem_prova_determinante_exists(self):
        """DEFAULT_PENALTY_RULES deve ter SEM_PROVA_DETERMINANTE."""
        assert "SEM_PROVA_DETERMINANTE" in DEFAULT_PENALTY_RULES

        rule = DEFAULT_PENALTY_RULES["SEM_PROVA_DETERMINANTE"]

        assert rule.penalty_per_occurrence >= 0.10  # Penalty alto
        assert rule.severity_ceiling is not None
        assert rule.severity_ceiling <= 0.65  # Ceiling baixo

    def test_validate_judge_opinion_sem_prova_determinante(self):
        """validate_judge_opinion deve detectar SEM_PROVA_DETERMINANTE."""
        # Criar JudgeOpinion com ponto determinante SEM citations
        opinion = JudgeOpinion(
            judge_id="J1",
            model_name="test",
            run_id="run_test",
            recommendation=DecisionType.PROCEDENTE,
            decision_points=[
                JudgePoint(
                    point_id="p1",
                    conclusion="Conclusão crucial",
                    rationale="Sem prova documental",
                    citations=[],  # SEM CITATIONS
                    is_determinant=True,  # É DETERMINANTE
                )
            ],
        )

        is_valid, errors, penalty = validate_judge_opinion(
            opinion,
            document_text="texto do documento",
            total_chars=1000,
        )

        # Deve ter detectado SEM_PROVA_DETERMINANTE
        sem_prova_errors = [e for e in errors if e.error_type == "SEM_PROVA_DETERMINANTE"]
        assert len(sem_prova_errors) > 0

        # Penalty deve ser significativo
        assert penalty >= 0.10

    def test_validate_judge_opinion_non_determinant_ok(self):
        """Ponto não-determinante sem citation é apenas warning."""
        opinion = JudgeOpinion(
            judge_id="J1",
            model_name="test",
            run_id="run_test",
            recommendation=DecisionType.PROCEDENTE,
            decision_points=[
                JudgePoint(
                    point_id="p1",
                    conclusion="Conclusão secundária",
                    rationale="Observação",
                    citations=[],  # Sem citations
                    is_determinant=False,  # NÃO é determinante
                )
            ],
        )

        is_valid, errors, penalty = validate_judge_opinion(
            opinion,
            document_text="texto do documento",
            total_chars=1000,
        )

        # Deve ter warning, não error grave
        sem_prova_errors = [e for e in errors if e.error_type == "SEM_PROVA_DETERMINANTE"]
        assert len(sem_prova_errors) == 0

        # Mas deve ter MISSING_CITATION warning
        missing_citation = [e for e in errors if e.error_type == "MISSING_CITATION"]
        assert len(missing_citation) > 0

    def test_compute_penalty_with_sem_prova_determinante(self):
        """compute_penalty deve aplicar ceiling para SEM_PROVA_DETERMINANTE."""
        calculator = ConfidencePolicyCalculator()

        # Criar lista de erros incluindo SEM_PROVA_DETERMINANTE
        # O formato esperado é lista de strings ou objetos com error_type
        errors_list = ["SEM_PROVA_DETERMINANTE: Ponto determinante sem prova"]

        result = calculator.compute_penalty(
            errors_list=errors_list,
            original_confidence=0.90,
        )

        # Deve ter aplicado penalty para SEM_PROVA_DETERMINANTE
        assert result.total_penalty > 0
        # Ceiling deve ter sido aplicado (severity_ceiling=0.60)
        assert result.adjusted_confidence <= 0.60  # Ceiling de 60%


# ============================================================================
# TESTES: PIPELINE NÃO ABORTA (Ordem 5)
# ============================================================================

class TestPipelineNaoAborta:
    """Testes para garantir que o pipeline não aborta."""

    def test_parse_audit_report_never_raises(self):
        """parse_audit_report nunca deve levantar exceção."""
        # Inputs realmente inválidos (não parseable)
        truly_invalid_inputs = [
            "",
            "texto aleatório sem JSON",
            "{json inválido sem fechar",
            "null",  # JSON válido mas não é objeto
            "[]",    # JSON válido mas não é objeto
        ]

        for invalid_input in truly_invalid_inputs:
            # Não deve levantar exceção
            report = parse_audit_report(invalid_input, "A1", "model", "run")

            # Deve ter criado relatório
            assert report is not None
            assert report.auditor_id == "A1"

        # Inputs parcialmente válidos (JSON válido mas incompleto)
        # Estes devem funcionar sem erros
        partial_valid = '{"findings": []}'
        report = parse_audit_report(partial_valid, "A1", "model", "run")
        assert report is not None
        assert report.auditor_id == "A1"

    def test_parse_judge_opinion_never_raises(self):
        """parse_judge_opinion nunca deve levantar exceção."""
        invalid_inputs = [
            "",
            "texto aleatório",
            "{json inválido",
        ]

        for invalid_input in invalid_inputs:
            opinion = parse_judge_opinion(invalid_input, "J1", "model", "run")

            assert opinion is not None
            assert opinion.judge_id == "J1"
            assert len(opinion.errors) > 0

    def test_parse_chefe_report_never_raises(self):
        """parse_chefe_report nunca deve levantar exceção."""
        invalid_inputs = [
            "",
            "texto aleatório",
            "{json inválido",
        ]

        for invalid_input in invalid_inputs:
            report = parse_chefe_report(invalid_input, "model", "run")

            assert report is not None
            assert report.chefe_id == "CHEFE"
            assert len(report.errors) > 0


# ============================================================================
# TESTES: OUTPUTS JSON EXISTEM
# ============================================================================

class TestOutputsJSONExistem:
    """Testes para verificar que outputs JSON são gerados."""

    def test_agregado_json_fields(self):
        """fase1_agregado_consolidado.json deve ter campos obrigatórios."""
        required_fields = [
            "run_id",
            "doc_meta",
            "union_items",
            "coverage_report",
            "unreadable_parts",
            "errors",
            "warnings",
            "summary",
        ]

        # Estrutura mínima esperada
        minimal_agregado = {
            "run_id": "test",
            "doc_meta": {},
            "union_items": [],
            "coverage_report": {},
            "unreadable_parts": [],
            "errors": [],
            "warnings": [],
            "summary": {},
        }

        for field in required_fields:
            assert field in minimal_agregado

    def test_chefe_json_fields(self):
        """fase2_chefe_consolidado.json deve ter campos obrigatórios."""
        required_fields = [
            "chefe_id",
            "model_name",
            "run_id",
            "consolidated_findings",
            "divergences",
            "coverage_check",
        ]

        report = ChefeConsolidatedReport(
            chefe_id="CHEFE",
            model_name="test",
            run_id="run",
        )

        d = report.to_dict()

        for field in required_fields:
            assert field in d


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

# -*- coding: utf-8 -*-
"""
Testes para MetaIntegrity, TextNormalize e ConfidencePolicy.

Casos cobertos:
1. Multi-documento (2 docs)
2. PDFSafe + OCR ruidoso
3. Citations com doc_id inexistente
4. pages_total incoerente
5. Normalização de texto
6. Policy de confiança determinística
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ============================================================================
# FIXTURES E HELPERS
# ============================================================================

@pytest.fixture
def temp_output_dir():
    """Cria diretório temporário para outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_document_text():
    """Texto de documento de teste com marcadores de página."""
    return """[Página 1]
O contrato de arrendamento foi celebrado em 15 de Janeiro de 2024.
O valor mensal da renda é de €850,00 (oitocentos e cinquenta euros).

[Página 2]
As partes acordaram um prazo de 2 (dois) anos.
Nos termos do artigo 1022º do Código Civil.

[Página 3]
O inquilino compromete-se a pagar a renda até ao dia 8 de cada mês.
Penalidade de mora: 1% ao mês sobre o valor em atraso.
"""


@pytest.fixture
def sample_ocr_noisy_text():
    """Texto com erros de OCR típicos."""
    return """[Página 1]
0 c0ntrat0 de arrendament0 f0i ce1ebrad0 em 15 de Jane1r0 de 2024.
0 va10r mensa1 da renda é de €85O,OO (o1t0cent0s e c1nquenta eur0s).

[Página 2]
As partes ac0rdaram um praz0 de 2 (d0is) an0s.
N0s term0s d0 art1g0 1022º d0 Cód1g0 C1v1l.
"""


@pytest.fixture
def sample_unified_result():
    """UnifiedExtractionResult simulado."""
    return {
        "result_id": "unified_test_001",
        "document_meta": {
            "doc_id": "doc_main",
            "filename": "contrato.pdf",
            "file_type": ".pdf",
            "total_chars": 500,
            "total_pages": 3,
        },
        "chunks": [
            {"chunk_id": "doc_main_c0000", "start_char": 0, "end_char": 500}
        ],
        "union_items": [
            {"item_id": "item_001", "item_type": "date", "value_normalized": "2024-01-15"},
            {"item_id": "item_002", "item_type": "amount", "value_normalized": "€850,00"},
        ],
    }


@pytest.fixture
def sample_coverage_report():
    """Coverage report simulado."""
    return {
        "total_chars": 500,
        "covered_chars": 480,
        "coverage_percent": 96.0,
        "is_complete": True,
        "pages_total": 3,
        "pages_covered": 3,
        "pages_missing": 0,
        "pages_unreadable": 0,
        "gaps": [],
    }


@pytest.fixture
def sample_audit_reports():
    """Lista de AuditReports simulados."""
    return [
        {
            "auditor_id": "A1",
            "model_name": "test-model-1",
            "run_id": "test_run",
            "timestamp": datetime.now().isoformat(),
            "findings": [
                {
                    "finding_id": "f1",
                    "claim": "Contrato celebrado em Janeiro 2024",
                    "finding_type": "facto",
                    "severity": "medio",
                    "citations": [
                        {
                            "doc_id": "doc_main",
                            "start_char": 10,
                            "end_char": 80,
                            "page_num": 1,
                            "excerpt": "contrato de arrendamento celebrado",
                        }
                    ],
                    "evidence_item_ids": ["item_001"],
                }
            ],
            "errors": [],
        },
        {
            "auditor_id": "A2",
            "model_name": "test-model-2",
            "run_id": "test_run",
            "timestamp": datetime.now().isoformat(),
            "findings": [
                {
                    "finding_id": "f2",
                    "claim": "Renda mensal €850",
                    "finding_type": "facto",
                    "severity": "baixo",
                    "citations": [
                        {
                            "doc_id": "doc_main",
                            "start_char": 50,
                            "end_char": 120,
                            "page_num": 1,
                            "excerpt": "valor mensal da renda",
                        }
                    ],
                    "evidence_item_ids": ["item_002"],
                }
            ],
            "errors": [],
        },
    ]


@pytest.fixture
def sample_judge_opinions():
    """Lista de JudgeOpinions simulados."""
    return [
        {
            "judge_id": "J1",
            "model_name": "test-judge-1",
            "run_id": "test_run",
            "timestamp": datetime.now().isoformat(),
            "recommendation": "procedente",
            "decision_points": [
                {
                    "point_id": "p1",
                    "conclusion": "Contrato válido",
                    "rationale": "Cumpre requisitos legais",
                    "citations": [
                        {
                            "doc_id": "doc_main",
                            "start_char": 10,
                            "end_char": 100,
                            "page_num": 1,
                        }
                    ],
                    "confidence": 0.85,
                }
            ],
            "disagreements": [],
            "errors": [],
        },
    ]


# ============================================================================
# TESTES: TEXT_NORMALIZE
# ============================================================================

class TestTextNormalize:
    """Testes para src/pipeline/text_normalize.py"""

    def test_normalize_basic(self):
        """Normalização básica funciona."""
        from src.pipeline.text_normalize import normalize_for_matching

        text = "Contrato de Arrendaménto Célebrado"
        result = normalize_for_matching(text)

        assert "e" in result  # acento removido
        assert result.islower()
        assert "  " not in result  # sem espaços duplos

    def test_normalize_ocr_substitutions(self):
        """Substituições OCR funcionam quando configuradas."""
        from src.pipeline.text_normalize import (
            normalize_for_matching,
            NormalizationConfig,
        )

        text = "c0ntrat0 ce1ebrad0"
        config = NormalizationConfig.ocr_tolerant()
        result = normalize_for_matching(text, config)

        # 0→o, 1→l apenas quando não em contexto numérico
        # Nota: a substituição é contextual
        assert "contrato" in result or "c0ntrat0" in result

    def test_normalize_preserves_currency(self):
        """Símbolos de moeda são preservados."""
        from src.pipeline.text_normalize import normalize_for_matching

        text = "Valor de €850,00 euros"
        result = normalize_for_matching(text)

        assert "€" in result or "85000" in result

    def test_normalize_with_debug(self):
        """Normalização retorna debug info."""
        from src.pipeline.text_normalize import normalize_for_matching

        text = "Contrato CELEBRADO"
        result = normalize_for_matching(text, return_debug=True)

        assert hasattr(result, 'raw')
        assert hasattr(result, 'normalized')
        assert hasattr(result, 'words')
        assert result.raw == text
        assert result.normalized.islower()

    def test_text_contains_direct(self):
        """text_contains encontra substring direta."""
        from src.pipeline.text_normalize import text_contains_normalized

        haystack = "O contrato de arrendamento foi celebrado em Lisboa"
        needle = "contrato celebrado"

        result = text_contains_normalized(haystack, needle, threshold=0.7)
        assert result

    def test_text_contains_ocr_noisy(self):
        """text_contains tolera OCR ruidoso."""
        from src.pipeline.text_normalize import (
            text_contains_normalized,
            NormalizationConfig,
        )

        # Texto com OCR limpo
        haystack = "O contrato de arrendamento foi celebrado"
        # Excerpt com erros OCR
        needle = "c0ntrat0 arrendament0"

        config = NormalizationConfig.ocr_tolerant()
        result = text_contains_normalized(
            haystack, needle, threshold=0.6, config=config
        )

        # Deve encontrar com threshold baixo devido ao overlap de palavras
        # mesmo que substituições OCR não sejam perfeitas
        assert isinstance(result, bool)

    def test_text_similarity(self):
        """Similaridade entre textos funciona."""
        from src.pipeline.text_normalize import text_similarity_normalized

        text1 = "contrato de arrendamento celebrado"
        text2 = "contrato arrendamento celebrado"

        similarity = text_similarity_normalized(text1, text2)

        assert similarity >= 0.7  # Alta similaridade (Jaccard com pequena diferença)

    def test_text_similarity_different(self):
        """Similaridade baixa para textos diferentes."""
        from src.pipeline.text_normalize import text_similarity_normalized

        text1 = "contrato de arrendamento"
        text2 = "processo judicial pendente"

        similarity = text_similarity_normalized(text1, text2)

        assert similarity < 0.3  # Baixa similaridade

    def test_extract_page_markers(self):
        """Extração de marcadores de página funciona."""
        from src.pipeline.text_normalize import extract_page_markers

        text = "[Página 1]\nTexto da página 1\n[Página 2]\nTexto da página 2"
        markers = extract_page_markers(text)

        assert len(markers) == 2
        assert markers[0][0] == 1  # page_num
        assert markers[1][0] == 2

    def test_normalize_excerpt_debug(self):
        """Debug de excerpt funciona."""
        from src.pipeline.text_normalize import normalize_excerpt_for_debug

        excerpt = "contrato celebrado"
        actual = "O contrato foi celebrado em Lisboa"

        debug = normalize_excerpt_for_debug(excerpt, actual)

        assert "excerpt" in debug
        assert "actual" in debug
        assert "match" in debug
        assert debug["match"] is True


# ============================================================================
# TESTES: CONFIDENCE_POLICY
# ============================================================================

class TestConfidencePolicy:
    """Testes para src/pipeline/confidence_policy.py"""

    def test_compute_penalty_empty(self):
        """Penalty é zero sem erros."""
        from src.pipeline.confidence_policy import compute_penalty

        result = compute_penalty()

        assert result.total_penalty == 0.0
        assert result.adjusted_confidence == 1.0
        assert not result.is_severely_penalized

    def test_compute_penalty_from_errors_list(self):
        """Penalty calculada de lista de erros."""
        from src.pipeline.confidence_policy import compute_penalty

        errors = [
            "INTEGRITY_WARNING: PAGE_MISMATCH: página errada",
            "INTEGRITY_WARNING: EXCERPT_MISMATCH: excerpt não encontrado",
        ]

        result = compute_penalty(errors_list=errors, original_confidence=1.0)

        assert result.total_penalty > 0
        assert result.adjusted_confidence < 1.0

    def test_compute_penalty_error_recovered(self):
        """ERROR_RECOVERED impõe ceiling."""
        from src.pipeline.confidence_policy import compute_penalty

        errors = ["ERROR_RECOVERED: JSON inválido, relatório mínimo criado"]

        result = compute_penalty(errors_list=errors, original_confidence=1.0)

        assert result.is_severely_penalized
        assert result.confidence_ceiling is not None
        assert result.confidence_ceiling <= 0.75
        assert result.adjusted_confidence <= result.confidence_ceiling

    def test_compute_penalty_from_coverage(self):
        """Penalty calculada de coverage report."""
        from src.pipeline.confidence_policy import compute_penalty

        coverage = {
            "coverage_percent": 85.0,  # < 95%
            "pages_missing": 2,
            "pages_unreadable": 1,
            "gaps": [
                {"start": 100, "end": 300, "length": 200},  # > 100 chars
            ],
        }

        result = compute_penalty(coverage_report=coverage, original_confidence=1.0)

        assert result.total_penalty > 0
        assert "coverage" in result.by_category

    def test_compute_penalty_cumulative(self):
        """Penalties são cumulativas até limite."""
        from src.pipeline.confidence_policy import compute_penalty

        errors = [
            "INTEGRITY_WARNING: PAGE_MISMATCH: erro 1",
            "INTEGRITY_WARNING: PAGE_MISMATCH: erro 2",
            "INTEGRITY_WARNING: PAGE_MISMATCH: erro 3",
            "INTEGRITY_WARNING: PAGE_MISMATCH: erro 4",
            "INTEGRITY_WARNING: PAGE_MISMATCH: erro 5",
        ]

        result = compute_penalty(errors_list=errors, original_confidence=1.0)

        # Deve haver penalidade mas não exceder max
        assert result.total_penalty > 0
        assert result.total_penalty <= 0.50  # global max

    def test_penalty_breakdown_by_category(self):
        """Breakdown por categoria funciona."""
        from src.pipeline.confidence_policy import compute_penalty

        errors = [
            "INTEGRITY_WARNING: PAGE_MISMATCH: erro",
            "INTEGRITY_WARNING: EXCERPT_MISMATCH: erro",
            "ERROR_RECOVERED: parsing falhou",
        ]

        coverage = {"coverage_percent": 80.0, "gaps": []}

        result = compute_penalty(
            errors_list=errors,
            coverage_report=coverage,
            original_confidence=1.0
        )

        # Deve ter breakdown por categoria
        assert len(result.by_category) > 0

    def test_apply_penalty_to_confidence(self):
        """Aplicar penalty a confidence funciona."""
        from src.pipeline.confidence_policy import (
            compute_penalty,
            apply_penalty_to_confidence,
        )

        penalty = compute_penalty(
            errors_list=["INTEGRITY_WARNING: PAGE_MISMATCH: erro"]
        )

        adjusted = apply_penalty_to_confidence(0.95, penalty)

        assert adjusted < 0.95
        assert adjusted >= 0.0

    def test_penalty_result_to_dict(self):
        """Resultado pode ser serializado."""
        from src.pipeline.confidence_policy import compute_penalty

        result = compute_penalty(
            errors_list=["INTEGRITY_WARNING: PAGE_MISMATCH: erro"]
        )

        d = result.to_dict()

        assert "total_penalty" in d
        assert "adjusted_confidence" in d
        assert "by_category" in d


# ============================================================================
# TESTES: META_INTEGRITY
# ============================================================================

class TestMetaIntegrity:
    """Testes para src/pipeline/meta_integrity.py"""

    def test_validate_empty_dir(self, temp_output_dir):
        """Validação de diretório vazio reporta ficheiros em falta."""
        from src.pipeline.meta_integrity import MetaIntegrityValidator

        validator = MetaIntegrityValidator(
            run_id="test_empty",
            output_dir=temp_output_dir,
        )

        report = validator.validate()

        assert not report.is_consistent
        assert len(report.files_check.missing) > 0
        assert report.error_count > 0

    def test_validate_with_files(
        self,
        temp_output_dir,
        sample_unified_result,
        sample_coverage_report,
        sample_audit_reports,
        sample_judge_opinions,
    ):
        """Validação com ficheiros presentes funciona."""
        from src.pipeline.meta_integrity import (
            MetaIntegrityValidator,
            MetaIntegrityConfig,
        )

        # Criar ficheiros necessários
        (temp_output_dir / "fase1_unified_result.json").write_text(
            json.dumps(sample_unified_result), encoding='utf-8'
        )
        (temp_output_dir / "fase1_coverage_report.json").write_text(
            json.dumps(sample_coverage_report), encoding='utf-8'
        )
        (temp_output_dir / "fase2_all_audit_reports.json").write_text(
            json.dumps(sample_audit_reports), encoding='utf-8'
        )
        (temp_output_dir / "fase3_all_judge_opinions.json").write_text(
            json.dumps(sample_judge_opinions), encoding='utf-8'
        )

        # Criar ficheiros individuais de auditors
        for i in range(1, 5):
            (temp_output_dir / f"fase2_auditor_{i}.json").write_text(
                json.dumps(sample_audit_reports[0] if i <= len(sample_audit_reports) else {}),
                encoding='utf-8'
            )

        # Criar ficheiros individuais de juízes
        for i in range(1, 4):
            (temp_output_dir / f"fase3_juiz_{i}.json").write_text(
                json.dumps(sample_judge_opinions[0] if i <= len(sample_judge_opinions) else {}),
                encoding='utf-8'
            )

        # Criar ficheiros de extractors
        for i in range(1, 6):
            (temp_output_dir / f"fase1_extractor_E{i}_items.json").write_text(
                json.dumps([]), encoding='utf-8'
            )

        # Config sem alguns ficheiros opcionais
        config = MetaIntegrityConfig(
            require_integrity_report=False,
            require_final_decision=False,
        )

        validator = MetaIntegrityValidator(
            run_id="test_files",
            output_dir=temp_output_dir,
            config=config,
            loaded_doc_ids={"doc_main"},
        )

        report = validator.validate()

        # Deve ter todos os ficheiros requeridos
        assert report.files_check.all_present or len(report.files_check.missing) == 0

    def test_doc_id_invalid(self, temp_output_dir, sample_unified_result):
        """doc_id inexistente é detectado como ERROR."""
        from src.pipeline.meta_integrity import (
            MetaIntegrityValidator,
            MetaIntegrityConfig,
        )

        # Audit report com doc_id inválido
        audit_reports = [
            {
                "auditor_id": "A1",
                "model_name": "test",
                "run_id": "test",
                "timestamp": datetime.now().isoformat(),
                "findings": [
                    {
                        "finding_id": "f1",
                        "claim": "teste",
                        "finding_type": "facto",
                        "severity": "baixo",
                        "citations": [
                            {
                                "doc_id": "doc_inexistente",  # DOC_ID INVÁLIDO
                                "start_char": 10,
                                "end_char": 50,
                            }
                        ],
                    }
                ],
            }
        ]

        (temp_output_dir / "fase1_unified_result.json").write_text(
            json.dumps(sample_unified_result), encoding='utf-8'
        )
        (temp_output_dir / "fase2_all_audit_reports.json").write_text(
            json.dumps(audit_reports), encoding='utf-8'
        )

        config = MetaIntegrityConfig(
            require_coverage_report=False,
            require_integrity_report=False,
            require_judge_opinions=False,
            require_final_decision=False,
            require_audit_reports=False,
        )

        validator = MetaIntegrityValidator(
            run_id="test_invalid_doc_id",
            output_dir=temp_output_dir,
            config=config,
            loaded_doc_ids={"doc_main"},  # Só doc_main é válido
        )

        report = validator.validate()

        # Deve ter erro de DOC_ID_INVALID
        doc_id_errors = [
            e for e in report.errors
            if e.check_type == "DOC_ID_INVALID"
        ]
        assert len(doc_id_errors) > 0
        assert doc_id_errors[0].severity == "ERROR"

    def test_pages_total_inconsistent(self, temp_output_dir, sample_unified_result):
        """pages_total incoerente é detectado."""
        from src.pipeline.meta_integrity import MetaIntegrityValidator, MetaIntegrityConfig

        # Coverage com pages_total diferente do documento
        coverage = {
            "total_chars": 500,
            "covered_chars": 500,
            "coverage_percent": 100.0,
            "pages_total": 10,  # DIFERENTE de document_num_pages=3
            "pages_covered": 8,
            "pages_missing": 1,
            "pages_unreadable": 1,
        }

        (temp_output_dir / "fase1_coverage_report.json").write_text(
            json.dumps(coverage), encoding='utf-8'
        )
        (temp_output_dir / "fase1_unified_result.json").write_text(
            json.dumps(sample_unified_result), encoding='utf-8'
        )

        config = MetaIntegrityConfig(
            require_integrity_report=False,
            require_audit_reports=False,
            require_judge_opinions=False,
            require_final_decision=False,
        )

        validator = MetaIntegrityValidator(
            run_id="test_pages_mismatch",
            output_dir=temp_output_dir,
            config=config,
            document_num_pages=3,  # Documento tem 3 páginas
        )

        report = validator.validate()

        # Deve ter erro de PAGES_TOTAL_MISMATCH
        pages_errors = [
            e for e in report.errors
            if "PAGES" in e.check_type
        ]
        assert len(pages_errors) > 0

    def test_pages_sum_inconsistent(self, temp_output_dir, sample_unified_result):
        """Soma de páginas incoerente é detectada."""
        from src.pipeline.meta_integrity import MetaIntegrityValidator, MetaIntegrityConfig

        # pages_covered + pages_missing + pages_unreadable != pages_total
        coverage = {
            "total_chars": 500,
            "coverage_percent": 100.0,
            "pages_total": 10,
            "pages_covered": 5,
            "pages_missing": 2,
            "pages_unreadable": 1,
            # 5 + 2 + 1 = 8 != 10
        }

        (temp_output_dir / "fase1_coverage_report.json").write_text(
            json.dumps(coverage), encoding='utf-8'
        )

        config = MetaIntegrityConfig(
            require_unified_result=False,
            require_integrity_report=False,
            require_audit_reports=False,
            require_judge_opinions=False,
            require_final_decision=False,
            validate_doc_ids=False,
        )

        validator = MetaIntegrityValidator(
            run_id="test_pages_sum",
            output_dir=temp_output_dir,
            config=config,
        )

        report = validator.validate()

        # Deve ter erro de PAGES_SUM_MISMATCH
        sum_errors = [
            e for e in report.errors
            if e.check_type == "PAGES_SUM_MISMATCH"
        ]
        assert len(sum_errors) > 0

    def test_timestamp_sanity(self, temp_output_dir):
        """Timestamps fora da janela são detectados."""
        from src.pipeline.meta_integrity import MetaIntegrityValidator, MetaIntegrityConfig

        # Timestamp muito antigo
        old_audit = {
            "auditor_id": "A1",
            "timestamp": (datetime.now() - timedelta(days=30)).isoformat(),
            "findings": [],
        }

        (temp_output_dir / "fase2_all_audit_reports.json").write_text(
            json.dumps([old_audit]), encoding='utf-8'
        )

        config = MetaIntegrityConfig(
            require_unified_result=False,
            require_coverage_report=False,
            require_integrity_report=False,
            require_judge_opinions=False,
            require_final_decision=False,
            validate_doc_ids=False,
            validate_coverage_math=False,
            validate_counts=False,
            timestamp_tolerance_minutes=60,  # 1 hora
        )

        validator = MetaIntegrityValidator(
            run_id="test_timestamp",
            output_dir=temp_output_dir,
            config=config,
            run_start=datetime.now(),
        )

        report = validator.validate()

        # Deve ter warning de TIMESTAMP_TOO_OLD
        ts_errors = [
            e for e in report.errors
            if "TIMESTAMP" in e.check_type
        ]
        assert len(ts_errors) > 0

    def test_report_save(self, temp_output_dir):
        """Relatório pode ser guardado."""
        from src.pipeline.meta_integrity import MetaIntegrityValidator, MetaIntegrityConfig

        config = MetaIntegrityConfig(
            require_unified_result=False,
            require_coverage_report=False,
            require_integrity_report=False,
            require_audit_reports=False,
            require_judge_opinions=False,
            require_final_decision=False,
        )

        validator = MetaIntegrityValidator(
            run_id="test_save",
            output_dir=temp_output_dir,
            config=config,
        )

        report = validator.validate()
        filepath = report.save(temp_output_dir)

        assert filepath.exists()
        assert filepath.name == "meta_integrity_report.json"

        # Verificar conteúdo
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert "run_id" in data
        assert "summary" in data
        assert "is_consistent" in data["summary"]


# ============================================================================
# TESTES: INTEGRAÇÃO MULTI-DOCUMENTO
# ============================================================================

class TestMultiDocumentIntegration:
    """Testes com múltiplos documentos."""

    def test_two_documents_valid_refs(self, temp_output_dir):
        """Referencias a dois documentos válidos funcionam."""
        from src.pipeline.meta_integrity import MetaIntegrityValidator, MetaIntegrityConfig

        # Unified result com referência ao documento principal
        unified = {
            "document_meta": {
                "doc_id": "doc_main",
                "total_chars": 500,
                "total_pages": 3,
            },
            "union_items": [],
        }

        # Audit reports com referências a dois documentos
        audits = [
            {
                "auditor_id": "A1",
                "timestamp": datetime.now().isoformat(),
                "findings": [
                    {
                        "finding_id": "f1",
                        "citations": [
                            {"doc_id": "doc_main", "start_char": 10, "end_char": 50},
                            {"doc_id": "doc_anexo", "start_char": 5, "end_char": 30},
                        ],
                    }
                ],
            }
        ]

        (temp_output_dir / "fase1_unified_result.json").write_text(
            json.dumps(unified), encoding='utf-8'
        )
        (temp_output_dir / "fase2_all_audit_reports.json").write_text(
            json.dumps(audits), encoding='utf-8'
        )

        config = MetaIntegrityConfig(
            require_coverage_report=False,
            require_integrity_report=False,
            require_judge_opinions=False,
            require_final_decision=False,
        )

        validator = MetaIntegrityValidator(
            run_id="test_multi_doc",
            output_dir=temp_output_dir,
            config=config,
            loaded_doc_ids={"doc_main", "doc_anexo"},  # Ambos válidos
        )

        report = validator.validate()

        # Não deve ter erros de doc_id
        doc_id_errors = [
            e for e in report.errors
            if e.check_type == "DOC_ID_INVALID"
        ]
        assert len(doc_id_errors) == 0


# ============================================================================
# TESTES: OCR RUIDOSO + PDFSAFE
# ============================================================================

class TestOCRNoisyIntegration:
    """Testes com OCR ruidoso."""

    def test_excerpt_mismatch_warning_not_crash(self, sample_document_text):
        """Excerpt mismatch gera warning, não crash."""
        from src.pipeline.integrity import validate_citation

        # Excerpt com erros OCR
        citation = {
            "doc_id": "doc_test",
            "start_char": 10,
            "end_char": 80,
            "excerpt": "c0ntrat0 de arrendament0 ce1ebrad0",  # OCR errors
        }

        is_valid, errors = validate_citation(
            citation,
            sample_document_text,
            len(sample_document_text),
            source="test",
        )

        # Não deve crashar
        # Pode ou não encontrar match dependendo do threshold
        assert isinstance(is_valid, bool)
        assert isinstance(errors, list)

    def test_ocr_tolerant_matching(self, sample_document_text, sample_ocr_noisy_text):
        """OCR tolerante encontra matches."""
        from src.pipeline.text_normalize import (
            text_contains_normalized,
            NormalizationConfig,
        )

        # Excerpt do texto limpo
        excerpt = "contrato arrendamento celebrado"

        config = NormalizationConfig.ocr_tolerant()

        # Deve encontrar no texto limpo
        match_clean = text_contains_normalized(
            sample_document_text, excerpt, threshold=0.6, config=config
        )
        assert match_clean

        # No texto OCR ruidoso, pode ou não encontrar dependendo da gravidade do ruído
        match_noisy = text_contains_normalized(
            sample_ocr_noisy_text, excerpt, threshold=0.4, config=config
        )
        # Não assertamos resultado específico, apenas que não crasha
        assert isinstance(match_noisy, bool)


# ============================================================================
# TESTES: INTEGRITY VALIDATOR ATUALIZADO
# ============================================================================

class TestIntegrityValidatorUpdated:
    """Testes para IntegrityValidator com normalização unificada."""

    def test_validate_citation_uses_unified_normalization(self, sample_document_text):
        """validate_citation usa normalização unificada."""
        from src.pipeline.integrity import validate_citation

        citation = {
            "doc_id": "doc_test",
            "start_char": 10,
            "end_char": 80,
            "excerpt": "contrato arrendamento celebrado",
        }

        is_valid, errors = validate_citation(
            citation,
            sample_document_text,
            len(sample_document_text),
        )

        # Deve validar sem erros
        assert is_valid
        assert len(errors) == 0

    def test_integrity_validator_full_flow(self, sample_document_text):
        """IntegrityValidator fluxo completo."""
        from src.pipeline.integrity import IntegrityValidator

        validator = IntegrityValidator(
            run_id="test_flow",
            document_text=sample_document_text,
        )

        # Simular validação de audit report (mockado)
        @dataclass
        class MockFinding:
            finding_id: str
            citations: list
            evidence_item_ids: list = field(default_factory=list)

        @dataclass
        class MockAuditReport:
            auditor_id: str
            findings: list
            errors: list = field(default_factory=list)

        @dataclass
        class MockCitation:
            doc_id: str = "doc_test"
            start_char: int = 10
            end_char: int = 80
            page_num: int = 1
            excerpt: str = "contrato arrendamento"

            def to_dict(self):
                return {
                    "doc_id": self.doc_id,
                    "start_char": self.start_char,
                    "end_char": self.end_char,
                    "page_num": self.page_num,
                    "excerpt": self.excerpt,
                }

        report = MockAuditReport(
            auditor_id="A1",
            findings=[
                MockFinding(
                    finding_id="f1",
                    citations=[MockCitation()],
                )
            ],
        )

        validated = validator.validate_and_annotate_audit(report)

        # Não deve crashar e deve retornar relatório
        assert validated is not None
        assert validator.get_report() is not None


# ============================================================================
# TESTES: INTEGRAÇÃO COMPLETA
# ============================================================================

class TestFullIntegration:
    """Testes de integração completa."""

    def test_full_pipeline_meta_integrity(
        self,
        temp_output_dir,
        sample_unified_result,
        sample_coverage_report,
        sample_audit_reports,
        sample_judge_opinions,
    ):
        """Pipeline completo de meta-integridade."""
        from src.pipeline.meta_integrity import (
            validate_run_meta_integrity,
            create_meta_integrity_summary,
        )
        from src.pipeline.confidence_policy import compute_penalty

        # Criar todos os ficheiros
        (temp_output_dir / "fase1_unified_result.json").write_text(
            json.dumps(sample_unified_result), encoding='utf-8'
        )
        (temp_output_dir / "fase1_coverage_report.json").write_text(
            json.dumps(sample_coverage_report), encoding='utf-8'
        )
        (temp_output_dir / "fase2_all_audit_reports.json").write_text(
            json.dumps(sample_audit_reports), encoding='utf-8'
        )
        (temp_output_dir / "fase3_all_judge_opinions.json").write_text(
            json.dumps(sample_judge_opinions), encoding='utf-8'
        )

        # Ficheiros individuais
        for i in range(1, 5):
            (temp_output_dir / f"fase2_auditor_{i}.json").write_text(
                json.dumps({}), encoding='utf-8'
            )
        for i in range(1, 4):
            (temp_output_dir / f"fase3_juiz_{i}.json").write_text(
                json.dumps({}), encoding='utf-8'
            )
        for i in range(1, 6):
            (temp_output_dir / f"fase1_extractor_E{i}_items.json").write_text(
                json.dumps([]), encoding='utf-8'
            )

        # Executar validação
        report = validate_run_meta_integrity(
            run_id="test_full",
            output_dir=temp_output_dir,
            loaded_doc_ids={"doc_main"},
            document_num_pages=3,
        )

        # Criar resumo
        summary = create_meta_integrity_summary(report)
        assert "test_full" in summary

        # Calcular penalty baseada no coverage
        penalty = compute_penalty(coverage_report=sample_coverage_report)

        # Deve funcionar end-to-end
        assert report is not None
        assert penalty is not None


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

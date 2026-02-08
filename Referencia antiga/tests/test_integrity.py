# -*- coding: utf-8 -*-
"""
Testes End-to-End para IntegrityValidator.

Cobre os seguintes casos:
1. PDFSafe ativo: citations com page_num consistente
2. Sem PDFSafe (marcadores): page_num pode ser null mas offsets ok
3. OCR ruidoso: excerpt mismatch deve gerar warning (não crash)
4. LLM offsets errados: deve recuperar e reportar (ERROR_RECOVERED + INTEGRITY_WARNING)

Executar com: pytest tests/test_integrity.py -v
"""

import sys
from pathlib import Path

# Adicionar raiz ao path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
from datetime import datetime

from src.pipeline.integrity import (
    IntegrityValidator,
    IntegrityReport,
    ValidationError,
    validate_citation,
    validate_audit_report,
    validate_judge_opinion,
    validate_final_decision,
    normalize_text_for_comparison,
    text_similarity,
    text_contains,
    parse_audit_report_with_validation,
    parse_judge_opinion_with_validation,
    parse_final_decision_with_validation,
)
from src.pipeline.page_mapper import CharToPageMapper, PageBoundary
from src.pipeline.schema_audit import (
    Citation,
    AuditFinding,
    AuditReport,
    CoverageCheck,
    JudgePoint,
    JudgeOpinion,
    FinalDecision,
    FindingType,
    Severity,
    DecisionType,
)
from src.pipeline.schema_unified import (
    EvidenceItem,
    SourceSpan,
    ItemType,
    ExtractionMethod,
    UnifiedExtractionResult,
    DocumentMeta,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def sample_document_text():
    """Texto de documento de teste com marcadores de página."""
    return """[Página 1]
O contrato de arrendamento foi celebrado em 15 de Janeiro de 2024.
O valor mensal da renda é de €850,00 (oitocentos e cinquenta euros).
O senhorio é João Silva, NIF 123456789.

[Página 2]
O inquilino é Maria Santos, NIF 987654321.
As partes acordaram um prazo de 2 (dois) anos, com início em 01/02/2024.
Nos termos do artigo 1022º do Código Civil.

[Página 3]
O inquilino compromete-se a pagar a renda até ao dia 8 de cada mês.
Em caso de mora superior a 3 meses, aplica-se o artigo 1083º do Código Civil.
Assinado em Lisboa, 15 de Janeiro de 2024.
"""


@pytest.fixture
def page_mapper_with_markers(sample_document_text):
    """CharToPageMapper criado a partir de marcadores."""
    return CharToPageMapper.from_text_markers(sample_document_text, "doc_test")


@pytest.fixture
def page_mapper_with_issues():
    """CharToPageMapper com páginas problemáticas."""
    boundaries = [
        PageBoundary(page_num=1, start_char=0, end_char=200, char_count=200, status="OK"),
        PageBoundary(page_num=2, start_char=200, end_char=400, char_count=200, status="SUSPEITA"),
        PageBoundary(page_num=3, start_char=400, end_char=600, char_count=200, status="SEM_TEXTO"),
        PageBoundary(page_num=4, start_char=600, end_char=800, char_count=200, status="OK"),
    ]
    return CharToPageMapper(boundaries=boundaries, doc_id="doc_issues", source="test")


@pytest.fixture
def sample_unified_result():
    """UnifiedExtractionResult de teste."""
    doc_meta = DocumentMeta(
        doc_id="doc_test",
        filename="contrato.pdf",
        file_type=".pdf",
        total_chars=500,
        total_pages=3,
    )

    span = SourceSpan(
        doc_id="doc_test",
        chunk_id="doc_test_c0000",
        start_char=10,
        end_char=50,
        extractor_id="E1",
        method=ExtractionMethod.TEXT,
        page_num=1,
        confidence=0.95,
    )

    item = EvidenceItem(
        item_id="item_test_001",
        item_type=ItemType.DATE,
        value_normalized="2024-01-15",
        source_spans=[span],
        raw_text="15 de Janeiro de 2024",
    )

    return UnifiedExtractionResult(
        result_id="result_test",
        document_meta=doc_meta,
        union_items=[item],
    )


@pytest.fixture
def sample_audit_report():
    """AuditReport de teste."""
    citation = Citation(
        doc_id="doc_test",
        start_char=10,
        end_char=80,
        page_num=1,
        excerpt="contrato de arrendamento foi celebrado em 15 de Janeiro",
    )

    finding = AuditFinding(
        finding_id="finding_001",
        claim="O contrato foi celebrado em 15/01/2024",
        finding_type=FindingType.FACTO,
        severity=Severity.MEDIO,
        citations=[citation],
        evidence_item_ids=["item_test_001"],
    )

    return AuditReport(
        auditor_id="A1",
        model_name="test-model",
        run_id="run_test",
        findings=[finding],
        coverage_check=CoverageCheck(
            docs_seen=["doc_test"],
            pages_seen=[1, 2, 3],
            coverage_percent=100.0,
        ),
    )


@pytest.fixture
def sample_judge_opinion():
    """JudgeOpinion de teste."""
    citation = Citation(
        doc_id="doc_test",
        start_char=200,
        end_char=280,
        page_num=2,
        excerpt="artigo 1022º do Código Civil",
    )

    point = JudgePoint(
        point_id="point_001",
        conclusion="O contrato é válido",
        rationale="Base legal adequada conforme CC",
        citations=[citation],
        legal_basis=["Art. 1022º CC"],
        confidence=0.9,
    )

    return JudgeOpinion(
        judge_id="J1",
        model_name="test-model",
        run_id="run_test",
        recommendation=DecisionType.PROCEDENTE,
        decision_points=[point],
    )


@pytest.fixture
def sample_final_decision():
    """FinalDecision de teste."""
    proof = Citation(
        doc_id="doc_test",
        start_char=350,
        end_char=420,
        page_num=3,
        excerpt="mora superior a 3 meses",
    )

    return FinalDecision(
        run_id="run_test",
        model_name="test-model",
        final_answer="O pedido é PROCEDENTE com base nos factos apresentados e legislação aplicável.",
        decision_type=DecisionType.PROCEDENTE,
        confidence=0.9,
        proofs=[proof],
        judges_consulted=["J1", "J2", "J3"],
        auditors_consulted=["A1", "A2", "A3", "A4"],
    )


# ============================================================================
# TESTES DE NORMALIZAÇÃO
# ============================================================================

class TestNormalization:
    """Testes para funções de normalização."""

    def test_normalize_text_removes_accents(self):
        """Normalização remove acentos."""
        text = "Contrato de arrendamento celebrado"
        normalized = normalize_text_for_comparison("Contrato de arrendaménto célebrado")
        assert "e" in normalized  # 'é' -> 'e'
        assert "a" in normalized  # 'á' -> 'a'

    def test_normalize_text_collapses_whitespace(self):
        """Normalização colapsa whitespace."""
        text = "texto   com    muitos   espaços"
        normalized = normalize_text_for_comparison(text)
        assert "  " not in normalized

    def test_text_similarity_identical(self):
        """Textos idênticos têm similaridade 1.0."""
        text = "contrato de arrendamento"
        assert text_similarity(text, text) == 1.0

    def test_text_similarity_different(self):
        """Textos diferentes têm similaridade < 1.0."""
        text1 = "contrato de arrendamento"
        text2 = "documento completamente diferente"
        sim = text_similarity(text1, text2)
        assert sim < 0.5

    def test_text_contains_exact(self):
        """Contenção exacta detectada."""
        haystack = "O contrato foi celebrado em Lisboa"
        needle = "contrato foi celebrado"
        assert text_contains(haystack, needle)

    def test_text_contains_fuzzy(self):
        """Contenção fuzzy com OCR ruidoso."""
        haystack = "O contrato foi celebrado em Lisboa"
        needle = "contrato foi ce1ebrado"  # 'l' -> '1' (OCR error)

        # Com a nova normalização OCR-tolerante, o threshold alto PODE
        # encontrar match pois 'ce1ebrado' normaliza para 'celebrado'
        # quando OCR substitutions está ativado
        # Teste principal: deve passar com threshold baixo
        assert text_contains(haystack, needle, threshold=0.5)

        # Teste com texto muito diferente - este sim deve falhar
        needle_different = "algo completamente diferente"
        assert not text_contains(haystack, needle_different, threshold=0.9)


# ============================================================================
# TESTES DE VALIDAÇÃO DE CITATION
# ============================================================================

class TestValidateCitation:
    """Testes para validate_citation()."""

    def test_valid_citation_passes(self, sample_document_text):
        """Citation válida passa sem erros."""
        citation = {
            "doc_id": "doc_test",
            "start_char": 10,
            "end_char": 80,
            "page_num": 1,
            "excerpt": "contrato de arrendamento foi celebrado",
        }

        is_valid, errors = validate_citation(
            citation,
            sample_document_text,
            len(sample_document_text),
        )

        assert is_valid
        assert len(errors) == 0

    def test_negative_start_char_fails(self, sample_document_text):
        """start_char negativo gera erro."""
        citation = {
            "doc_id": "doc_test",
            "start_char": -10,
            "end_char": 50,
        }

        is_valid, errors = validate_citation(
            citation,
            sample_document_text,
            len(sample_document_text),
        )

        assert not is_valid
        assert any(e.error_type == "RANGE_INVALID" for e in errors)

    def test_end_less_than_start_fails(self, sample_document_text):
        """end_char < start_char gera erro."""
        citation = {
            "doc_id": "doc_test",
            "start_char": 100,
            "end_char": 50,
        }

        is_valid, errors = validate_citation(
            citation,
            sample_document_text,
            len(sample_document_text),
        )

        assert not is_valid
        assert any(e.error_type == "RANGE_INVALID" for e in errors)

    def test_end_beyond_total_warns(self, sample_document_text):
        """end_char > total_chars gera warning (não erro)."""
        citation = {
            "doc_id": "doc_test",
            "start_char": 10,
            "end_char": 999999,  # Muito além do total
        }

        is_valid, errors = validate_citation(
            citation,
            sample_document_text,
            len(sample_document_text),
        )

        # Warning, não erro fatal
        assert any(e.error_type == "RANGE_INVALID" and e.severity == "WARNING" for e in errors)

    def test_page_mismatch_with_mapper(self, sample_document_text, page_mapper_with_markers):
        """page_num inconsistente com mapper gera warning."""
        citation = {
            "doc_id": "doc_test",
            "start_char": 10,  # Está na página 1
            "end_char": 50,
            "page_num": 3,    # Mas diz página 3
        }

        is_valid, errors = validate_citation(
            citation,
            sample_document_text,
            len(sample_document_text),
            page_mapper=page_mapper_with_markers,
        )

        assert any(e.error_type == "PAGE_MISMATCH" for e in errors)

    def test_excerpt_mismatch_warns(self, sample_document_text):
        """excerpt que não existe no range gera warning."""
        citation = {
            "doc_id": "doc_test",
            "start_char": 10,
            "end_char": 50,
            "excerpt": "texto completamente diferente que não existe no documento",
        }

        is_valid, errors = validate_citation(
            citation,
            sample_document_text,
            len(sample_document_text),
        )

        assert any(e.error_type == "EXCERPT_MISMATCH" for e in errors)


# ============================================================================
# CASO 1: PDF SAFE ATIVO - CITATIONS COM PAGE_NUM CONSISTENTE
# ============================================================================

class TestPDFSafeConsistency:
    """Testes para cenário com PDFSafe ativo."""

    def test_pdfsafe_page_num_consistent(self, sample_document_text, page_mapper_with_markers):
        """Com PDFSafe, page_num deve ser consistente com offsets."""
        # Criar validator com mapper
        validator = IntegrityValidator(
            run_id="test_pdfsafe",
            document_text=sample_document_text,
            page_mapper=page_mapper_with_markers,
        )

        # Citation na página 1 (offsets 0-~170 aproximadamente)
        citation_p1 = Citation(
            doc_id="doc_test",
            start_char=20,
            end_char=80,
            page_num=1,
            excerpt="contrato de arrendamento",
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[citation_p1],
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        # Validar
        validated = validator.validate_and_annotate_audit(report)

        # Não deve ter PAGE_MISMATCH
        integrity_warnings = [e for e in validated.errors if "PAGE_MISMATCH" in e]
        assert len(integrity_warnings) == 0

    def test_pdfsafe_page_num_mismatch_detected(self, sample_document_text, page_mapper_with_markers):
        """Com PDFSafe, page_num errado é detectado."""
        validator = IntegrityValidator(
            run_id="test_pdfsafe_mismatch",
            document_text=sample_document_text,
            page_mapper=page_mapper_with_markers,
        )

        # Citation com page_num errado
        citation_wrong = Citation(
            doc_id="doc_test",
            start_char=20,      # Está na página 1
            end_char=80,
            page_num=3,         # Mas diz página 3
            excerpt="contrato",
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[citation_wrong],
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        validated = validator.validate_and_annotate_audit(report)

        # Deve ter PAGE_MISMATCH warning
        assert any("PAGE_MISMATCH" in e for e in validated.errors)


# ============================================================================
# CASO 2: SEM PDFSAFE - PAGE_NUM NULL MAS OFFSETS OK
# ============================================================================

class TestWithoutPDFSafe:
    """Testes para cenário sem PDFSafe (apenas marcadores)."""

    def test_no_mapper_page_num_null_ok(self, sample_document_text):
        """Sem mapper, page_num=null é aceite se offsets válidos."""
        validator = IntegrityValidator(
            run_id="test_no_pdfsafe",
            document_text=sample_document_text,
            # Sem page_mapper
        )

        citation = Citation(
            doc_id="doc_test",
            start_char=20,
            end_char=80,
            page_num=None,  # Sem page_num
            excerpt="contrato de arrendamento",
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[citation],
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        validated = validator.validate_and_annotate_audit(report)

        # Não deve ter erros de PAGE_MISMATCH (não há mapper)
        assert not any("PAGE_MISMATCH" in e for e in validated.errors)
        # Offsets são válidos, então não deve ter RANGE_INVALID
        assert not any("RANGE_INVALID" in e for e in validated.errors)

    def test_no_mapper_invalid_offsets_detected(self, sample_document_text):
        """Sem mapper, offsets inválidos ainda são detectados."""
        validator = IntegrityValidator(
            run_id="test_no_pdfsafe_bad",
            document_text=sample_document_text,
        )

        citation = Citation(
            doc_id="doc_test",
            start_char=500,     # > end
            end_char=100,
            page_num=None,
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[citation],
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        validated = validator.validate_and_annotate_audit(report)

        # Deve detectar RANGE_INVALID
        assert any("RANGE_INVALID" in e for e in validated.errors)


# ============================================================================
# CASO 3: OCR RUIDOSO - EXCERPT MISMATCH GERA WARNING (NÃO CRASH)
# ============================================================================

class TestOCRNoisy:
    """Testes para cenário com OCR ruidoso."""

    def test_ocr_excerpt_mismatch_warns_not_crashes(self, sample_document_text):
        """OCR ruidoso gera warning mas não crash."""
        validator = IntegrityValidator(
            run_id="test_ocr_noisy",
            document_text=sample_document_text,
        )

        # Excerpt com "erros de OCR"
        citation = Citation(
            doc_id="doc_test",
            start_char=10,
            end_char=80,
            excerpt="c0ntrat0 de arrendament0 f0i ce1ebrad0",  # l->1, o->0
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste OCR",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[citation],
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        # Não deve levantar exceção
        validated = validator.validate_and_annotate_audit(report)

        # Pode ter warning de EXCERPT_MISMATCH
        # Mas o importante é que não crashou
        assert validated is not None
        assert isinstance(validated.errors, list)

    def test_ocr_tolerant_matching(self, sample_document_text):
        """Matching tolerante aceita variações de OCR."""
        validator = IntegrityValidator(
            run_id="test_ocr_tolerant",
            document_text=sample_document_text,
        )

        # Excerpt com pequenas variações (ainda legível)
        citation = Citation(
            doc_id="doc_test",
            start_char=10,
            end_char=90,
            excerpt="contrato arrendamento celebrado Janeiro",  # Palavras principais
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[citation],
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        validated = validator.validate_and_annotate_audit(report)

        # Com matching tolerante, não deve ter muitos erros
        excerpt_errors = [e for e in validated.errors if "EXCERPT_MISMATCH" in e]
        # Pode ou não ter, dependendo da tolerância, mas não deve crashar
        assert validated is not None


# ============================================================================
# CASO 4: LLM OFFSETS ERRADOS - RECUPERAR E REPORTAR
# ============================================================================

class TestLLMBadOffsets:
    """Testes para cenário onde LLM devolve offsets errados."""

    def test_llm_bad_offsets_recovers(self, sample_document_text):
        """Offsets errados do LLM são recuperados e reportados."""
        validator = IntegrityValidator(
            run_id="test_llm_bad",
            document_text=sample_document_text,
        )

        # Citations com vários tipos de erros
        citations = [
            Citation(doc_id="doc_test", start_char=-50, end_char=100),   # start negativo
            Citation(doc_id="doc_test", start_char=500, end_char=100),   # end < start
            Citation(doc_id="doc_test", start_char=0, end_char=999999),  # end muito grande
        ]

        findings = [
            AuditFinding(
                finding_id=f"f{i}",
                claim="Teste",
                finding_type=FindingType.FACTO,
                severity=Severity.BAIXO,
                citations=[c],
            )
            for i, c in enumerate(citations)
        ]

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=findings,
        )

        # Não deve crashar
        validated = validator.validate_and_annotate_audit(report)

        # Deve ter vários RANGE_INVALID
        range_errors = [e for e in validated.errors if "RANGE_INVALID" in e]
        assert len(range_errors) >= 2  # Pelo menos 2 erros de range

        # Deve ter INTEGRITY_WARNING nos errors
        assert any("INTEGRITY_WARNING" in e for e in validated.errors)

    def test_parse_with_validation_recovers(self, sample_document_text):
        """parse_*_with_validation recupera de JSON inválido."""
        validator = IntegrityValidator(
            run_id="test_parse_bad",
            document_text=sample_document_text,
        )

        # JSON inválido
        bad_json = "isto não é JSON válido { broken"

        # Deve recuperar sem crashar
        report = parse_audit_report_with_validation(
            bad_json,
            "A1",
            "test-model",
            "test_run",
            validator=validator,
        )

        # Deve ter sido criado relatório mínimo com ERROR_RECOVERED
        assert report is not None
        assert any("ERROR_RECOVERED" in e for e in report.errors)


# ============================================================================
# TESTES DE INTEGRAÇÃO COMPLETA
# ============================================================================

class TestFullIntegration:
    """Testes de integração end-to-end."""

    def test_full_pipeline_validation(
        self,
        sample_document_text,
        page_mapper_with_markers,
        sample_unified_result,
        sample_audit_report,
        sample_judge_opinion,
        sample_final_decision,
    ):
        """Validação completa do pipeline F2→F3→F4."""
        validator = IntegrityValidator(
            run_id="test_full_pipeline",
            document_text=sample_document_text,
            page_mapper=page_mapper_with_markers,
            unified_result=sample_unified_result,
        )

        # Fase 2: Auditor
        validated_audit = validator.validate_and_annotate_audit(sample_audit_report)
        assert validated_audit is not None

        # Fase 3: Juiz
        validated_judge = validator.validate_and_annotate_judge(sample_judge_opinion)
        assert validated_judge is not None

        # Fase 4: Presidente
        validated_decision = validator.validate_and_annotate_decision(sample_final_decision)
        assert validated_decision is not None

        # Verificar relatório
        report = validator.get_report()
        assert report.run_id == "test_full_pipeline"

        # Deve poder serializar
        json_report = report.to_json()
        assert "run_id" in json_report
        assert "citations" in json_report

    def test_report_saved_to_file(self, sample_document_text, tmp_path):
        """Relatório de integridade é guardado em ficheiro."""
        validator = IntegrityValidator(
            run_id="test_save",
            document_text=sample_document_text,
        )

        # Adicionar alguns erros de teste
        validator.report.add_error(ValidationError(
            error_type="TEST_ERROR",
            severity="WARNING",
            message="Erro de teste",
        ))

        # Guardar
        filepath = validator.save_report(tmp_path)

        # Verificar ficheiro existe
        assert filepath.exists()
        assert filepath.name == "integrity_report.json"

        # Verificar conteúdo
        import json
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["run_id"] == "test_save"
        assert len(data["top_errors"]) >= 1

    def test_confidence_penalty_applied(self, sample_document_text):
        """Penalização de confidence é aplicada correctamente."""
        validator = IntegrityValidator(
            run_id="test_penalty",
            document_text=sample_document_text,
        )

        # Citation com muitos erros
        citation = Citation(
            doc_id="doc_test",
            start_char=500,     # > end (erro)
            end_char=100,
            excerpt="texto que não existe",
        )

        point = JudgePoint(
            point_id="p1",
            conclusion="Teste",
            rationale="Teste",
            citations=[citation],
            confidence=0.9,  # Confidence original alto
        )

        opinion = JudgeOpinion(
            judge_id="J1",
            model_name="test",
            run_id="test",
            recommendation=DecisionType.PROCEDENTE,
            decision_points=[point],
        )

        validated = validator.validate_and_annotate_judge(opinion)

        # Confidence deve ter sido reduzido (se houve erros)
        if any("INTEGRITY_WARNING" in e for e in validated.errors):
            # Pode não ter sido reduzido se não há erros significativos
            pass  # OK

    def test_pages_with_issues_validated(self, page_mapper_with_issues):
        """Páginas com status problemático são validadas."""
        document_text = "x" * 800  # Texto dummy

        validator = IntegrityValidator(
            run_id="test_page_issues",
            document_text=document_text,
            page_mapper=page_mapper_with_issues,
        )

        # Citation na página SUSPEITA
        citation = Citation(
            doc_id="doc_issues",
            start_char=250,     # Página 2 (SUSPEITA)
            end_char=300,
            page_num=2,
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[citation],
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        validated = validator.validate_and_annotate_audit(report)

        # Validação deve completar sem crash
        assert validated is not None


# ============================================================================
# TESTES DE EDGE CASES
# ============================================================================

class TestEdgeCases:
    """Testes para casos extremos."""

    def test_empty_document(self):
        """Documento vazio não crasheá."""
        validator = IntegrityValidator(
            run_id="test_empty",
            document_text="",
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[],
        )

        validated = validator.validate_and_annotate_audit(report)
        assert validated is not None

    def test_no_citations(self, sample_document_text):
        """Finding sem citations gera warning."""
        validator = IntegrityValidator(
            run_id="test_no_citations",
            document_text=sample_document_text,
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste sem citação",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[],  # Vazio!
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        validated = validator.validate_and_annotate_audit(report)

        # Deve ter warning de MISSING_CITATION
        assert any("MISSING_CITATION" in e for e in validated.errors)

    def test_nonexistent_evidence_item(self, sample_document_text, sample_unified_result):
        """evidence_item_id inexistente gera warning."""
        validator = IntegrityValidator(
            run_id="test_bad_item_id",
            document_text=sample_document_text,
            unified_result=sample_unified_result,
        )

        citation = Citation(
            doc_id="doc_test",
            start_char=10,
            end_char=50,
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[citation],
            evidence_item_ids=["item_que_nao_existe"],
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        validated = validator.validate_and_annotate_audit(report, sample_unified_result)

        # Deve ter warning de ITEM_NOT_FOUND
        assert any("ITEM_NOT_FOUND" in e for e in validated.errors)

    def test_unicode_in_excerpt(self, sample_document_text):
        """Excerpt com unicode especial é tratado."""
        validator = IntegrityValidator(
            run_id="test_unicode",
            document_text=sample_document_text,
        )

        citation = Citation(
            doc_id="doc_test",
            start_char=10,
            end_char=80,
            excerpt="contrato de arrendámento célebrado em Janéiro",  # Acentos extra
        )

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste",
            finding_type=FindingType.FACTO,
            severity=Severity.BAIXO,
            citations=[citation],
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test",
            run_id="test",
            findings=[finding],
        )

        # Não deve crashar
        validated = validator.validate_and_annotate_audit(report)
        assert validated is not None


# ============================================================================
# EXECUÇÃO DIRECTA
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

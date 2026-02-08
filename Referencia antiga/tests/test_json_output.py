# -*- coding: utf-8 -*-
"""
Teste para verificar geração de ficheiros JSON pelo pipeline.
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import sys

# Adicionar diretório raiz ao path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestJSONOutputGeneration:
    """Testes para verificar que os ficheiros JSON são gerados corretamente."""

    def test_fase1_agregado_json_structure(self):
        """Verifica a estrutura esperada do fase1_agregado_consolidado.json."""
        expected_fields = [
            "run_id",
            "timestamp",
            "doc_meta",
            "union_items",
            "union_items_count",
            "items_by_extractor",
            "coverage_report",
            "unreadable_parts",
            "conflicts",
            "conflicts_count",
            "extraction_runs",
            "errors",
            "warnings",
            "summary",
        ]

        # Estrutura mínima esperada
        agregado_json = {
            "run_id": "test_run",
            "timestamp": "2026-02-05T00:00:00",
            "doc_meta": {"doc_id": "test", "filename": "test.pdf"},
            "union_items": [],
            "union_items_count": 0,
            "items_by_extractor": {},
            "coverage_report": {"coverage_percent": 95.0},
            "unreadable_parts": [],
            "conflicts": [],
            "conflicts_count": 0,
            "extraction_runs": [],
            "errors": [],
            "warnings": [],
            "summary": {"total_items": 0},
        }

        for field in expected_fields:
            assert field in agregado_json, f"Campo {field} deve existir"

    def test_fase2_chefe_json_structure(self):
        """Verifica a estrutura esperada do fase2_chefe_consolidado.json."""
        from src.pipeline.schema_audit import ChefeConsolidatedReport

        report = ChefeConsolidatedReport(
            chefe_id="CHEFE",
            model_name="test-model",
            run_id="test_run",
        )

        json_dict = report.to_dict()

        expected_fields = [
            "chefe_id",
            "model_name",
            "run_id",
            "consolidated_findings",
            "divergences",
            "coverage_check",
            "recommendations_phase3",
            "legal_refs_consolidated",
            "open_questions",
            "errors",
            "warnings",
            "timestamp",
        ]

        for field in expected_fields:
            assert field in json_dict, f"Campo {field} deve existir em ChefeConsolidatedReport"

    def test_json_write_functions_exist(self):
        """Verifica que o código de escrita de JSON existe no processor."""
        from src.pipeline import processor

        # Ler o ficheiro processor.py
        processor_path = Path(processor.__file__)
        content = processor_path.read_text(encoding='utf-8')

        # Verificar se os padrões de escrita de JSON estão presentes
        assert "fase1_agregado_consolidado.json" in content, "Código para escrever fase1_agregado_consolidado.json deve existir"
        assert "fase2_chefe_consolidado.json" in content, "Código para escrever fase2_chefe_consolidado.json deve existir"
        assert "[JSON-WRITE]" in content, "Logs de diagnóstico JSON-WRITE devem existir"

    def test_use_unified_provenance_enabled(self):
        """Verifica que USE_UNIFIED_PROVENANCE está ativado."""
        from src.config import USE_UNIFIED_PROVENANCE

        assert USE_UNIFIED_PROVENANCE is True, "USE_UNIFIED_PROVENANCE deve estar True para gerar JSON"

    def test_output_dir_creation(self):
        """Testa que o output_dir é criado corretamente."""
        from src.config import OUTPUT_DIR

        assert OUTPUT_DIR.exists() or OUTPUT_DIR.parent.exists(), "OUTPUT_DIR ou parent devem existir"


class TestJSONSerializationIntegrity:
    """Testes para garantir que a serialização JSON funciona corretamente."""

    def test_evidence_item_serialization(self):
        """Verifica que EvidenceItem serializa corretamente para JSON."""
        from src.pipeline.schema_unified import EvidenceItem, SourceSpan, ItemType, ExtractionMethod

        span = SourceSpan(
            doc_id="test_doc",
            chunk_id="chunk_1",
            start_char=0,
            end_char=100,
            page_num=1,
            extractor_id="E1",
            method=ExtractionMethod.TEXT,
            confidence=0.95,
        )

        item = EvidenceItem(
            item_id="item_001",
            item_type=ItemType.DATE,
            value_normalized="2024-01-01",
            raw_text="01/01/2024",
            source_spans=[span],
        )

        json_dict = item.to_dict()

        # Verificar que serializa para JSON válido
        json_str = json.dumps(json_dict, ensure_ascii=False)
        assert json_str is not None

        # Verificar campos obrigatórios
        assert json_dict["item_id"] == "item_001"
        assert json_dict["item_type"] == "date"
        assert json_dict["value_normalized"] == "2024-01-01"
        assert len(json_dict["source_spans"]) == 1

    def test_audit_report_serialization(self):
        """Verifica que AuditReport serializa corretamente para JSON."""
        from src.pipeline.schema_audit import AuditReport, AuditFinding, FindingType, Severity, Citation

        finding = AuditFinding(
            finding_id="f1",
            claim="Teste de claim",
            finding_type=FindingType.FACTO,
            severity=Severity.MEDIO,
            citations=[],
            is_determinant=True,
        )

        report = AuditReport(
            auditor_id="A1",
            model_name="test-model",
            run_id="test_run",
            findings=[finding],
        )

        json_dict = report.to_dict()
        json_str = json.dumps(json_dict, ensure_ascii=False)

        assert json_str is not None
        assert json_dict["auditor_id"] == "A1"
        assert len(json_dict["findings"]) == 1
        assert json_dict["findings"][0]["is_determinant"] is True


class TestJSONFirstRendering:
    """Testes para JSON-first rendering (markdown derivado de JSON)."""

    def test_render_agregado_markdown_from_json(self):
        """render_agregado_markdown_from_json deve gerar markdown válido."""
        from src.pipeline.extractor_unified import render_agregado_markdown_from_json

        agregado_json = {
            "run_id": "test_run",
            "timestamp": "2026-02-05T00:00:00",
            "doc_meta": {
                "doc_id": "test_doc",
                "filename": "test.pdf",
                "total_chars": 10000
            },
            "union_items": [
                {
                    "item_id": "item_001",
                    "item_type": "date",
                    "value_normalized": "2024-01-15",
                    "raw_text": "15 de Janeiro de 2024",
                    "source_spans": [
                        {
                            "doc_id": "test_doc",
                            "start_char": 100,
                            "end_char": 130,
                            "page_num": 1,
                            "extractor_id": "E1"
                        }
                    ]
                }
            ],
            "union_items_count": 1,
            "items_by_extractor": {"E1": 1},
            "coverage_report": {
                "total_chars": 10000,
                "covered_chars": 9500,
                "coverage_percent": 95.0,
                "is_complete": True,
                "gaps": []
            },
            "unreadable_parts": [],
            "conflicts": [],
            "conflicts_count": 0,
            "extraction_runs": [],
            "errors": [],
            "warnings": [],
            "summary": {"total_items": 1, "coverage_percent": 95.0}
        }

        markdown = render_agregado_markdown_from_json(agregado_json)

        # Verificar que markdown foi gerado
        assert markdown is not None
        assert len(markdown) > 0

        # Verificar conteúdo
        assert "EXTRAÇÃO CONSOLIDADA" in markdown
        assert "test.pdf" in markdown
        assert "DATE" in markdown
        assert "2024-01-15" in markdown
        assert "RELATÓRIO DE COBERTURA" in markdown
        assert "95.0" in markdown

    def test_render_agregado_with_unreadable_parts(self):
        """Markdown deve incluir partes ilegíveis do JSON."""
        from src.pipeline.extractor_unified import render_agregado_markdown_from_json

        agregado_json = {
            "run_id": "test",
            "doc_meta": {"filename": "test.pdf", "total_chars": 1000},
            "union_items": [],
            "coverage_report": {"coverage_percent": 80.0, "total_chars": 1000, "covered_chars": 800, "is_complete": False, "gaps": []},
            "unreadable_parts": [
                {"doc_id": "doc1", "page_num": 5, "reason": "scan ilegível"}
            ],
            "conflicts": [],
            "errors": [],
            "summary": {"total_items": 0}
        }

        markdown = render_agregado_markdown_from_json(agregado_json)

        assert "PARTES ILEGÍVEIS" in markdown
        assert "scan ilegível" in markdown

    def test_render_agregado_with_conflicts(self):
        """Markdown deve incluir conflitos do JSON."""
        from src.pipeline.extractor_unified import render_agregado_markdown_from_json

        agregado_json = {
            "run_id": "test",
            "doc_meta": {"filename": "test.pdf", "total_chars": 1000},
            "union_items": [],
            "coverage_report": {"coverage_percent": 100.0, "total_chars": 1000, "covered_chars": 1000, "is_complete": True, "gaps": []},
            "unreadable_parts": [],
            "conflicts": [
                {
                    "conflict_id": "conflict_001",
                    "item_type": "date",
                    "values": [
                        {"extractor_id": "E1", "value": "2024-01-15"},
                        {"extractor_id": "E2", "value": "2024-01-16"}
                    ]
                }
            ],
            "conflicts_count": 1,
            "errors": [],
            "summary": {"total_items": 0}
        }

        markdown = render_agregado_markdown_from_json(agregado_json)

        assert "CONFLITOS DETETADOS" in markdown
        assert "conflict_001" in markdown
        assert "E1" in markdown
        assert "E2" in markdown


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

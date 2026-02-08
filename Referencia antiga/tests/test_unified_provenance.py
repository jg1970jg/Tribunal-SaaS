# -*- coding: utf-8 -*-
"""
Testes para o sistema unificado de proveniência e cobertura.
"""

import sys
from pathlib import Path

# Adicionar diretório raiz ao path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
from datetime import datetime


class TestSchemaUnified:
    """Testes para schema_unified.py"""

    def test_source_span_creation(self):
        """Testa criação de SourceSpan com offsets válidos."""
        from src.pipeline.schema_unified import SourceSpan, ExtractionMethod

        span = SourceSpan(
            doc_id="doc_test",
            chunk_id="doc_test_c0000",
            start_char=100,
            end_char=150,
            extractor_id="E1",
            method=ExtractionMethod.TEXT,
            confidence=0.95,
            raw_text="texto de teste",
        )

        assert span.doc_id == "doc_test"
        assert span.start_char == 100
        assert span.end_char == 150
        assert span.extractor_id == "E1"
        assert span.confidence == 0.95
        assert span.span_key == "doc_test:100-150"

    def test_source_span_invalid_offsets(self):
        """Testa que offsets inválidos geram erro."""
        from src.pipeline.schema_unified import SourceSpan

        # start_char negativo
        with pytest.raises(ValueError):
            SourceSpan(
                doc_id="doc_test",
                chunk_id="doc_test_c0000",
                start_char=-10,
                end_char=50,
                extractor_id="E1",
            )

        # end_char < start_char
        with pytest.raises(ValueError):
            SourceSpan(
                doc_id="doc_test",
                chunk_id="doc_test_c0000",
                start_char=100,
                end_char=50,
                extractor_id="E1",
            )

    def test_evidence_item_requires_source_spans(self):
        """Testa que EvidenceItem requer source_spans."""
        from src.pipeline.schema_unified import EvidenceItem, ItemType

        # Sem source_spans deve falhar
        with pytest.raises(ValueError):
            EvidenceItem(
                item_id="item_test",
                item_type=ItemType.DATE,
                value_normalized="2024-01-15",
                source_spans=[],  # Vazio!
            )

    def test_evidence_item_with_source_spans(self):
        """Testa criação de EvidenceItem com source_spans."""
        from src.pipeline.schema_unified import (
            EvidenceItem, ItemType, SourceSpan, ExtractionMethod
        )

        span = SourceSpan(
            doc_id="doc_test",
            chunk_id="doc_test_c0000",
            start_char=100,
            end_char=110,
            extractor_id="E1",
        )

        item = EvidenceItem(
            item_id="",
            item_type=ItemType.DATE,
            value_normalized="2024-01-15",
            source_spans=[span],
            raw_text="15/01/2024",
        )

        assert item.item_type == ItemType.DATE
        assert item.value_normalized == "2024-01-15"
        assert len(item.source_spans) == 1
        assert item.primary_span == span
        assert "E1" in item.extractor_ids

    def test_chunk_creation(self):
        """Testa criação de Chunk com offsets."""
        from src.pipeline.schema_unified import Chunk, ExtractionMethod

        chunk = Chunk(
            doc_id="doc_test",
            chunk_id="",
            chunk_index=0,
            total_chunks=3,
            start_char=0,
            end_char=50000,
            overlap=0,
            text="x" * 50000,
            method=ExtractionMethod.TEXT,
        )

        assert chunk.chunk_id == "doc_test_c0000"
        assert chunk.char_length == 50000
        assert chunk.start_char == 0
        assert chunk.end_char == 50000

    def test_calculate_chunks_for_document(self):
        """Testa cálculo de chunks para documento."""
        from src.pipeline.schema_unified import calculate_chunks_for_document

        # Documento pequeno (não divide)
        chunks_small = calculate_chunks_for_document(30000, chunk_size=50000, overlap=2500)
        assert len(chunks_small) == 1
        assert chunks_small[0] == (0, 30000)

        # Documento grande (divide em chunks)
        chunks_large = calculate_chunks_for_document(150000, chunk_size=50000, overlap=2500)
        assert len(chunks_large) > 1

        # Verificar overlaps
        for i in range(1, len(chunks_large)):
            prev_end = chunks_large[i - 1][1]
            curr_start = chunks_large[i][0]
            # Deve haver overlap de 2500 chars
            assert prev_end > curr_start

    def test_coverage_calculation(self):
        """Testa cálculo de cobertura."""
        from src.pipeline.schema_unified import Coverage, CharRange

        coverage = Coverage(total_chars=10000)
        coverage.char_ranges_covered = [
            CharRange(start=0, end=3000, extractor_id="E1"),
            CharRange(start=2500, end=6000, extractor_id="E2"),  # Overlap com E1
            CharRange(start=7000, end=10000, extractor_id="E3"),
        ]
        coverage.calculate_coverage()

        # Deve ter um gap de 6000-7000
        assert len(coverage.char_ranges_missing) == 1
        assert coverage.char_ranges_missing[0].start == 6000
        assert coverage.char_ranges_missing[0].end == 7000
        assert coverage.coverage_percent == 90.0  # 9000/10000
        assert not coverage.is_complete  # Gap > 100 chars


class TestExtractorUnified:
    """Testes para extractor_unified.py"""

    def test_fallback_extract_with_offsets(self):
        """Testa extração fallback por regex."""
        from src.pipeline.schema_unified import Chunk, ExtractionMethod
        from src.pipeline.extractor_unified import _fallback_extract_with_offsets

        texto = """
        CONTRATO celebrado em 15/01/2024.
        Valor: €1.500,00 (mil e quinhentos euros).
        Conforme artigo 1022º do Código Civil.
        """

        chunk = Chunk(
            doc_id="doc_test",
            chunk_id="doc_test_c0000",
            chunk_index=0,
            total_chunks=1,
            start_char=0,
            end_char=len(texto),
            overlap=0,
            text=texto,
            method=ExtractionMethod.TEXT,
        )

        items = _fallback_extract_with_offsets(chunk, "E1")

        # Deve encontrar pelo menos data, valor e referência legal
        types_found = {item.item_type.value for item in items}
        assert "date" in types_found
        assert "amount" in types_found
        assert "legal_ref" in types_found

        # Verificar que todos os items têm source_spans
        for item in items:
            assert len(item.source_spans) > 0
            span = item.primary_span
            assert span.extractor_id == "E1"
            assert span.start_char >= 0
            assert span.end_char <= len(texto)

    def test_aggregate_with_provenance(self):
        """Testa agregação preservando proveniência."""
        from src.pipeline.schema_unified import (
            EvidenceItem, ItemType, SourceSpan
        )
        from src.pipeline.extractor_unified import aggregate_with_provenance

        # Criar items de dois extratores
        span_e1 = SourceSpan(
            doc_id="doc_test",
            chunk_id="doc_test_c0000",
            start_char=100,
            end_char=110,
            extractor_id="E1",
        )
        span_e2 = SourceSpan(
            doc_id="doc_test",
            chunk_id="doc_test_c0000",
            start_char=100,
            end_char=112,
            extractor_id="E2",
        )

        item_e1 = EvidenceItem(
            item_id="item_e1",
            item_type=ItemType.DATE,
            value_normalized="2024-01-15",
            source_spans=[span_e1],
        )
        item_e2 = EvidenceItem(
            item_id="item_e2",
            item_type=ItemType.DATE,
            value_normalized="2024-01-15",  # Mesmo valor
            source_spans=[span_e2],
        )

        items_by_extractor = {
            "E1": [item_e1],
            "E2": [item_e2],
        }

        union_items, conflicts = aggregate_with_provenance(items_by_extractor)

        # Deve manter ambos os items (SEM deduplicação)
        assert len(union_items) == 2

        # Não deve haver conflitos (mesmo valor)
        assert len(conflicts) == 0

    def test_aggregate_detects_conflicts(self):
        """Testa que agregação detecta conflitos."""
        from src.pipeline.schema_unified import (
            EvidenceItem, ItemType, SourceSpan
        )
        from src.pipeline.extractor_unified import aggregate_with_provenance

        # Criar items com valores diferentes para o mesmo local
        span_e1 = SourceSpan(
            doc_id="doc_test",
            chunk_id="doc_test_c0000",
            start_char=100,
            end_char=110,
            extractor_id="E1",
        )
        span_e2 = SourceSpan(
            doc_id="doc_test",
            chunk_id="doc_test_c0000",
            start_char=100,
            end_char=110,
            extractor_id="E2",
        )

        item_e1 = EvidenceItem(
            item_id="item_e1",
            item_type=ItemType.AMOUNT,
            value_normalized="€1.500,00",
            source_spans=[span_e1],
        )
        item_e2 = EvidenceItem(
            item_id="item_e2",
            item_type=ItemType.AMOUNT,
            value_normalized="€1.500",  # Valor diferente!
            source_spans=[span_e2],
        )

        items_by_extractor = {
            "E1": [item_e1],
            "E2": [item_e2],
        }

        union_items, conflicts = aggregate_with_provenance(items_by_extractor)

        # Deve manter ambos os items
        assert len(union_items) == 2

        # Deve detectar conflito
        assert len(conflicts) == 1

    def test_calculate_coverage(self):
        """Testa cálculo de cobertura."""
        from src.pipeline.schema_unified import (
            Chunk, EvidenceItem, ItemType, SourceSpan, ExtractionMethod
        )
        from src.pipeline.extractor_unified import calculate_coverage

        # Criar chunks
        chunks = [
            Chunk(
                doc_id="doc_test",
                chunk_id="doc_test_c0000",
                chunk_index=0,
                total_chunks=1,
                start_char=0,
                end_char=1000,
                overlap=0,
                text="x" * 1000,
                method=ExtractionMethod.TEXT,
            )
        ]

        # Criar items com spans
        span = SourceSpan(
            doc_id="doc_test",
            chunk_id="doc_test_c0000",
            start_char=100,
            end_char=200,
            extractor_id="E1",
        )
        items = [
            EvidenceItem(
                item_id="item_1",
                item_type=ItemType.FACT,
                value_normalized="facto teste",
                source_spans=[span],
            )
        ]

        coverage = calculate_coverage(chunks, items, total_chars=1000)

        assert coverage["total_chars"] == 1000
        assert coverage["coverage_percent"] == 100.0  # Chunk cobre tudo
        assert coverage["is_complete"] == True


class TestItemsToMarkdown:
    """Testes para conversão para Markdown."""

    def test_items_to_markdown(self):
        """Testa conversão de items para markdown."""
        from src.pipeline.schema_unified import (
            EvidenceItem, ItemType, SourceSpan
        )
        from src.pipeline.extractor_unified import items_to_markdown

        span = SourceSpan(
            doc_id="doc_test",
            chunk_id="doc_test_c0000",
            start_char=100,
            end_char=120,
            extractor_id="E1",
        )

        items = [
            EvidenceItem(
                item_id="item_date",
                item_type=ItemType.DATE,
                value_normalized="2024-01-15",
                source_spans=[span],
                raw_text="15/01/2024",
            )
        ]

        md = items_to_markdown(items, include_provenance=True)

        assert "DATE" in md
        assert "2024-01-15" in md
        assert "E1" in md
        assert "100" in md  # start_char


if __name__ == "__main__":
    # Executar testes
    pytest.main([__file__, "-v"])

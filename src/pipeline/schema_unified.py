"""
Schema Unificado para Extração com Proveniência e Cobertura.

Este módulo define as estruturas de dados para rastreabilidade completa:
- Cada facto/data/valor/ref legal tem source_spans obrigatórios
- Cobertura auditável por caracteres e páginas
- Sem deduplicação, mas com preservação de fontes

REGRAS NÃO-NEGOCIÁVEIS:
1. Nada pode ser perdido - cobertura auditável
2. Sem deduplicação - mantém união bruta com fontes
3. Rastreio reverso - qualquer item mapeia de volta ao original
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any
from enum import Enum
import hashlib
import json
import uuid


# ============================================================================
# ENUMS E TIPOS
# ============================================================================

class ItemType(str, Enum):
    """Tipos de itens extraídos."""
    FACT = "fact"
    DATE = "date"
    AMOUNT = "amount"
    LEGAL_REF = "legal_ref"
    VISUAL = "visual"
    TABLE = "table"
    ENTITY = "entity"
    OTHER = "other"


class ExtractionMethod(str, Enum):
    """Método de extração usado."""
    TEXT = "text"      # Texto direto (TXT, DOCX)
    OCR = "ocr"        # OCR de PDF/imagem
    HYBRID = "hybrid"  # Misto


class ExtractionStatus(str, Enum):
    """Status da extração."""
    SUCCESS = "success"
    PARTIAL = "partial"  # Alguns chunks falharam
    FAILED = "failed"
    PENDING = "pending"


# ============================================================================
# DATACLASSES PRINCIPAIS
# ============================================================================

@dataclass
class DocumentMeta:
    """Metadados do documento."""
    doc_id: str
    filename: str
    file_type: str  # ".pdf", ".txt", ".docx"
    total_chars: int
    total_pages: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    checksum: Optional[str] = None  # SHA256 do conteúdo

    def __post_init__(self):
        if not self.doc_id:
            self.doc_id = f"doc_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "filename": self.filename,
            "file_type": self.file_type,
            "total_chars": self.total_chars,
            "total_pages": self.total_pages,
            "created_at": self.created_at.isoformat(),
            "checksum": self.checksum,
        }


@dataclass
class Chunk:
    """
    Representa um chunk do documento com offsets precisos.

    Com chunk_size=50000 e overlap=2500:
    - step = 50000 - 2500 = 47500
    - chunk0: [0, 50000)
    - chunk1: [47500, 97500)
    - chunk2: [95000, 145000)
    - etc.
    """
    doc_id: str
    chunk_id: str
    chunk_index: int
    total_chunks: int
    start_char: int
    end_char: int
    overlap: int
    text: str
    method: ExtractionMethod = ExtractionMethod.TEXT
    page_start: Optional[int] = None  # Página inicial (se mapeável)
    page_end: Optional[int] = None    # Página final (se mapeável)

    def __post_init__(self):
        if not self.chunk_id:
            self.chunk_id = f"{self.doc_id}_c{self.chunk_index:04d}"

    @property
    def char_length(self) -> int:
        return self.end_char - self.start_char

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "overlap": self.overlap,
            "char_length": self.char_length,
            "method": self.method.value,
            "page_start": self.page_start,
            "page_end": self.page_end,
            # text omitido para não duplicar
        }


@dataclass
class SourceSpan:
    """
    Localização exata de um item no documento original.
    OBRIGATÓRIO para cada EvidenceItem.
    """
    doc_id: str
    chunk_id: str
    start_char: int  # Offset absoluto no documento
    end_char: int    # Offset absoluto no documento
    extractor_id: str  # E1, E2, E3, E4, E5
    method: ExtractionMethod = ExtractionMethod.TEXT
    page_num: Optional[int] = None
    confidence: float = 1.0  # 0.0-1.0
    raw_text: Optional[str] = None  # Texto original (opcional, para debug)

    def __post_init__(self):
        if self.start_char < 0:
            raise ValueError(f"start_char não pode ser negativo: {self.start_char}")
        if self.end_char < self.start_char:
            raise ValueError(f"end_char ({self.end_char}) < start_char ({self.start_char})")

    @property
    def span_key(self) -> str:
        """Chave única para este span (para detecção de conflitos)."""
        return f"{self.doc_id}:{self.start_char}-{self.end_char}"

    def overlaps_with(self, other: 'SourceSpan', min_overlap: int = 10) -> bool:
        """Verifica se dois spans se sobrepõem significativamente."""
        if self.doc_id != other.doc_id:
            return False
        overlap_start = max(self.start_char, other.start_char)
        overlap_end = min(self.end_char, other.end_char)
        return (overlap_end - overlap_start) >= min_overlap

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "extractor_id": self.extractor_id,
            "method": self.method.value,
            "page_num": self.page_num,
            "confidence": self.confidence,
            "raw_text": self.raw_text[:100] if self.raw_text else None,
        }


@dataclass
class EvidenceItem:
    """
    Item extraído com proveniência completa.

    REGRA: source_spans não pode estar vazio.
    """
    item_id: str
    item_type: ItemType
    value_normalized: str  # Valor normalizado (ex: data ISO, valor numérico)
    source_spans: list[SourceSpan]  # OBRIGATÓRIO - pelo menos 1
    raw_text: Optional[str] = None  # Texto original como aparece no doc
    context: Optional[str] = None   # Contexto circundante
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.item_id:
            self.item_id = f"item_{uuid.uuid4().hex[:8]}"

        # VALIDAÇÃO CRÍTICA: source_spans obrigatório
        if not self.source_spans:
            raise ValueError(
                f"EvidenceItem '{self.item_id}' criado sem source_spans! "
                f"Tipo: {self.item_type}, Valor: {self.value_normalized}"
            )

    @property
    def extractor_ids(self) -> set[str]:
        """Retorna set de extractors que encontraram este item."""
        return {span.extractor_id for span in self.source_spans}

    @property
    def primary_span(self) -> SourceSpan:
        """Retorna o span principal (primeiro)."""
        return self.source_spans[0]

    def add_source(self, span: SourceSpan):
        """Adiciona mais uma fonte a este item."""
        self.source_spans.append(span)

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type.value,
            "value_normalized": self.value_normalized,
            "raw_text": self.raw_text,
            "context": self.context[:200] if self.context else None,
            "source_spans": [s.to_dict() for s in self.source_spans],
            "extractor_ids": list(self.extractor_ids),
            "metadata": self.metadata,
        }


@dataclass
class ExtractionRun:
    """Registo de uma execução de extração."""
    run_id: str
    extractor_id: str  # E1, E2, E3, E4, E5
    model_name: str
    method: ExtractionMethod
    status: ExtractionStatus
    chunks_processed: int = 0
    chunks_failed: int = 0
    items_extracted: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "extractor_id": self.extractor_id,
            "model_name": self.model_name,
            "method": self.method.value,
            "status": self.status.value,
            "chunks_processed": self.chunks_processed,
            "chunks_failed": self.chunks_failed,
            "items_extracted": self.items_extracted,
            "errors": self.errors,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
        }


@dataclass
class CharRange:
    """Intervalo de caracteres."""
    start: int
    end: int
    extractor_id: Optional[str] = None  # Qual extrator cobriu

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: 'CharRange') -> bool:
        return not (self.end <= other.start or other.end <= self.start)

    def merge(self, other: 'CharRange') -> 'CharRange':
        """Merge dois ranges sobrepostos."""
        return CharRange(
            start=min(self.start, other.start),
            end=max(self.end, other.end),
            extractor_id=self.extractor_id or other.extractor_id
        )

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "extractor_id": self.extractor_id,
        }


@dataclass
class Coverage:
    """
    Auditoria de cobertura do documento.

    REGRA: char_ranges_missing deve estar vazio no final OK.
    """
    total_chars: int
    char_ranges_covered: list[CharRange] = field(default_factory=list)
    char_ranges_missing: list[CharRange] = field(default_factory=list)
    coverage_by_extractor: dict[str, list[CharRange]] = field(default_factory=dict)
    pages_covered: list[int] = field(default_factory=list)
    pages_unreadable: list[dict] = field(default_factory=list)  # [{page_num, reason}]
    coverage_percent: float = 0.0
    is_complete: bool = False

    def calculate_coverage(self):
        """Calcula percentagem de cobertura e verifica completude."""
        if not self.char_ranges_covered:
            self.coverage_percent = 0.0
            self.is_complete = False
            return

        # Merge ranges sobrepostos
        merged = self._merge_ranges(self.char_ranges_covered)

        # Calcular chars cobertos
        covered_chars = sum(r.length for r in merged)
        self.coverage_percent = (covered_chars / self.total_chars) * 100 if self.total_chars > 0 else 0

        # Encontrar gaps
        self.char_ranges_missing = self._find_gaps(merged, self.total_chars)

        # Verificar completude (permitir micro-gaps < 100 chars)
        significant_gaps = [g for g in self.char_ranges_missing if g.length >= 100]
        self.is_complete = len(significant_gaps) == 0

    def _merge_ranges(self, ranges: list[CharRange]) -> list[CharRange]:
        """Merge ranges sobrepostos."""
        if not ranges:
            return []

        sorted_ranges = sorted(ranges, key=lambda r: r.start)
        merged = [sorted_ranges[0]]

        for current in sorted_ranges[1:]:
            last = merged[-1]
            if current.start <= last.end:
                merged[-1] = last.merge(current)
            else:
                merged.append(current)

        return merged

    def _find_gaps(self, merged_ranges: list[CharRange], total: int) -> list[CharRange]:
        """Encontra intervalos não cobertos."""
        gaps = []
        prev_end = 0

        for r in merged_ranges:
            if r.start > prev_end:
                gaps.append(CharRange(start=prev_end, end=r.start))
            prev_end = max(prev_end, r.end)

        if prev_end < total:
            gaps.append(CharRange(start=prev_end, end=total))

        return gaps

    def to_dict(self) -> dict:
        return {
            "total_chars": self.total_chars,
            "coverage_percent": round(self.coverage_percent, 2),
            "is_complete": self.is_complete,
            "char_ranges_covered_count": len(self.char_ranges_covered),
            "char_ranges_missing": [r.to_dict() for r in self.char_ranges_missing],
            "coverage_by_extractor": {
                k: [r.to_dict() for r in v]
                for k, v in self.coverage_by_extractor.items()
            },
            "pages_covered": self.pages_covered,
            "pages_unreadable": self.pages_unreadable,
        }


@dataclass
class Conflict:
    """
    Conflito entre extratores para o mesmo span.

    Exemplo: E1 diz "€1.500" e E2 diz "€1.500,00" para o mesmo local.
    """
    conflict_id: str
    item_type: ItemType
    span_key: str  # Identificador do span em conflito
    values: list[dict]  # [{extractor_id, value, confidence}]
    resolution: Optional[str] = None  # Se resolvido, qual valor escolhido
    resolved_by: Optional[str] = None  # "manual" | "auto" | None

    def __post_init__(self):
        if not self.conflict_id:
            self.conflict_id = f"conflict_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id,
            "item_type": self.item_type.value,
            "span_key": self.span_key,
            "values": self.values,
            "resolution": self.resolution,
            "resolved_by": self.resolved_by,
        }


@dataclass
class UnifiedExtractionResult:
    """
    Resultado final da extração unificada.

    Contém TUDO: metadados, chunks, runs, items, coverage, conflicts.
    """
    # Identificação
    result_id: str
    document_meta: DocumentMeta

    # Chunks processados
    chunks: list[Chunk] = field(default_factory=list)

    # Runs de extração (1 por extrator)
    extraction_runs: list[ExtractionRun] = field(default_factory=list)

    # Items extraídos (com proveniência)
    evidence_items: list[EvidenceItem] = field(default_factory=list)

    # Agregação final (união sem dedup)
    union_items: list[EvidenceItem] = field(default_factory=list)

    # Conflitos detectados
    conflicts: list[Conflict] = field(default_factory=list)

    # Cobertura
    coverage: Optional[Coverage] = None

    # Status geral
    status: ExtractionStatus = ExtractionStatus.PENDING
    errors: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if not self.result_id:
            self.result_id = f"result_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def validate(self) -> tuple[bool, list[str]]:
        """
        Valida o resultado completo.

        Returns:
            (is_valid, list_of_errors)
        """
        errors = []

        # 1. Verificar que todos os items têm source_spans
        for item in self.evidence_items:
            if not item.source_spans:
                errors.append(f"Item {item.item_id} sem source_spans")

        for item in self.union_items:
            if not item.source_spans:
                errors.append(f"Union item {item.item_id} sem source_spans")

        # 2. Verificar cobertura
        if self.coverage:
            if not self.coverage.is_complete:
                gaps = self.coverage.char_ranges_missing
                errors.append(f"Cobertura incompleta: {len(gaps)} gaps")

        # 3. Verificar chunks
        if self.chunks:
            # Verificar sequência de offsets
            for i, chunk in enumerate(self.chunks):
                if chunk.chunk_index != i:
                    errors.append(f"Chunk index mismatch: {chunk.chunk_index} != {i}")

        return len(errors) == 0, errors

    def get_items_by_type(self, item_type: ItemType) -> list[EvidenceItem]:
        """Retorna items filtrados por tipo."""
        return [i for i in self.union_items if i.item_type == item_type]

    def get_items_by_span(self, start_char: int, end_char: int) -> list[EvidenceItem]:
        """Retorna items que se sobrepõem com o intervalo dado."""
        results = []
        for item in self.union_items:
            for span in item.source_spans:
                if not (span.end_char <= start_char or span.start_char >= end_char):
                    results.append(item)
                    break
        return results

    def to_dict(self) -> dict:
        return {
            "result_id": self.result_id,
            "document_meta": self.document_meta.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "chunks_count": len(self.chunks),
            "chunks": [c.to_dict() for c in self.chunks],
            "extraction_runs": [r.to_dict() for r in self.extraction_runs],
            "evidence_items_count": len(self.evidence_items),
            "evidence_items": [i.to_dict() for i in self.evidence_items],
            "union_items_count": len(self.union_items),
            "union_items": [i.to_dict() for i in self.union_items],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "coverage": self.coverage.to_dict() if self.coverage else None,
            "errors": self.errors,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serializa para JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def create_chunk_id(doc_id: str, chunk_index: int) -> str:
    """Cria ID determinístico para um chunk."""
    return f"{doc_id}_c{chunk_index:04d}"


def create_item_id(item_type: ItemType, value: str, span: SourceSpan) -> str:
    """Cria ID determinístico para um item baseado no conteúdo."""
    content = f"{item_type.value}:{value}:{span.doc_id}:{span.start_char}"
    return f"item_{hashlib.sha256(content.encode()).hexdigest()[:12]}"


def calculate_chunks_for_document(
    total_chars: int,
    chunk_size: int = 50000,
    overlap: int = 2500
) -> list[tuple[int, int]]:
    """
    Calcula os intervalos de chunks para um documento.

    Args:
        total_chars: Total de caracteres do documento
        chunk_size: Tamanho de cada chunk
        overlap: Sobreposição entre chunks

    Returns:
        Lista de tuplas (start_char, end_char)
    """
    if total_chars <= chunk_size:
        return [(0, total_chars)]

    step = chunk_size - overlap  # 47500 com defaults
    chunks = []
    start = 0

    while start < total_chars:
        end = min(start + chunk_size, total_chars)
        chunks.append((start, end))

        if end >= total_chars:
            break

        start += step

    return chunks


def validate_evidence_item(item: EvidenceItem) -> tuple[bool, Optional[str]]:
    """
    Valida um EvidenceItem.

    Returns:
        (is_valid, error_message)
    """
    if not item.source_spans:
        return False, f"Item '{item.item_id}' não tem source_spans"

    for span in item.source_spans:
        if span.start_char < 0:
            return False, f"Span com start_char negativo: {span.start_char}"
        if span.end_char < span.start_char:
            return False, f"Span inválido: end ({span.end_char}) < start ({span.start_char})"

    return True, None


def merge_evidence_items_preserve_provenance(
    items_by_extractor: dict[str, list[EvidenceItem]]
) -> tuple[list[EvidenceItem], list[Conflict]]:
    """
    Combina items de múltiplos extratores preservando proveniência.

    SEM DEDUPLICAÇÃO - mantém tudo, mas detecta conflitos.

    Args:
        items_by_extractor: {extractor_id: [items]}

    Returns:
        (union_items, conflicts)
    """
    union_items = []
    conflicts = []

    # Índice para detecção de conflitos: span_key -> [(extractor_id, value, item)]
    span_index: dict[str, list[tuple[str, str, EvidenceItem]]] = {}

    for extractor_id, items in items_by_extractor.items():
        for item in items:
            # Adicionar à união (sem dedup)
            union_items.append(item)

            # Indexar por span para detectar conflitos
            for span in item.source_spans:
                key = span.span_key
                if key not in span_index:
                    span_index[key] = []
                span_index[key].append((extractor_id, item.value_normalized, item))

    # Detectar conflitos (mesmo span, valores diferentes)
    for span_key, entries in span_index.items():
        if len(entries) > 1:
            # Verificar se há valores diferentes
            values = set(e[1] for e in entries)
            if len(values) > 1:
                conflict = Conflict(
                    conflict_id="",
                    item_type=entries[0][2].item_type,
                    span_key=span_key,
                    values=[
                        {"extractor_id": e[0], "value": e[1], "confidence": 1.0}
                        for e in entries
                    ]
                )
                conflicts.append(conflict)

    return union_items, conflicts


# ============================================================================
# EXEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    # Exemplo de criação de estruturas

    # 1. Documento
    doc = DocumentMeta(
        doc_id="doc_test123",
        filename="contrato.txt",
        file_type=".txt",
        total_chars=150000,
    )

    # 2. Chunks
    chunk_intervals = calculate_chunks_for_document(doc.total_chars)
    print(f"Documento de {doc.total_chars:,} chars -> {len(chunk_intervals)} chunks")
    for i, (start, end) in enumerate(chunk_intervals):
        print(f"  Chunk {i}: [{start:,} - {end:,}) = {end-start:,} chars")

    # 3. Source span
    span = SourceSpan(
        doc_id=doc.doc_id,
        chunk_id="doc_test123_c0000",
        start_char=1500,
        end_char=1520,
        extractor_id="E1",
        raw_text="15 de Janeiro de 2024"
    )

    # 4. Evidence item
    item = EvidenceItem(
        item_id="",
        item_type=ItemType.DATE,
        value_normalized="2024-01-15",
        source_spans=[span],
        raw_text="15 de Janeiro de 2024"
    )

    print(f"\nItem criado: {item.item_id}")
    print(f"  Tipo: {item.item_type.value}")
    print(f"  Valor: {item.value_normalized}")
    print(f"  Span: chars {span.start_char}-{span.end_char}")
    print(f"  Extrator: {span.extractor_id}")

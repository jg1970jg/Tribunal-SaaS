# ============================================================================
# Pipeline v4.2 — M7B: Consolidação Hierárquica
# ============================================================================
# Consolida resultados da análise por chunk (M7) em union_items
# compatíveis com o formato Phase 2 (auditores).
#
# Estratégia:
# - ≤15 chunks: consolidação directa
# - >15 chunks: lotes de 10 → meta-consolidação
#
# Output: lista de EvidenceItem com SourceSpan para cada item.
# ============================================================================

import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from src.config import V42_HIERARCHICAL_THRESHOLD, V42_CONSOLIDATION_BATCH_SIZE
from src.pipeline.schema_unified import (
    EvidenceItem,
    SourceSpan,
    ExtractionMethod,
    ItemType,
    Conflict,
)

logger = logging.getLogger(__name__)

# Mapeamento de tipos do M7 para ItemType do schema_unified
ITEM_TYPE_MAP = {
    "fact": ItemType.FACT,
    "date": ItemType.DATE,
    "amount": ItemType.AMOUNT,
    "legal_ref": ItemType.LEGAL_REF,
    "entity": ItemType.ENTITY,
    "table": ItemType.TABLE,
    "visual": ItemType.VISUAL,
    "other": ItemType.OTHER,
    # Categorias adicionais do M7 → mapeamento
    "facto_relevante": ItemType.FACT,
    "data_chave": ItemType.DATE,
    "valor_monetario": ItemType.AMOUNT,
    "referencia_legal": ItemType.LEGAL_REF,
    "parte_processual": ItemType.ENTITY,
}


@dataclass
class ConsolidationResult:
    """Resultado da consolidação M7B."""
    union_items: list[EvidenceItem]
    conflicts: list[Conflict]
    total_items: int
    total_chunks_processed: int
    hierarchical: bool = False


def consolidate(
    chunk_analyses: list,       # list[ChunkAnalysis] from m7_legal_analysis
    chunks: list,               # list[SemanticChunk] from m6_chunking
    doc_id: str,
    entity_registry=None,       # EntityRegistry from m5_entity_lock
) -> ConsolidationResult:
    """
    M7B: Consolidação hierárquica.

    Converte análises por chunk em union_items compatíveis com Phase 2.

    Args:
        chunk_analyses: resultados do M7
        chunks: chunks semânticos do M6 (para posições)
        doc_id: ID do documento
        entity_registry: registo de entidades travadas (M5)

    Returns:
        ConsolidationResult com union_items e conflicts
    """
    total_chunks = len(chunk_analyses)
    logger.info(f"[M7B] Consolidação: {total_chunks} chunks")

    if total_chunks <= V42_HIERARCHICAL_THRESHOLD:
        # Consolidação directa
        result = _direct_consolidation(chunk_analyses, chunks, doc_id, entity_registry)
    else:
        # Consolidação hierárquica
        logger.info(
            f"[M7B] Modo hierárquico: {total_chunks} chunks > threshold {V42_HIERARCHICAL_THRESHOLD}"
        )
        result = _hierarchical_consolidation(chunk_analyses, chunks, doc_id, entity_registry)

    # Adicionar entidades travadas como items (se não já incluídas)
    if entity_registry:
        _add_locked_entities(result, entity_registry, doc_id, chunks)

    logger.info(
        f"[M7B] Consolidação concluída: {len(result.union_items)} union_items, "
        f"{len(result.conflicts)} conflicts"
    )

    return result


def _direct_consolidation(
    chunk_analyses: list,
    chunks: list,
    doc_id: str,
    entity_registry,
) -> ConsolidationResult:
    """Consolidação directa para documentos com poucos chunks."""
    union_items = []
    conflicts = []

    for analysis in chunk_analyses:
        if analysis.error:
            continue

        # Encontrar o chunk correspondente
        chunk = _find_chunk(chunks, analysis.chunk_index)
        if not chunk:
            continue

        for item in analysis.items:
            evidence_item = _create_evidence_item(
                item, analysis, chunk, doc_id
            )
            if evidence_item:
                union_items.append(evidence_item)

    # Detectar conflitos (items com mesmo tipo e posição mas valores diferentes)
    conflicts = _detect_conflicts(union_items)

    return ConsolidationResult(
        union_items=union_items,
        conflicts=conflicts,
        total_items=len(union_items),
        total_chunks_processed=len(chunk_analyses),
        hierarchical=False,
    )


def _hierarchical_consolidation(
    chunk_analyses: list,
    chunks: list,
    doc_id: str,
    entity_registry,
) -> ConsolidationResult:
    """Consolidação hierárquica para documentos grandes."""
    batch_size = V42_CONSOLIDATION_BATCH_SIZE
    all_items = []
    all_conflicts = []

    # Processar em lotes
    for batch_start in range(0, len(chunk_analyses), batch_size):
        batch_end = min(batch_start + batch_size, len(chunk_analyses))
        batch_analyses = chunk_analyses[batch_start:batch_end]

        logger.info(f"[M7B] Lote {batch_start // batch_size + 1}: chunks {batch_start}-{batch_end}")

        result = _direct_consolidation(batch_analyses, chunks, doc_id, entity_registry)
        all_items.extend(result.union_items)
        all_conflicts.extend(result.conflicts)

    # Meta-consolidação: detectar duplicados entre lotes
    deduplicated_items, extra_conflicts = _cross_batch_dedup(all_items)
    all_conflicts.extend(extra_conflicts)

    return ConsolidationResult(
        union_items=deduplicated_items,
        conflicts=all_conflicts,
        total_items=len(deduplicated_items),
        total_chunks_processed=len(chunk_analyses),
        hierarchical=True,
    )


def _create_evidence_item(
    item,  # AnalyzedItem from m7_legal_analysis
    analysis,  # ChunkAnalysis
    chunk,  # SemanticChunk
    doc_id: str,
) -> Optional[EvidenceItem]:
    """Converter AnalyzedItem + chunk info em EvidenceItem."""
    # Mapear tipo
    item_type = ITEM_TYPE_MAP.get(item.item_type, ItemType.OTHER)
    if item.category in ITEM_TYPE_MAP:
        item_type = ITEM_TYPE_MAP[item.category]

    # Determinar posição no documento
    # Usar posição do chunk como aproximação
    start_char = chunk.start_char
    end_char = chunk.end_char

    # Se temos raw_text, tentar encontrar posição exacta
    if item.raw_text and item.raw_text in chunk.text:
        offset = chunk.text.find(item.raw_text)
        if offset >= 0:
            start_char = chunk.start_char + offset
            end_char = start_char + len(item.raw_text)

    # Determinar página
    page_num = chunk.page_start

    # Criar SourceSpan
    source_span = SourceSpan(
        doc_id=doc_id,
        chunk_id=f"{doc_id}_c{chunk.chunk_index:04d}",
        start_char=start_char,
        end_char=end_char,
        extractor_id=f"M7_chunk_{chunk.chunk_index}",
        method=ExtractionMethod.OCR,
        page_num=page_num,
        confidence=item.confidence,
        raw_text=item.raw_text[:100] if item.raw_text else None,
    )

    item_id = f"item_{uuid.uuid4().hex[:8]}"

    try:
        return EvidenceItem(
            item_id=item_id,
            item_type=item_type,
            value_normalized=item.value,
            source_spans=[source_span],
            raw_text=item.raw_text,
            context=item.context[:200] if item.context else None,
            metadata={
                "category": item.category,
                "model": analysis.model_used,
                "chunk_index": analysis.chunk_index,
            },
        )
    except ValueError as e:
        logger.warning(f"[M7B] Erro ao criar EvidenceItem: {e}")
        return None


def _add_locked_entities(
    result: ConsolidationResult,
    entity_registry,
    doc_id: str,
    chunks: list,
) -> None:
    """Adicionar entidades travadas (M5) como union_items se não duplicados."""
    existing_values = {
        (item.item_type.value, item.value_normalized)
        for item in result.union_items
    }

    entity_type_map = {
        "date": ItemType.DATE,
        "amount": ItemType.AMOUNT,
        "legal_ref": ItemType.LEGAL_REF,
        "process_number": ItemType.ENTITY,
        "person": ItemType.ENTITY,
        "org": ItemType.ENTITY,
        "location": ItemType.ENTITY,
        "nif": ItemType.ENTITY,
    }

    added = 0
    for entity in entity_registry.entities:
        item_type = entity_type_map.get(entity.entity_type, ItemType.OTHER)

        # Verificar se já existe
        key = (item_type.value, entity.normalized)
        if key in existing_values:
            continue

        # Encontrar chunk_id
        chunk_id = f"{doc_id}_c0000"
        for chunk in chunks:
            if chunk.start_char <= entity.start_char < chunk.end_char:
                chunk_id = f"{doc_id}_c{chunk.chunk_index:04d}"
                break

        try:
            evidence_item = EvidenceItem(
                item_id=f"item_ent_{uuid.uuid4().hex[:8]}",
                item_type=item_type,
                value_normalized=entity.normalized,
                source_spans=[SourceSpan(
                    doc_id=doc_id,
                    chunk_id=chunk_id,
                    start_char=entity.start_char,
                    end_char=entity.end_char,
                    extractor_id=f"M5_{entity.source}",
                    method=ExtractionMethod.OCR,
                    page_num=entity.page_num,
                    confidence=entity.confidence,
                    raw_text=entity.text,
                )],
                raw_text=entity.text,
                metadata={
                    "entity_type": entity.entity_type,
                    "source": entity.source,
                    "locked": True,
                },
            )
            result.union_items.append(evidence_item)
            existing_values.add(key)
            added += 1
        except ValueError:
            continue

    if added:
        logger.info(f"[M7B] Adicionadas {added} entidades travadas aos union_items")


def _find_chunk(chunks: list, chunk_index: int):
    """Encontrar chunk por índice."""
    for chunk in chunks:
        if chunk.chunk_index == chunk_index:
            return chunk
    return None


def _detect_conflicts(items: list[EvidenceItem]) -> list:
    """Detectar conflitos entre items (mesmo tipo, posição, valores diferentes)."""
    conflicts = []
    # Agrupar por tipo e posição
    by_position = {}
    for item in items:
        for span in item.source_spans:
            key = (item.item_type.value, span.page_num)
            by_position.setdefault(key, []).append(item)

    for key, group in by_position.items():
        if len(group) <= 1:
            continue
        # Verificar se há valores diferentes
        values = set(item.value_normalized for item in group)
        if len(values) > 1:
            try:
                conflicts.append(Conflict(
                    conflict_id=f"conflict_{uuid.uuid4().hex[:8]}",
                    item_type=key[0],
                    items_involved=[item.item_id for item in group],
                    description=f"Valores diferentes para {key[0]} na página {key[1]}: {values}",
                ))
            except Exception:
                pass

    return conflicts


def _cross_batch_dedup(items: list[EvidenceItem]) -> tuple[list[EvidenceItem], list]:
    """Remover duplicados entre lotes na consolidação hierárquica."""
    seen = {}  # (type, value) -> first item
    unique_items = []
    conflicts = []

    for item in items:
        key = (item.item_type.value, item.value_normalized)
        if key not in seen:
            seen[key] = item
            unique_items.append(item)
        else:
            # Duplicado: merge source_spans
            existing = seen[key]
            for span in item.source_spans:
                existing.add_source(span)

    removed = len(items) - len(unique_items)
    if removed:
        logger.info(f"[M7B] Cross-batch dedup: {removed} duplicados removidos")

    return unique_items, conflicts

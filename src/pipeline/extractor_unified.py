# -*- coding: utf-8 -*-
"""
Extrator Unificado com Proveniência para Modo Texto/Chunks.

Extrai informação estruturada com source_spans obrigatórios.
Cada facto/data/valor/ref legal tem localização precisa no documento.

REGRAS:
1. Todos os items têm source_spans (obrigatório)
2. Offsets são relativos ao chunk, convertidos para absolutos
3. Sem perda de informação - tudo é preservado
"""

import json
import logging
import re
import hashlib
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass

from src.config import LOG_LEVEL
from src.pipeline.schema_unified import (
    Chunk,
    SourceSpan,
    EvidenceItem,
    ItemType,
    ExtractionMethod,
    ExtractionRun,
    ExtractionStatus,
    create_item_id,
)

logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger(__name__)


# ============================================================================
# PROMPT PARA EXTRAÇÃO ESTRUTURADA COM OFFSETS
# ============================================================================

SYSTEM_EXTRATOR_UNIFIED = """És um extrator de informação jurídica especializado em Direito Português.
RECEBES um chunk de texto com metadados (chunk_id, start_char, end_char).
DEVES devolver um JSON ESTRITO com items extraídos e suas localizações EXATAS.

REGRAS CRÍTICAS:
1. Para CADA item extraído, tens de indicar offset_start e offset_end RELATIVOS ao início do chunk
2. Os offsets são posições de caracteres no texto do chunk (começando em 0)
3. Se não conseguires determinar o offset exato, usa offset aproximado e marca confidence < 1.0
4. NUNCA inventes informação que não está no texto
5. Se o texto estiver ilegível/ruído, marca unreadable: true

FORMATO DE OUTPUT OBRIGATÓRIO (JSON):
{
  "chunk_id": "doc_xxx_c0001",
  "items": [
    {
      "item_type": "fact|date|amount|legal_ref|visual|entity|other",
      "value_normalized": "valor normalizado (ex: 2024-01-15 para datas)",
      "raw_text": "texto exato como aparece no documento",
      "offset_start": 150,
      "offset_end": 175,
      "confidence": 0.95,
      "context": "frase ou parágrafo circundante (opcional)"
    }
  ],
  "unreadable_sections": [
    {"offset_start": 500, "offset_end": 600, "reason": "OCR ilegível"}
  ],
  "chunk_summary": "resumo do chunk em 1-2 frases"
}

TIPOS DE ITEMS:
- fact: factos relevantes (ex: "contrato assinado", "partes acordaram X")
- date: datas (normalizar para YYYY-MM-DD)
- amount: valores monetários (normalizar para €X.XXX,XX)
- legal_ref: referências legais (Art. Xº do CC, DL n.º X/AAAA)
- visual: elementos visuais mencionados (assinatura, carimbo, tabela)
- entity: entidades (nomes, empresas, moradas)
- other: outros dados relevantes

EXEMPLO DE OFFSET:
Se o texto é "O contrato foi assinado em 15/01/2024 pelas partes."
E "15/01/2024" começa na posição 27 e termina na 37:
{
  "item_type": "date",
  "value_normalized": "2024-01-15",
  "raw_text": "15/01/2024",
  "offset_start": 27,
  "offset_end": 37,
  "confidence": 1.0
}
"""


def build_unified_prompt(chunk: Chunk, area_direito: str, extractor_id: str) -> str:
    """
    Constrói prompt para extração unificada com metadados do chunk.

    Args:
        chunk: Chunk object com texto e offsets
        area_direito: Área do direito (Civil, Penal, etc.)
        extractor_id: ID do extrator (E1, E2, etc.)

    Returns:
        Prompt formatado
    """
    return f"""CHUNK A ANALISAR:
- chunk_id: {chunk.chunk_id}
- doc_id: {chunk.doc_id}
- posição no documento: caracteres [{chunk.start_char:,} - {chunk.end_char:,})
- chunk_index: {chunk.chunk_index + 1} de {chunk.total_chunks}
- overlap com chunk anterior: {chunk.overlap} chars
- área do direito: {area_direito}
- extrator: {extractor_id}

TEXTO DO CHUNK ({len(chunk.text):,} caracteres):
---
{chunk.text}
---

Extrai TODOS os items relevantes com offsets EXATOS relativos ao início deste texto (posição 0).
Retorna JSON no formato especificado."""


def parse_unified_output(
    output: str,
    chunk: Chunk,
    extractor_id: str,
    model_name: str,
    page_mapper: Optional[Any] = None
) -> Tuple[List[EvidenceItem], List[Dict], List[str]]:
    """
    Parseia output do LLM e cria EvidenceItems com source_spans.

    Args:
        output: Output JSON do LLM
        chunk: Chunk processado
        extractor_id: ID do extrator
        model_name: Nome do modelo usado
        page_mapper: CharToPageMapper opcional para preencher page_num

    Returns:
        (items, unreadable_sections, errors)
    """
    items = []
    unreadable = []
    errors = []

    # Tentar extrair JSON (robusto: markdown, texto antes/depois, etc.)
    from src.pipeline.extractor_json import extract_json_from_text
    json_data = extract_json_from_text(output)

    if not json_data:
        # v4.0: Tentar auto-repair antes de desistir
        repaired = auto_repair_json(output)
        if repaired:
            json_data = extract_json_from_text(repaired)
            if json_data:
                logger.info(f"[JSON-REPAIR] {extractor_id}: JSON reparado com sucesso")

    if not json_data:
        errors.append(f"Não foi possível extrair JSON do output do {extractor_id}")
        # Tentar fallback para extração por regex
        items = _fallback_extract_with_offsets(chunk, extractor_id, page_mapper)
        return items, unreadable, errors

    # v4.0 Fix: Se json_data é uma lista (E2/E7 devolvem array directamente),
    # converter para formato esperado {items: [...]}
    if isinstance(json_data, list):
        # Filtrar items de controlo (ex: {"status": "to_be_continued"})
        real_items = [x for x in json_data if isinstance(x, dict) and "status" not in x]
        json_data = {"items": real_items}
        logger.info(f"[JSON-ARRAY] {extractor_id}: convertido array ({len(real_items)} items) para formato dict")

    # Processar items
    raw_items = json_data.get("items", [])
    for raw_item in raw_items:
        try:
            item = _create_evidence_item(raw_item, chunk, extractor_id, page_mapper)
            if item:
                items.append(item)
        except Exception as e:
            errors.append(f"Erro ao criar item: {e}")

    # Processar secções ilegíveis
    raw_unreadable = json_data.get("unreadable_sections", [])
    for section in raw_unreadable:
        unreadable.append({
            "chunk_id": chunk.chunk_id,
            "offset_start": section.get("offset_start", 0) + chunk.start_char,
            "offset_end": section.get("offset_end", 0) + chunk.start_char,
            "reason": section.get("reason", "desconhecido"),
        })

    logger.info(
        f"{extractor_id} chunk {chunk.chunk_index}: "
        f"{len(items)} items, {len(unreadable)} ilegíveis, {len(errors)} erros"
    )

    return items, unreadable, errors


def _create_evidence_item(
    raw_item: Dict,
    chunk: Chunk,
    extractor_id: str,
    page_mapper: Optional[Any] = None
) -> Optional[EvidenceItem]:
    """
    Cria EvidenceItem a partir de item raw do LLM.

    Converte offsets relativos ao chunk para offsets absolutos.
    Se page_mapper fornecido, preenche page_num automaticamente.

    Args:
        raw_item: Item raw do output LLM
        chunk: Chunk sendo processado
        extractor_id: ID do extrator (E1-E5)
        page_mapper: CharToPageMapper opcional para mapeamento de páginas
    """
    item_type_str = raw_item.get("item_type", "other")
    try:
        item_type = ItemType(item_type_str)
    except ValueError:
        item_type = ItemType.OTHER

    value = raw_item.get("value_normalized", "")
    raw_text = raw_item.get("raw_text", value)

    if not value and not raw_text:
        return None

    # Offsets relativos ao chunk
    rel_start = raw_item.get("offset_start", 0)
    rel_end = raw_item.get("offset_end", rel_start + len(raw_text))

    # Validar offsets
    if rel_start < 0:
        rel_start = 0
    if rel_end > len(chunk.text):
        rel_end = len(chunk.text)
    if rel_end < rel_start:
        rel_end = rel_start + len(raw_text)

    # Converter para offsets absolutos
    abs_start = chunk.start_char + rel_start
    abs_end = chunk.start_char + rel_end

    # Determinar page_num se mapper disponível
    page_num = None
    if page_mapper is not None:
        page_num = page_mapper.get_page(abs_start)

    # Criar source span
    span = SourceSpan(
        doc_id=chunk.doc_id,
        chunk_id=chunk.chunk_id,
        start_char=abs_start,
        end_char=abs_end,
        extractor_id=extractor_id,
        method=chunk.method,
        page_num=page_num,
        confidence=raw_item.get("confidence", 1.0),
        raw_text=raw_text[:200] if raw_text else None,
    )

    # Criar evidence item
    item = EvidenceItem(
        item_id=create_item_id(item_type, value, span),
        item_type=item_type,
        value_normalized=value,
        source_spans=[span],
        raw_text=raw_text,
        context=raw_item.get("context"),
    )

    return item


def _fallback_extract_with_offsets(
    chunk: Chunk,
    extractor_id: str,
    page_mapper: Optional[Any] = None
) -> List[EvidenceItem]:
    """
    Extração por regex quando LLM falha.
    Extrai datas, valores e referências legais com offsets.

    Args:
        chunk: Chunk sendo processado
        extractor_id: ID do extrator
        page_mapper: CharToPageMapper opcional para preencher page_num
    """
    items = []
    text = chunk.text

    # Padrões de extração
    patterns = {
        ItemType.DATE: [
            r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b',
            r'\b(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})\b',
        ],
        ItemType.AMOUNT: [
            r'€\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)',
            r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*(?:euros?|EUR)',
        ],
        ItemType.LEGAL_REF: [
            r'\b(art(?:igo)?\.?\s*\d+[°ºª]?(?:\s*n\.?º?\s*\d+)?(?:\s*al[ií]nea\s*[a-z]\))?)\b',
            r'\b(DL\s*n\.?º?\s*\d+[/-]\d+)\b',
            r'\b(Lei\s*n\.?º?\s*\d+[/-]\d+)\b',
        ],
    }

    for item_type, type_patterns in patterns.items():
        for pattern in type_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                raw_text = match.group(0)
                rel_start = match.start()
                rel_end = match.end()

                # Converter para absoluto
                abs_start = chunk.start_char + rel_start
                abs_end = chunk.start_char + rel_end

                # Determinar page_num se mapper disponível
                page_num = None
                if page_mapper is not None:
                    page_num = page_mapper.get_page(abs_start)

                span = SourceSpan(
                    doc_id=chunk.doc_id,
                    chunk_id=chunk.chunk_id,
                    start_char=abs_start,
                    end_char=abs_end,
                    extractor_id=extractor_id,
                    method=chunk.method,
                    page_num=page_num,
                    confidence=0.8,  # Menor confiança por ser regex
                    raw_text=raw_text,
                )

                # Normalizar valor
                value = _normalize_value(item_type, raw_text)

                item = EvidenceItem(
                    item_id=create_item_id(item_type, value, span),
                    item_type=item_type,
                    value_normalized=value,
                    source_spans=[span],
                    raw_text=raw_text,
                )
                items.append(item)

    logger.info(f"Fallback regex {extractor_id}: {len(items)} items extraídos")
    return items


def _normalize_value(item_type: ItemType, raw_text: str) -> str:
    """Normaliza valor baseado no tipo."""
    if item_type == ItemType.DATE:
        # Tentar converter para ISO
        # Simplificado - em produção usar dateparser
        return raw_text.strip()

    elif item_type == ItemType.AMOUNT:
        # Remover formatação, manter número
        cleaned = re.sub(r'[€\s]', '', raw_text)
        return f"€{cleaned}"

    elif item_type == ItemType.LEGAL_REF:
        return raw_text.strip()

    return raw_text.strip()


# ============================================================================
# v4.0 HANDOVER — DEDUPLICAÇÃO SEMÂNTICA + DESCARTE INTELIGENTE
# ============================================================================

def normalize_and_hash(text: str) -> str:
    """
    Normaliza texto para comparação semântica.
    Remove acentos, pontuação, espaços extras e converte para minúsculas.
    Retorna hash MD5 do texto normalizado.
    """
    import unicodedata
    import re as _re
    # Remover acentos
    nfkd = unicodedata.normalize('NFKD', text)
    sem_acentos = ''.join(c for c in nfkd if not unicodedata.combining(c))
    # Minúsculas, sem pontuação, sem espaços extras
    normalizado = _re.sub(r'[^\w\s]', '', sem_acentos.lower())
    normalizado = _re.sub(r'\s+', ' ', normalizado).strip()
    return hashlib.md5(normalizado.encode()).hexdigest()


def validate_and_filter_extractors(
    items_by_extractor: Dict[str, List[EvidenceItem]],
    min_ratio: float = 0.20,
) -> Dict[str, List[EvidenceItem]]:
    """
    v4.0 Handover — Descarte inteligente de extractores fracos.

    Se um extrator tem <min_ratio da média de items:
    - Verifica se tem factos exclusivos (que nenhum outro extrator encontrou)
    - Factos exclusivos são mantidos com verificacao_obrigatoria=True
    - Factos não-exclusivos do extrator fraco são descartados

    Args:
        items_by_extractor: {extractor_id: [items]}
        min_ratio: Rácio mínimo vs média (default 20%)

    Returns:
        Dict filtrado com extratores válidos (ou parcialmente válidos)
    """
    if not items_by_extractor:
        return items_by_extractor

    counts = {eid: len(items) for eid, items in items_by_extractor.items()}
    if not counts:
        return items_by_extractor

    avg_count = sum(counts.values()) / len(counts)
    threshold = avg_count * min_ratio

    logger.info(f"[SMART-DISCARD] Média items/extrator: {avg_count:.0f}, threshold 20%: {threshold:.0f}")

    # Construir hash index de todos os items de todos os extractores
    # hash -> set of extractor_ids that found this item
    all_hashes: Dict[str, set] = {}
    item_hash_map: Dict[str, Dict[str, str]] = {}  # extractor_id -> {item_hash: item}

    for eid, items in items_by_extractor.items():
        item_hash_map[eid] = {}
        for item in items:
            h = normalize_and_hash(item.value_normalized or item.raw_text or "")
            if h not in all_hashes:
                all_hashes[h] = set()
            all_hashes[h].add(eid)
            item_hash_map[eid][h] = item

    filtered = {}
    for eid, items in items_by_extractor.items():
        if counts[eid] >= threshold:
            # Extrator OK — manter tudo
            filtered[eid] = items
            logger.info(f"[SMART-DISCARD] {eid}: {counts[eid]} items — MANTIDO (>= threshold)")
        else:
            # Extrator fraco — verificar factos exclusivos
            exclusive_items = []
            for item in items:
                h = normalize_and_hash(item.value_normalized or item.raw_text or "")
                sources = all_hashes.get(h, set())
                if len(sources) <= 1:
                    # Facto exclusivo — manter com flag
                    item.context = (item.context or "") + " [FONTE_UNICA — VERIFICACAO_OBRIGATORIA]"
                    exclusive_items.append(item)

            if exclusive_items:
                filtered[eid] = exclusive_items
                logger.warning(
                    f"[SMART-DISCARD] {eid}: {counts[eid]} items (< threshold {threshold:.0f}) — "
                    f"MANTIDO PARCIAL: {len(exclusive_items)} factos exclusivos preservados"
                )
            else:
                logger.warning(
                    f"[SMART-DISCARD] {eid}: {counts[eid]} items (< threshold {threshold:.0f}) — "
                    f"DESCARTADO: 0 factos exclusivos"
                )

    return filtered


# ============================================================================
# AGREGADOR COM PROVENIÊNCIA + DEDUPLICAÇÃO SEMÂNTICA
# ============================================================================

def aggregate_with_provenance(
    items_by_extractor: Dict[str, List[EvidenceItem]],
    detect_conflicts: bool = True,
    deduplicate: bool = True,
) -> Tuple[List[EvidenceItem], List[Dict]]:
    """
    Agrega items de múltiplos extratores preservando proveniência.

    v4.0: Com deduplicação semântica — items idênticos (mesma info, palavras diferentes)
    são fundidos num único item com múltiplas fontes. Conflitos são detectados e marcados.

    Args:
        items_by_extractor: {extractor_id: [items]}
        detect_conflicts: Se True, detecta valores divergentes
        deduplicate: Se True, aplica deduplicação semântica (v4.0)

    Returns:
        (union_items, conflicts)
    """
    if not deduplicate:
        # Modo legacy — sem deduplicação
        return _aggregate_legacy(items_by_extractor, detect_conflicts)

    # v4.0: Deduplicação semântica
    # Agrupar items por hash normalizado
    hash_groups: Dict[str, List[Tuple[str, EvidenceItem]]] = {}  # hash -> [(extractor_id, item)]
    conflicts = []

    for extractor_id, items in items_by_extractor.items():
        for item in items:
            h = normalize_and_hash(item.value_normalized or item.raw_text or "")
            if h not in hash_groups:
                hash_groups[h] = []
            hash_groups[h].append((extractor_id, item))

    # Construir items deduplicados
    union_items = []
    for h, entries in hash_groups.items():
        if len(entries) == 1:
            # Item único — manter como está
            eid, item = entries[0]
            union_items.append(item)
        else:
            # Múltiplos extractores encontraram o mesmo item
            # Verificar se os valores são realmente iguais ou divergentes
            values = set()
            for eid, item in entries:
                values.add((item.value_normalized or "").strip().lower())

            if len(values) <= 1:
                # Consenso — fundir source_spans
                base_eid, base_item = entries[0]
                for eid, item in entries[1:]:
                    base_item.source_spans.extend(item.source_spans)
                # Adicionar info de consenso no contexto
                source_ids = sorted(set(e[0] for e in entries))
                consensus_tag = f"[{','.join(source_ids)}] consenso:{len(entries)}"
                base_item.context = consensus_tag + (" " + base_item.context if base_item.context else "")
                union_items.append(base_item)
            else:
                # Divergência — manter ambos e registar conflito
                for eid, item in entries:
                    union_items.append(item)
                conflict = {
                    "conflict_id": f"conflict_{h[:8]}",
                    "item_type": entries[0][1].item_type.value,
                    "hash": h,
                    "values": [
                        {"extractor_id": e[0], "value": e[1].value_normalized}
                        for e in entries
                    ],
                }
                conflicts.append(conflict)

    # Detectar conflitos adicionais por span proximity
    if detect_conflicts:
        span_index: Dict[str, List[Tuple[str, str, EvidenceItem]]] = {}
        for item in union_items:
            for span in item.source_spans:
                bucket = span.start_char // 100
                key = f"{item.item_type.value}:{span.doc_id}:{bucket}"
                if key not in span_index:
                    span_index[key] = []
                span_index[key].append((span.extractor_id, item.value_normalized, item))

        for key, span_entries in span_index.items():
            if len(span_entries) > 1:
                span_values = set(e[1] for e in span_entries)
                if len(span_values) > 1:
                    # Check if already captured
                    existing_ids = {c["conflict_id"] for c in conflicts}
                    cid = f"conflict_{hashlib.md5(key.encode()).hexdigest()[:8]}"
                    if cid not in existing_ids:
                        conflicts.append({
                            "conflict_id": cid,
                            "item_type": span_entries[0][2].item_type.value,
                            "span_key": key,
                            "values": [
                                {"extractor_id": e[0], "value": e[1]}
                                for e in span_entries
                            ],
                        })

    logger.info(
        f"Agregação v4.0: {len(union_items)} items deduplicados "
        f"(de {sum(len(v) for v in items_by_extractor.values())} brutos), "
        f"{len(conflicts)} conflitos"
    )

    return union_items, conflicts


def _aggregate_legacy(
    items_by_extractor: Dict[str, List[EvidenceItem]],
    detect_conflicts: bool = True,
) -> Tuple[List[EvidenceItem], List[Dict]]:
    """Agregação legacy sem deduplicação (backward compatibility)."""
    union_items = []
    conflicts = []
    span_index: Dict[str, List[Tuple[str, str, EvidenceItem]]] = {}

    for extractor_id, items in items_by_extractor.items():
        for item in items:
            union_items.append(item)
            if detect_conflicts:
                for span in item.source_spans:
                    bucket = span.start_char // 100
                    key = f"{item.item_type.value}:{span.doc_id}:{bucket}"
                    if key not in span_index:
                        span_index[key] = []
                    span_index[key].append((extractor_id, item.value_normalized, item))

    if detect_conflicts:
        for key, entries in span_index.items():
            if len(entries) > 1:
                values = set(e[1] for e in entries)
                if len(values) > 1:
                    conflicts.append({
                        "conflict_id": f"conflict_{hashlib.md5(key.encode()).hexdigest()[:8]}",
                        "item_type": entries[0][2].item_type.value,
                        "span_key": key,
                        "values": [
                            {"extractor_id": e[0], "value": e[1]}
                            for e in entries
                        ],
                    })

    logger.info(f"Agregação legacy: {len(union_items)} items, {len(conflicts)} conflitos")
    return union_items, conflicts


# ============================================================================
# COBERTURA
# ============================================================================

def calculate_coverage(
    chunks: List[Chunk],
    items: List[EvidenceItem],
    total_chars: int,
    page_mapper: Optional[Any] = None,
    total_pages: Optional[int] = None
) -> Dict:
    """
    Calcula cobertura do documento (chars e páginas).

    Args:
        chunks: Lista de chunks processados
        items: Items extraídos
        total_chars: Total de caracteres do documento
        page_mapper: CharToPageMapper opcional para cobertura por páginas
        total_pages: Total de páginas do documento (opcional)

    Returns:
        Dict com métricas de cobertura (chars e páginas)
    """
    # Cobertura por chunks
    chunk_ranges = [(c.start_char, c.end_char) for c in chunks]

    # Merge ranges sobrepostos
    merged = _merge_ranges(chunk_ranges)

    # Calcular chars cobertos
    covered_chars = sum(end - start for start, end in merged)
    coverage_percent = (covered_chars / total_chars) * 100 if total_chars > 0 else 0

    # Encontrar gaps
    gaps = _find_gaps(merged, total_chars)

    # Cobertura por extrator
    coverage_by_extractor = {}
    for item in items:
        for span in item.source_spans:
            ext_id = span.extractor_id
            if ext_id not in coverage_by_extractor:
                coverage_by_extractor[ext_id] = []
            coverage_by_extractor[ext_id].append((span.start_char, span.end_char))

    result = {
        "total_chars": total_chars,
        "covered_chars": covered_chars,
        "coverage_percent": round(coverage_percent, 2),
        "is_complete": len([g for g in gaps if g[1] - g[0] >= 100]) == 0,
        "chunks_count": len(chunks),
        "merged_ranges": len(merged),
        "gaps": [{"start": g[0], "end": g[1], "length": g[1] - g[0]} for g in gaps],
        "items_count": len(items),
        "coverage_by_extractor": {
            k: len(v) for k, v in coverage_by_extractor.items()
        },
    }

    # Adicionar cobertura por páginas se mapper disponível
    if page_mapper is not None:
        # Calcular páginas cobertas pelos chunks
        pages_covered = set()
        for chunk in chunks:
            pages = page_mapper.get_pages_for_range(chunk.start_char, chunk.end_char)
            pages_covered.update(pages)

        # Páginas ilegíveis (SUSPEITA, SEM_TEXTO, VISUAL_ONLY)
        pages_unreadable = set(page_mapper.get_unreadable_pages())

        # Total de páginas
        pages_total = page_mapper.total_pages if page_mapper.total_pages > 0 else (total_pages or 1)

        # Páginas que faltam = total - cobertas - ilegíveis
        all_pages = set(range(1, pages_total + 1))
        pages_missing = all_pages - pages_covered - pages_unreadable

        # Cobertura por páginas
        readable_pages = pages_total - len(pages_unreadable)
        pages_coverage_percent = (len(pages_covered) / readable_pages * 100) if readable_pages > 0 else 0

        result.update({
            "pages_total": pages_total,
            "pages_covered": len(pages_covered),
            "pages_covered_list": sorted(pages_covered),
            "pages_unreadable": len(pages_unreadable),
            "pages_unreadable_list": sorted(pages_unreadable),
            "pages_missing": len(pages_missing),
            "pages_missing_list": sorted(pages_missing),
            "pages_coverage_percent": round(pages_coverage_percent, 2),
            "pages_is_complete": len(pages_missing) == 0,
        })
    elif total_pages is not None:
        # Sem mapper mas com total_pages - info parcial
        result["pages_total"] = total_pages

    return result


def _merge_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge ranges sobrepostos."""
    if not ranges:
        return []

    sorted_ranges = sorted(ranges, key=lambda r: r[0])
    merged = [sorted_ranges[0]]

    for current in sorted_ranges[1:]:
        last = merged[-1]
        if current[0] <= last[1]:
            merged[-1] = (last[0], max(last[1], current[1]))
        else:
            merged.append(current)

    return merged


def _find_gaps(merged_ranges: List[Tuple[int, int]], total: int) -> List[Tuple[int, int]]:
    """Encontra intervalos não cobertos."""
    gaps = []
    prev_end = 0

    for start, end in merged_ranges:
        if start > prev_end:
            gaps.append((prev_end, start))
        prev_end = max(prev_end, end)

    if prev_end < total:
        gaps.append((prev_end, total))

    return gaps


# ============================================================================
# CONVERSÃO PARA MARKDOWN (compatibilidade)
# ============================================================================

def items_to_markdown(
    items: List[EvidenceItem],
    include_provenance: bool = True
) -> str:
    """
    Converte items para markdown preservando proveniência.

    Args:
        items: Lista de EvidenceItems
        include_provenance: Se True, inclui info de fonte

    Returns:
        Markdown formatado
    """
    lines = ["# EXTRAÇÃO UNIFICADA COM PROVENIÊNCIA\n"]

    # Agrupar por tipo
    by_type: Dict[ItemType, List[EvidenceItem]] = {}
    for item in items:
        if item.item_type not in by_type:
            by_type[item.item_type] = []
        by_type[item.item_type].append(item)

    # Ordem de apresentação
    type_order = [
        ItemType.FACT, ItemType.DATE, ItemType.AMOUNT,
        ItemType.LEGAL_REF, ItemType.ENTITY, ItemType.VISUAL, ItemType.OTHER
    ]

    for item_type in type_order:
        if item_type not in by_type:
            continue

        type_items = by_type[item_type]
        lines.append(f"\n## {item_type.value.upper()} ({len(type_items)})\n")

        for item in type_items:
            # Valor
            lines.append(f"- **{item.value_normalized}**")

            if item.raw_text and item.raw_text != item.value_normalized:
                lines.append(f"  - Original: _{item.raw_text}_")

            if include_provenance:
                # Página (informação relevante para o utilizador)
                span = item.primary_span
                if span.page_num:
                    lines.append(f"  - Página: {span.page_num}")

            lines.append("")

    return "\n".join(lines)


def render_agregado_markdown_from_json(agregado_json: Dict) -> str:
    """
    Renderiza markdown a partir do JSON do agregado (JSON é fonte de verdade).

    Args:
        agregado_json: Dict com a estrutura do agregado consolidado

    Returns:
        Markdown formatado derivado do JSON
    """
    lines = []

    # Cabeçalho
    doc_meta = agregado_json.get("doc_meta", {})
    summary = agregado_json.get("summary", {})
    coverage = agregado_json.get("coverage_report", {})

    lines.extend([
        "# EXTRAÇÃO CONSOLIDADA (AGREGADOR + PROVENIÊNCIA)",
        "## Metadados de Cobertura",
        f"Items: {summary.get('total_items', 0)} | "
        f"Cobertura: {coverage.get('coverage_percent', 0):.1f}% | "
        f"Conflitos: {agregado_json.get('conflicts_count', 0)}",
        "",
    ])

    # Items por tipo
    union_items = agregado_json.get("union_items", [])
    if union_items:
        # Agrupar por tipo
        by_type: Dict[str, List[Dict]] = {}
        for item in union_items:
            item_type = item.get("item_type", "other")
            if item_type not in by_type:
                by_type[item_type] = []
            by_type[item_type].append(item)

        # Ordem de apresentação
        type_order = ["fact", "date", "amount", "legal_ref", "entity", "visual", "other"]

        for item_type in type_order:
            if item_type not in by_type:
                continue

            type_items = by_type[item_type]
            lines.append(f"\n## {item_type.upper()} ({len(type_items)})\n")

            for item in type_items:
                value = item.get("value_normalized", "N/A")
                lines.append(f"- **{value}**")

                raw_text = item.get("raw_text")
                if raw_text and raw_text != value:
                    lines.append(f"  - Original: _{raw_text}_")

                # Página (informação relevante para o utilizador)
                source_spans = item.get("source_spans", [])
                if source_spans:
                    span = source_spans[0]
                    if span.get("page_num"):
                        lines.append(f"  - Página: {span.get('page_num')}")

                lines.append("")

    # Partes ilegíveis (relevante para o utilizador)
    unreadable = agregado_json.get("unreadable_parts", [])
    if unreadable:
        lines.extend([
            "",
            "---",
            "## Secções Ilegíveis",
            "",
        ])
        for part in unreadable:
            page_info = f" (pág. {part.get('page_num')})" if part.get('page_num') else ""
            lines.append(f"- {part.get('doc_id', 'doc')}{page_info}: {part.get('reason', 'ilegível')}")

    return "\n".join(lines)


# ============================================================================
# v4.0 HANDOVER — AUTO-REPAIR JSON + EXTRAÇÃO RECURSIVA
# ============================================================================

def auto_repair_json(text: str) -> Optional[str]:
    """
    v4.0 Handover — Repara JSON malformado de outputs LLM.

    Fixes applied:
    1. Remove markdown fences (```json...```)
    2. Remove text before first { or [
    3. Remove text after last } or ]
    4. Count brackets and close missing ones
    5. Remove trailing commas before } or ]
    6. Handle truncated strings

    Args:
        text: Raw text potentially containing malformed JSON

    Returns:
        Repaired JSON string, or None if unrecoverable
    """
    if not text or not text.strip():
        return None

    import re as _re

    cleaned = text.strip()

    # 1. Remove markdown fences
    cleaned = _re.sub(r'```(?:json)?\s*', '', cleaned)
    cleaned = _re.sub(r'```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    # 2. Find first { or [
    first_brace = -1
    for i, c in enumerate(cleaned):
        if c in '{[':
            first_brace = i
            break
    if first_brace < 0:
        return None
    cleaned = cleaned[first_brace:]

    # 3. Find last } or ]
    last_brace = -1
    for i in range(len(cleaned) - 1, -1, -1):
        if cleaned[i] in '}]':
            last_brace = i
            break
    if last_brace >= 0:
        cleaned = cleaned[:last_brace + 1]

    # 4. Remove trailing commas before } or ]
    cleaned = _re.sub(r',\s*([}\]])', r'\1', cleaned)

    # 5. Count and fix unbalanced brackets
    open_braces = cleaned.count('{') - cleaned.count('}')
    open_brackets = cleaned.count('[') - cleaned.count(']')

    # Handle truncated strings (unclosed quotes)
    in_string = False
    escaped = False
    for c in cleaned:
        if escaped:
            escaped = False
            continue
        if c == '\\':
            escaped = True
            continue
        if c == '"':
            in_string = not in_string

    if in_string:
        # Close the string
        cleaned += '"'

    # Close missing brackets
    if open_brackets > 0:
        cleaned += ']' * open_brackets
    if open_braces > 0:
        cleaned += '}' * open_braces

    # 6. Try to parse
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        # Last resort: try to fix common issues
        # Remove any control characters
        cleaned = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', cleaned)
        try:
            json.loads(cleaned)
            return cleaned
        except json.JSONDecodeError:
            logger.warning("[JSON-REPAIR] Could not repair JSON after all attempts")
            return None


def detect_continuation(json_data: dict) -> Optional[int]:
    """
    Detect if extraction output signals it was truncated.

    Checks for:
    - {"status": "to_be_continued", "last_item_id": N}
    - Presence of continuation markers

    Returns:
        last_item_id if continuation needed, None otherwise
    """
    if not isinstance(json_data, dict):
        return None

    status = json_data.get("status", "")
    if status == "to_be_continued":
        return json_data.get("last_item_id", 0)

    # Check in items array
    items = json_data.get("items", [])
    if items and isinstance(items[-1], dict):
        last_item = items[-1]
        if last_item.get("status") == "to_be_continued":
            return last_item.get("last_item_id", last_item.get("id", len(items)))

    return None


def recursive_extraction(
    llm_client,
    model: str,
    chunk,
    area_direito: str,
    extractor_id: str,
    system_prompt: str,
    temperature: float = 0.0,
    max_iterations: int = 5,
    max_tokens: int = 32768,
    page_mapper=None,
) -> Tuple[List[Any], List[Dict], List[str]]:
    """
    v4.0 Handover — Extração recursiva para outputs truncados.

    Se o LLM indica "to_be_continued", reinicia a partir do último item.
    Máximo max_iterations iterações.

    Args:
        llm_client: OpenRouterClient
        model: Model ID
        chunk: Chunk a processar
        area_direito: Legal domain
        extractor_id: E1-E7
        system_prompt: System prompt to use
        temperature: LLM temperature
        max_iterations: Max continuation rounds
        max_tokens: Max tokens per call
        page_mapper: Optional page mapper

    Returns:
        (all_items, all_unreadable, all_errors)
    """
    all_items = []
    all_unreadable = []
    all_errors = []

    last_item_id = None

    for iteration in range(max_iterations):
        # Build prompt
        prompt = build_unified_prompt(chunk, area_direito, extractor_id)

        if last_item_id is not None:
            prompt += f"\n\nCONTINUATION: Resume extraction from item ID {last_item_id + 1}. Do NOT repeat items 1-{last_item_id}."

        try:
            response = llm_client.chat_simple(
                model=model,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            if not response or not response.content:
                all_errors.append(f"Iteration {iteration}: empty response")
                break

            # Try to repair JSON if needed
            content = response.content
            try:
                json.loads(content)
            except (json.JSONDecodeError, ValueError):
                repaired = auto_repair_json(content)
                if repaired:
                    content = repaired
                    logger.info(f"[RECURSIVE] Iteration {iteration}: JSON repaired")

            # Parse output
            items, unreadable, errors = parse_unified_output(
                output=content,
                chunk=chunk,
                extractor_id=extractor_id,
                model_name=model,
                page_mapper=page_mapper,
            )

            all_items.extend(items)
            all_unreadable.extend(unreadable)
            all_errors.extend(errors)

            # Check for continuation
            from src.pipeline.extractor_json import extract_json_from_text
            json_data = extract_json_from_text(content)
            if json_data:
                continuation = detect_continuation(json_data)
                if continuation is not None:
                    last_item_id = continuation
                    logger.info(
                        f"[RECURSIVE] Iteration {iteration}: "
                        f"to_be_continued at item {last_item_id}, continuing..."
                    )
                    continue

            # No continuation needed
            logger.info(
                f"[RECURSIVE] Iteration {iteration}: "
                f"extraction complete ({len(items)} items this round, {len(all_items)} total)"
            )
            break

        except Exception as e:
            all_errors.append(f"Iteration {iteration}: {e}")
            logger.error(f"[RECURSIVE] Iteration {iteration} failed: {e}")
            break

    return all_items, all_unreadable, all_errors


# ============================================================================
# EXEMPLO / TESTE
# ============================================================================

if __name__ == "__main__":
    from src.pipeline.schema_unified import Chunk, ExtractionMethod

    # Criar chunk de teste
    texto_teste = """
    CONTRATO DE ARRENDAMENTO

    Celebrado em 15 de Janeiro de 2024, entre:

    SENHORIO: João Silva, portador do NIF 123456789
    INQUILINO: Maria Santos, portadora do NIF 987654321

    Pelo presente contrato, o SENHORIO arrenda ao INQUILINO o imóvel sito na
    Rua das Flores, n.º 123, Lisboa, pelo valor mensal de €850,00 (oitocentos
    e cinquenta euros).

    O contrato tem a duração de 2 (dois) anos, com início em 01/02/2024.

    Nos termos do artigo 1022º do Código Civil e da Lei n.º 6/2006.
    """

    chunk = Chunk(
        doc_id="test_doc",
        chunk_id="test_doc_c0000",
        chunk_index=0,
        total_chunks=1,
        start_char=0,
        end_char=len(texto_teste),
        overlap=0,
        text=texto_teste,
        method=ExtractionMethod.TEXT,
    )

    # Extrair por fallback regex
    items = _fallback_extract_with_offsets(chunk, "E1")

    print(f"\n=== {len(items)} items extraídos ===\n")
    for item in items:
        span = item.primary_span
        print(f"[{item.item_type.value}] {item.value_normalized}")
        print(f"  Chars: {span.start_char}-{span.end_char}")
        print(f"  Raw: {item.raw_text}")
        print()

    # Gerar markdown
    md = items_to_markdown(items)
    print("\n=== MARKDOWN ===\n")
    print(md)

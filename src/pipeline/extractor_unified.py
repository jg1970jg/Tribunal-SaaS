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
        errors.append(f"Não foi possível extrair JSON do output do {extractor_id}")
        # Tentar fallback para extração por regex
        items = _fallback_extract_with_offsets(chunk, extractor_id, page_mapper)
        return items, unreadable, errors

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
# AGREGADOR COM PROVENIÊNCIA
# ============================================================================

def aggregate_with_provenance(
    items_by_extractor: Dict[str, List[EvidenceItem]],
    detect_conflicts: bool = True
) -> Tuple[List[EvidenceItem], List[Dict]]:
    """
    Agrega items de múltiplos extratores preservando proveniência.

    SEM DEDUPLICAÇÃO - mantém tudo, mas detecta conflitos.

    Args:
        items_by_extractor: {extractor_id: [items]}
        detect_conflicts: Se True, detecta valores divergentes

    Returns:
        (union_items, conflicts)
    """
    union_items = []
    conflicts = []

    # Índice por span para detecção de conflitos
    # key = (item_type, span_aproximado) -> [(extractor, value, item)]
    span_index: Dict[str, List[Tuple[str, str, EvidenceItem]]] = {}

    for extractor_id, items in items_by_extractor.items():
        for item in items:
            # Adicionar à união
            union_items.append(item)

            if detect_conflicts:
                # Indexar para detecção de conflitos
                for span in item.source_spans:
                    # Usar bucket de 100 chars para agrupar spans próximos
                    bucket = span.start_char // 100
                    key = f"{item.item_type.value}:{span.doc_id}:{bucket}"

                    if key not in span_index:
                        span_index[key] = []
                    span_index[key].append((extractor_id, item.value_normalized, item))

    # Detectar conflitos
    if detect_conflicts:
        for key, entries in span_index.items():
            if len(entries) > 1:
                # Verificar valores diferentes
                values = set(e[1] for e in entries)
                if len(values) > 1:
                    conflict = {
                        "conflict_id": f"conflict_{hashlib.md5(key.encode()).hexdigest()[:8]}",
                        "item_type": entries[0][2].item_type.value,
                        "span_key": key,
                        "values": [
                            {"extractor_id": e[0], "value": e[1]}
                            for e in entries
                        ],
                    }
                    conflicts.append(conflict)

    logger.info(
        f"Agregação: {len(union_items)} items, {len(conflicts)} conflitos"
    )

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

# ============================================================================
# Pipeline v4.2 — M6: Chunking Adaptativo
# ============================================================================
# Divide o documento em chunks semânticos para análise jurídica por chunk.
#
# Características:
# - Tabelas são blocos atómicos (nunca divididas)
# - Respeita fronteiras de parágrafo
# - Target: ~4000 tokens (~16000 chars) com 500 tokens de overlap
# - Cada chunk inclui referências às entidades que contém
# ============================================================================

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.config import V42_CHUNK_TARGET_TOKENS, V42_CHUNK_OVERLAP_TOKENS

logger = logging.getLogger(__name__)

# Estimativa: 1 token ≈ 4 chars (para português)
CHARS_PER_TOKEN = 4


@dataclass
class TableRegion:
    """Região de tabela no texto (atómica, não pode ser dividida)."""
    start_char: int
    end_char: int
    page_num: int
    text: str


@dataclass
class SemanticChunk:
    """Chunk semântico para análise jurídica."""
    chunk_index: int
    start_char: int
    end_char: int
    text: str
    page_start: int
    page_end: int
    entity_refs: list[str] = field(default_factory=list)  # entity_ids
    contains_table: bool = False
    token_count: int = 0

    def to_dict(self) -> dict:
        return {
            "chunk_index": self.chunk_index,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "text_length": len(self.text),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "entity_refs": self.entity_refs,
            "contains_table": self.contains_table,
            "token_count": self.token_count,
        }


def create_chunks(
    text: str,
    entity_registry=None,  # EntityRegistry from m5_entity_lock
    tables: Optional[list] = None,  # list[ExtractedTable] from m3b_multifeature
    page_boundaries: Optional[dict[int, tuple[int, int]]] = None,
    target_tokens: Optional[int] = None,
    overlap_tokens: Optional[int] = None,
) -> list[SemanticChunk]:
    """
    M6: Chunking adaptativo.

    Divide texto em chunks semânticos respeitando tabelas e parágrafos.

    Args:
        text: texto completo do documento (após M4 limpeza)
        entity_registry: registo de entidades travadas (M5)
        tables: tabelas extraídas (M3B) para marcar como atómicas
        page_boundaries: {page_num: (start_char, end_char)}
        target_tokens: tamanho alvo por chunk em tokens
        overlap_tokens: sobreposição entre chunks em tokens

    Returns:
        Lista de SemanticChunk
    """
    if not text or not text.strip():
        return []

    target = target_tokens or V42_CHUNK_TARGET_TOKENS
    overlap = overlap_tokens or V42_CHUNK_OVERLAP_TOKENS
    target_chars = target * CHARS_PER_TOKEN
    overlap_chars = overlap * CHARS_PER_TOKEN

    logger.info(
        f"[M6] Chunking: {len(text):,} chars, "
        f"target={target} tokens ({target_chars} chars), "
        f"overlap={overlap} tokens ({overlap_chars} chars)"
    )

    # 1. Identificar regiões de tabela (atómicas)
    table_regions = _identify_table_regions(tables, text)

    # 2. Identificar pontos de divisão (fronteiras de parágrafo)
    split_points = _find_split_points(text, table_regions)

    # 3. Construir chunks
    chunks = _build_chunks(
        text, split_points, table_regions,
        target_chars, overlap_chars, page_boundaries
    )

    # 4. Enriquecer com referências a entidades
    if entity_registry:
        for chunk in chunks:
            chunk.entity_refs = entity_registry.get_entity_ids_in_range(
                chunk.start_char, chunk.end_char
            )

    # 5. Calcular tokens
    for chunk in chunks:
        chunk.token_count = len(chunk.text) // CHARS_PER_TOKEN

    logger.info(
        f"[M6] Chunking concluído: {len(chunks)} chunks "
        f"(média {sum(c.token_count for c in chunks) // max(len(chunks), 1)} tokens/chunk)"
    )

    return chunks


def _identify_table_regions(
    tables: Optional[list],
    text: str,
) -> list[TableRegion]:
    """Identificar regiões de tabela no texto."""
    if not tables:
        return []

    regions = []
    for table in tables:
        # Tentar encontrar a tabela no texto
        if table.raw_text:
            # Procurar o texto da tabela no documento
            idx = text.find(table.raw_text[:50])  # Primeiros 50 chars
            if idx >= 0:
                end_idx = idx + len(table.raw_text)
                regions.append(TableRegion(
                    start_char=idx,
                    end_char=end_idx,
                    page_num=table.page_num,
                    text=table.raw_text,
                ))

    # Ordenar por posição
    regions.sort(key=lambda r: r.start_char)
    return regions


def _find_split_points(text: str, table_regions: list[TableRegion]) -> list[int]:
    """
    Encontrar pontos válidos de divisão.

    Prioridade:
    1. Parágrafos duplos (\n\n)
    2. Parágrafos simples (\n) após ponto final
    3. Pontos finais seguidos de espaço

    Nunca dividir dentro de uma região de tabela.
    """
    split_points = [0]  # Início do texto

    # Construir set de ranges protegidos (tabelas)
    protected_ranges = [(r.start_char, r.end_char) for r in table_regions]

    # Encontrar parágrafos duplos
    idx = 0
    while True:
        idx = text.find("\n\n", idx)
        if idx == -1:
            break
        if not _in_protected_range(idx, protected_ranges):
            split_points.append(idx + 2)  # Após o \n\n
        idx += 2

    # Se poucos split points, adicionar quebras de linha após ponto
    if len(split_points) < 3:
        idx = 0
        while True:
            idx = text.find(".\n", idx)
            if idx == -1:
                break
            pos = idx + 2
            if not _in_protected_range(pos, protected_ranges) and pos not in split_points:
                split_points.append(pos)
            idx += 2

    split_points.append(len(text))  # Fim do texto
    split_points = sorted(set(split_points))

    return split_points


def _in_protected_range(pos: int, ranges: list[tuple[int, int]]) -> bool:
    """Verificar se posição cai dentro de uma range protegida."""
    return any(start <= pos < end for start, end in ranges)


def _build_chunks(
    text: str,
    split_points: list[int],
    table_regions: list[TableRegion],
    target_chars: int,
    overlap_chars: int,
    page_boundaries: Optional[dict[int, tuple[int, int]]],
) -> list[SemanticChunk]:
    """Construir chunks a partir dos split points."""
    chunks = []
    chunk_index = 0

    # Agrupar split points em chunks
    current_start = 0
    current_end = 0

    i = 0
    while i < len(split_points):
        sp = split_points[i]

        # Se adicionar este segmento excede o target
        segment_size = sp - current_start
        if segment_size > target_chars and current_end > current_start:
            # Criar chunk com o acumulado até agora
            chunk_text = text[current_start:current_end].strip()
            if chunk_text:
                page_start, page_end = _get_page_range(
                    current_start, current_end, page_boundaries
                )
                contains_table = any(
                    r.start_char < current_end and r.end_char > current_start
                    for r in table_regions
                )
                chunks.append(SemanticChunk(
                    chunk_index=chunk_index,
                    start_char=current_start,
                    end_char=current_end,
                    text=chunk_text,
                    page_start=page_start,
                    page_end=page_end,
                    contains_table=contains_table,
                ))
                chunk_index += 1

            # Overlap: recuar no texto
            overlap_start = max(current_end - overlap_chars, current_start)
            # Encontrar split point mais próximo do overlap_start
            best_overlap = current_end
            for j in range(len(split_points)):
                if split_points[j] >= overlap_start and split_points[j] < current_end:
                    best_overlap = split_points[j]
                    break

            current_start = best_overlap
            current_end = sp
        else:
            current_end = sp

        i += 1

    # Último chunk
    if current_end > current_start:
        chunk_text = text[current_start:current_end].strip()
        if chunk_text:
            page_start, page_end = _get_page_range(
                current_start, current_end, page_boundaries
            )
            contains_table = any(
                r.start_char < current_end and r.end_char > current_start
                for r in table_regions
            )
            chunks.append(SemanticChunk(
                chunk_index=chunk_index,
                start_char=current_start,
                end_char=current_end,
                text=chunk_text,
                page_start=page_start,
                page_end=page_end,
                contains_table=contains_table,
            ))

    # Se não conseguimos criar chunks (texto muito pequeno), criar um único
    if not chunks and text.strip():
        page_start, page_end = _get_page_range(0, len(text), page_boundaries)
        chunks.append(SemanticChunk(
            chunk_index=0,
            start_char=0,
            end_char=len(text),
            text=text.strip(),
            page_start=page_start,
            page_end=page_end,
        ))

    return chunks


def _get_page_range(
    start_char: int,
    end_char: int,
    page_boundaries: Optional[dict[int, tuple[int, int]]],
) -> tuple[int, int]:
    """Determinar range de páginas para um chunk."""
    if not page_boundaries:
        return (1, 1)

    page_start = 1
    page_end = 1

    for page_num, (p_start, p_end) in sorted(page_boundaries.items()):
        if p_start <= start_char < p_end:
            page_start = page_num
        if p_start < end_char <= p_end:
            page_end = page_num

    return (page_start, page_end)


def build_page_boundaries(ocr_pages: list) -> dict[int, tuple[int, int]]:
    """
    Construir mapeamento de páginas para posições de caracteres.

    Útil para M5 e M6.

    Args:
        ocr_pages: lista de OCRPageResult do M3

    Returns:
        {page_num: (start_char, end_char)}
    """
    boundaries = {}
    current_pos = 0

    for page in sorted(ocr_pages, key=lambda p: p.page_num):
        text_len = len(page.consensus_text)
        boundaries[page.page_num] = (current_pos, current_pos + text_len)
        current_pos += text_len + 2  # +2 for \n\n separator

    return boundaries

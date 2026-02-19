"""
Mapeamento de offsets de caracteres para páginas.

Permite rastrear qualquer posição no texto de volta à página original.
Suporta dois modos:
1. PDFSafe: usa PageRecord.text_clean para cálculo preciso
2. Fallback: usa marcadores [Página X] no texto
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Any


logger = logging.getLogger(__name__)


@dataclass
class PageBoundary:
    """Limites de uma página no texto concatenado."""
    page_num: int
    start_char: int  # Offset absoluto do início da página
    end_char: int    # Offset absoluto do fim da página
    char_count: int  # Número de caracteres na página
    status: str = "OK"  # OK | SUSPEITA | SEM_TEXTO | REPARADA

    @property
    def contains(self) -> range:
        return range(self.start_char, self.end_char)

    def contains_offset(self, offset: int) -> bool:
        return self.start_char <= offset < self.end_char


@dataclass
class CharToPageMapper:
    """
    Mapeia offsets de caracteres para números de página.

    Uso:
        mapper = CharToPageMapper.from_pdf_safe_result(pdf_result)
        # ou
        mapper = CharToPageMapper.from_text_markers(documento.text)

        page_num = mapper.get_page(12345)  # -> 5
        pages = mapper.get_pages_for_range(10000, 15000)  # -> [3, 4, 5]
    """
    boundaries: list[PageBoundary] = field(default_factory=list)
    total_chars: int = 0
    total_pages: int = 0
    doc_id: str = ""
    source: str = ""  # "pdf_safe" | "markers" | "unknown"

    def __post_init__(self):
        if self.boundaries:
            self.total_pages = len(self.boundaries)
            self.total_chars = self.boundaries[-1].end_char if self.boundaries else 0

    @classmethod
    def from_pdf_safe_result(cls, pdf_result: Any, doc_id: str = "") -> 'CharToPageMapper':
        """
        Cria mapper a partir de PDFSafeResult.
        Usa text_clean (ou override_text) de cada PageRecord.
        """
        boundaries = []
        current_offset = 0

        for page in pdf_result.pages:
            # Usar override se existir, senão text_clean
            page_text = page.override_text if page.override_text else page.text_clean

            # Adicionar marcador [Página X] que é inserido no texto final
            # O formato é "[Página {page_num}]\n{page_text}\n\n"
            marker = f"[Página {page.page_num}]\n"
            full_page_content = marker + page_text + "\n\n"
            char_count = len(full_page_content)

            boundary = PageBoundary(
                page_num=page.page_num,
                start_char=current_offset,
                end_char=current_offset + char_count,
                char_count=char_count,
                status=page.status_final,
            )
            boundaries.append(boundary)
            current_offset += char_count

        mapper = cls(
            boundaries=boundaries,
            doc_id=doc_id,
            source="pdf_safe",
        )

        logger.info(f"PageMapper criado (pdf_safe): {mapper.total_pages} páginas, {mapper.total_chars:,} chars")
        return mapper

    @classmethod
    def from_text_markers(cls, text: str, doc_id: str = "") -> 'CharToPageMapper':
        """
        Cria mapper a partir de marcadores [Página X] no texto.
        Fallback quando PDFSafe não está disponível.
        """
        # Padrão para encontrar marcadores de página
        pattern = re.compile(r'\[Página\s*(\d+)\]', re.IGNORECASE)

        boundaries = []
        matches = list(pattern.finditer(text))

        if not matches:
            # Sem marcadores - tratar como página única
            logger.warning("Sem marcadores [Página X] encontrados, tratando como página única")
            boundary = PageBoundary(
                page_num=1,
                start_char=0,
                end_char=len(text),
                char_count=len(text),
                status="OK",
            )
            return cls(
                boundaries=[boundary],
                doc_id=doc_id,
                source="markers",
            )

        for i, match in enumerate(matches):
            page_num = int(match.group(1))
            start_char = match.start()

            # Fim é o início do próximo marcador ou fim do texto
            if i + 1 < len(matches):
                end_char = matches[i + 1].start()
            else:
                end_char = len(text)

            boundary = PageBoundary(
                page_num=page_num,
                start_char=start_char,
                end_char=end_char,
                char_count=end_char - start_char,
                status="OK",
            )
            boundaries.append(boundary)

        mapper = cls(
            boundaries=boundaries,
            doc_id=doc_id,
            source="markers",
        )

        logger.info(f"PageMapper criado (markers): {mapper.total_pages} páginas, {mapper.total_chars:,} chars")
        return mapper

    @classmethod
    def from_document_content(cls, documento: Any, doc_id: str = "") -> 'CharToPageMapper':
        """
        Cria mapper a partir de DocumentContent.
        Usa PDFSafe se disponível, senão fallback para marcadores.
        """
        # Tentar PDFSafe primeiro
        if hasattr(documento, 'pdf_safe_result') and documento.pdf_safe_result is not None:
            return cls.from_pdf_safe_result(documento.pdf_safe_result, doc_id)

        # Fallback para marcadores
        return cls.from_text_markers(documento.text, doc_id)

    def get_page(self, char_offset: int) -> Optional[int]:
        """
        Retorna o número da página para um offset de caractere.
        Usa busca binária para eficiência em documentos grandes.

        Args:
            char_offset: Offset absoluto no texto

        Returns:
            Número da página (1-based) ou None se não encontrado
        """
        if not self.boundaries:
            return None

        # Busca binária pelo offset
        left, right = 0, len(self.boundaries) - 1

        while left <= right:
            mid = (left + right) // 2
            boundary = self.boundaries[mid]

            if boundary.contains_offset(char_offset):
                return boundary.page_num
            elif char_offset < boundary.start_char:
                right = mid - 1
            else:  # char_offset >= boundary.end_char
                left = mid + 1

        # Se offset maior que total, retorna última página
        if char_offset >= self.total_chars:
            return self.boundaries[-1].page_num

        return None

    def get_page_range(self, start_char: int, end_char: int) -> tuple[Optional[int], Optional[int]]:
        """
        Retorna (page_start, page_end) para um intervalo de caracteres.

        Args:
            start_char: Offset inicial
            end_char: Offset final

        Returns:
            (page_start, page_end) ou (None, None) se não mapeável
        """
        pages = self.get_pages_for_range(start_char, end_char)
        if not pages:
            return None, None
        return pages[0], pages[-1]

    def get_pages_for_range(self, start_char: int, end_char: int) -> list[int]:
        """
        Retorna lista de páginas que um intervalo de caracteres abrange.

        Args:
            start_char: Offset inicial
            end_char: Offset final

        Returns:
            Lista de números de página (ordenada, sem duplicados)
        """
        pages = set()

        for boundary in self.boundaries:
            # Verificar se há sobreposição
            if not (boundary.end_char <= start_char or boundary.start_char >= end_char):
                pages.add(boundary.page_num)

        return sorted(pages)

    def get_boundary(self, page_num: int) -> Optional[PageBoundary]:
        """Retorna o boundary para uma página específica."""
        for boundary in self.boundaries:
            if boundary.page_num == page_num:
                return boundary
        return None

    def get_page_status(self, page_num: int) -> str:
        """Retorna o status de uma página."""
        boundary = self.get_boundary(page_num)
        return boundary.status if boundary else "UNKNOWN"

    def get_unreadable_pages(self) -> list[int]:
        """Retorna lista de páginas com status problemático."""
        return [
            b.page_num for b in self.boundaries
            if b.status in ["SUSPEITA", "SEM_TEXTO", "VISUAL_ONLY"]
        ]

    def get_coverage_by_pages(self, char_ranges: list[tuple[int, int]]) -> dict:
        """
        Calcula cobertura por páginas dado um conjunto de intervalos de caracteres.

        Args:
            char_ranges: Lista de (start_char, end_char)

        Returns:
            Dict com métricas de cobertura por página
        """
        pages_touched = set()
        for start, end in char_ranges:
            pages_touched.update(self.get_pages_for_range(start, end))

        all_pages = set(b.page_num for b in self.boundaries)
        pages_missing = all_pages - pages_touched

        return {
            "pages_total": len(all_pages),
            "pages_covered": len(pages_touched),
            "pages_missing": len(pages_missing),
            "pages_covered_list": sorted(pages_touched),
            "pages_missing_list": sorted(pages_missing),
            "coverage_percent": (len(pages_touched) / len(all_pages) * 100) if all_pages else 0,
        }

    def to_dict(self) -> dict:
        """Serializa mapper para dict."""
        return {
            "doc_id": self.doc_id,
            "source": self.source,
            "total_pages": self.total_pages,
            "total_chars": self.total_chars,
            "boundaries": [
                {
                    "page_num": b.page_num,
                    "start_char": b.start_char,
                    "end_char": b.end_char,
                    "char_count": b.char_count,
                    "status": b.status,
                }
                for b in self.boundaries
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CharToPageMapper':
        """Reconstrói mapper a partir de dict."""
        boundaries = [
            PageBoundary(
                page_num=b["page_num"],
                start_char=b["start_char"],
                end_char=b["end_char"],
                char_count=b["char_count"],
                status=b.get("status", "OK"),
            )
            for b in data.get("boundaries", [])
        ]
        return cls(
            boundaries=boundaries,
            doc_id=data.get("doc_id", ""),
            source=data.get("source", "unknown"),
        )


# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def map_char_offset_to_page(
    doc_id: str,
    abs_start: int,
    abs_end: int,
    mapper: Optional[CharToPageMapper] = None,
    pdf_result: Any = None,
    text: str = ""
) -> tuple[Optional[int], Optional[int]]:
    """
    Mapeia intervalo de caracteres para páginas.

    Args:
        doc_id: ID do documento
        abs_start: Offset absoluto inicial
        abs_end: Offset absoluto final
        mapper: CharToPageMapper pré-criado (preferido)
        pdf_result: PDFSafeResult (alternativa)
        text: Texto com marcadores (fallback)

    Returns:
        (page_start, page_end) ou (None, None) se não mapeável
    """
    # Usar mapper se fornecido
    if mapper is None:
        if pdf_result is not None:
            mapper = CharToPageMapper.from_pdf_safe_result(pdf_result, doc_id)
        elif text:
            mapper = CharToPageMapper.from_text_markers(text, doc_id)
        else:
            return None, None

    pages = mapper.get_pages_for_range(abs_start, abs_end)

    if not pages:
        return None, None

    return pages[0], pages[-1]


def enrich_citations_with_pages(
    citations: list[dict],
    mapper: CharToPageMapper
) -> list[dict]:
    """
    Adiciona page_num às citações que não têm.

    Args:
        citations: Lista de dicts de citação
        mapper: CharToPageMapper

    Returns:
        Lista de citações com page_num preenchido
    """
    enriched = []

    for citation in citations:
        c = citation.copy()

        if c.get("page_num") is None:
            start = c.get("start_char", 0)
            end = c.get("end_char", start)
            pages = mapper.get_pages_for_range(start, end)

            if pages:
                c["page_num"] = pages[0]  # Usar primeira página do intervalo

        enriched.append(c)

    return enriched


def extend_coverage_report_with_pages(
    coverage_data: dict,
    mapper: CharToPageMapper
) -> dict:
    """
    Estende relatório de cobertura com informação de páginas.

    Args:
        coverage_data: Relatório de cobertura existente (chars)
        mapper: CharToPageMapper

    Returns:
        Relatório estendido com cobertura por páginas
    """
    extended = coverage_data.copy()

    # Calcular cobertura por páginas a partir de chunks
    # Assumir que coverage_data tem informação de chunks cobertos
    # (isto seria integrado com o calculate_coverage existente)

    # Adicionar info de páginas do mapper
    extended["pages_total"] = mapper.total_pages
    extended["pages_unreadable"] = mapper.get_unreadable_pages()
    extended["pages_unreadable_count"] = len(extended["pages_unreadable"])

    # Se tivermos os char_ranges cobertos, calcular páginas cobertas
    if "merged_ranges" in coverage_data:
        # Converter ranges para páginas
        all_pages = set()
        # (simplificado - em produção usaríamos os ranges reais)
        for b in mapper.boundaries:
            if b.status == "OK":
                all_pages.add(b.page_num)

        extended["pages_covered_list"] = sorted(all_pages)
        extended["pages_covered"] = len(all_pages)
        extended["pages_missing_list"] = sorted(
            set(range(1, mapper.total_pages + 1)) - all_pages
        )
        extended["pages_missing"] = len(extended["pages_missing_list"])

    return extended


# ============================================================================
# TESTE
# ============================================================================

if __name__ == "__main__":
    # Teste com marcadores
    texto_teste = """[Página 1]
Este é o conteúdo da primeira página.
Com várias linhas de texto.

[Página 2]
Segunda página do documento.
Mais conteúdo aqui.

[Página 3]
Terceira e última página.
Fim do documento."""

    mapper = CharToPageMapper.from_text_markers(texto_teste, "doc_teste")

    print(f"Total páginas: {mapper.total_pages}")
    print(f"Total chars: {mapper.total_chars}")
    print()

    # Testar mapeamento
    test_offsets = [0, 50, 100, 150, 200]
    for offset in test_offsets:
        page = mapper.get_page(offset)
        print(f"Offset {offset} -> Página {page}")

    print()

    # Testar range
    pages = mapper.get_pages_for_range(50, 180)
    print(f"Range [50, 180) abrange páginas: {pages}")

    print()

    # Boundaries
    for b in mapper.boundaries:
        print(f"Página {b.page_num}: [{b.start_char}, {b.end_char}) - {b.char_count} chars")

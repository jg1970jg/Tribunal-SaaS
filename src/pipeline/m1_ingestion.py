# ============================================================================
# Pipeline v4.2 — M1: Ingestão de Documentos
# ============================================================================
# Módulo de ingestão: aceita ficheiro, calcula hash SHA-256, detecta
# PDFs encriptados, determina se é digitalizado (precisa OCR) ou nativo.
# ============================================================================

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DocumentIngestion:
    """Resultado da ingestão M1."""
    file_hash: str           # SHA-256 do ficheiro original
    filename: str
    extension: str           # .pdf, .docx, .xlsx, .txt
    file_bytes: bytes
    file_size: int           # bytes
    num_pages: int
    is_encrypted: bool
    is_scanned: bool         # heurística: texto/página < threshold
    native_text: str         # texto extraído nativamente (pode ser vazio para scanned)
    metadata: dict = field(default_factory=dict)


def ingest_document(
    file_bytes: bytes,
    filename: str,
    existing_text: Optional[str] = None,
    existing_num_pages: Optional[int] = None,
    scanned_threshold: int = 50,
) -> DocumentIngestion:
    """
    M1: Ingestão de documento.

    Calcula hash, detecta encriptação, determina se é digitalizado.

    Args:
        file_bytes: bytes do ficheiro original
        filename: nome do ficheiro
        existing_text: texto já extraído pelo DocumentLoader (se disponível)
        existing_num_pages: número de páginas já determinado
        scanned_threshold: chars médios por página abaixo do qual é considerado scanned

    Returns:
        DocumentIngestion com todos os metadados
    """
    # SHA-256
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    extension = _get_extension(filename)
    file_size = len(file_bytes)

    logger.info(f"[M1] Ingestão: {filename} ({file_size:,} bytes, ext={extension})")

    # Defaults
    is_encrypted = False
    is_scanned = False
    num_pages = existing_num_pages or 0
    native_text = existing_text or ""

    if extension == ".pdf":
        is_encrypted, num_pages_detected, native_text_detected = _analyze_pdf(
            file_bytes, existing_text, existing_num_pages
        )
        if num_pages_detected:
            num_pages = num_pages_detected
        if native_text_detected and not native_text:
            native_text = native_text_detected

        # Heurística: é digitalizado?
        if num_pages > 0 and native_text:
            avg_chars_per_page = len(native_text.strip()) / num_pages
            is_scanned = avg_chars_per_page < scanned_threshold
            if is_scanned:
                logger.info(
                    f"[M1] PDF digitalizado detectado: "
                    f"{avg_chars_per_page:.0f} chars/página < {scanned_threshold}"
                )
        elif num_pages > 0 and not native_text.strip():
            is_scanned = True
            logger.info("[M1] PDF sem texto nativo → digitalizado")

    elif extension in (".docx", ".xlsx", ".txt"):
        # Estes formatos são sempre texto nativo, nunca precisam OCR
        is_scanned = False
        if not num_pages:
            num_pages = 1

    metadata = {
        "file_hash": file_hash,
        "file_size": file_size,
        "extension": extension,
        "is_encrypted": is_encrypted,
        "is_scanned": is_scanned,
        "num_pages": num_pages,
        "native_text_chars": len(native_text),
    }

    logger.info(
        f"[M1] Resultado: pages={num_pages}, scanned={is_scanned}, "
        f"encrypted={is_encrypted}, text={len(native_text):,} chars"
    )

    return DocumentIngestion(
        file_hash=file_hash,
        filename=filename,
        extension=extension,
        file_bytes=file_bytes,
        file_size=file_size,
        num_pages=num_pages,
        is_encrypted=is_encrypted,
        is_scanned=is_scanned,
        native_text=native_text,
        metadata=metadata,
    )


def _get_extension(filename: str) -> str:
    """Extrair extensão do ficheiro, normalizada."""
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return ""


def _analyze_pdf(
    file_bytes: bytes,
    existing_text: Optional[str],
    existing_num_pages: Optional[int],
) -> tuple[bool, Optional[int], Optional[str]]:
    """
    Analisar PDF para detectar encriptação e extrair texto nativo.

    Returns:
        (is_encrypted, num_pages, native_text)
    """
    try:
        import fitz  # pymupdf

        doc = fitz.open(stream=file_bytes, filetype="pdf")

        is_encrypted = doc.is_encrypted
        if is_encrypted:
            logger.warning("[M1] PDF encriptado detectado")
            doc.close()
            return (True, existing_num_pages, existing_text)

        num_pages = len(doc)

        # Se já temos texto do DocumentLoader, usá-lo
        if existing_text and existing_text.strip():
            doc.close()
            return (False, num_pages, existing_text)

        # Extrair texto nativo via pymupdf
        text_parts = []
        for page in doc:
            page_text = page.get_text("text")
            if page_text:
                text_parts.append(page_text)

        doc.close()
        native_text = "\n\n".join(text_parts)
        return (False, num_pages, native_text)

    except Exception as e:
        logger.error(f"[M1] Erro ao analisar PDF: {e}")
        return (False, existing_num_pages, existing_text)

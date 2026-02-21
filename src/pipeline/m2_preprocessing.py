# ============================================================================
# Pipeline v4.2 — M2: Pré-processamento de Imagens
# ============================================================================
# Converte cada página do PDF para imagem PNG a 300 DPI e aplica deskew
# (correcção de inclinação) usando OpenCV.
# ============================================================================

import io
import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PageImage:
    """Imagem pré-processada de uma página."""
    page_num: int          # 1-based
    image_bytes: bytes     # PNG bytes
    width: int
    height: int
    dpi: int
    skew_angle: float      # ângulo de inclinação detectado (graus)
    was_deskewed: bool     # se foi aplicada correcção


def preprocess_pdf(
    file_bytes: bytes,
    dpi: int = 300,
    deskew: bool = True,
    min_skew_angle: float = 0.5,
    batch_start: int = 0,
    batch_size: Optional[int] = None,
) -> list[PageImage]:
    """
    M2: Pré-processamento de PDF.

    Converte páginas para PNG a 300 DPI com deskew opcional.

    Args:
        file_bytes: bytes do ficheiro PDF
        dpi: resolução alvo (default 300)
        deskew: aplicar correcção de inclinação
        min_skew_angle: ângulo mínimo para aplicar deskew (graus)
        batch_start: página inicial (0-based, para processamento em lotes)
        batch_size: número de páginas a processar (None = todas)

    Returns:
        Lista de PageImage com imagens pré-processadas
    """
    import fitz  # pymupdf

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)

    end_page = total_pages
    if batch_size is not None:
        end_page = min(batch_start + batch_size, total_pages)

    logger.info(
        f"[M2] Pré-processamento: páginas {batch_start + 1}-{end_page}/{total_pages} a {dpi} DPI"
    )

    results = []

    for page_idx in range(batch_start, end_page):
        page = doc[page_idx]
        page_num = page_idx + 1  # 1-based

        # Render a 300 DPI
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        # Converter para bytes PNG
        png_bytes = pix.tobytes("png")
        width = pix.width
        height = pix.height

        skew_angle = 0.0
        was_deskewed = False

        # Deskew
        if deskew:
            skew_angle = _detect_skew(png_bytes)
            if abs(skew_angle) > min_skew_angle:
                png_bytes, width, height = _apply_deskew(png_bytes, skew_angle)
                was_deskewed = True
                logger.info(f"[M2] Página {page_num}: deskew {skew_angle:.2f} graus")

        results.append(PageImage(
            page_num=page_num,
            image_bytes=png_bytes,
            width=width,
            height=height,
            dpi=dpi,
            skew_angle=skew_angle,
            was_deskewed=was_deskewed,
        ))

        logger.debug(f"[M2] Página {page_num}: {width}x{height}px, {len(png_bytes):,} bytes")

    doc.close()
    logger.info(f"[M2] Pré-processamento concluído: {len(results)} páginas")
    return results


def _detect_skew(png_bytes: bytes) -> float:
    """
    Detectar ângulo de inclinação usando Hough Line Transform.

    Returns:
        Ângulo de inclinação em graus (positivo = sentido horário)
    """
    try:
        import cv2
        import numpy as np

        # Decode PNG
        nparr = np.frombuffer(png_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)

        if img is None:
            return 0.0

        # Edge detection
        edges = cv2.Canny(img, 50, 150, apertureSize=3)

        # Hough Line Transform
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=math.pi / 180,
            threshold=100,
            minLineLength=img.shape[1] // 4,  # min line = 25% of image width
            maxLineGap=10,
        )

        if lines is None or len(lines) == 0:
            return 0.0

        # Calculate angles of all detected lines
        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 - x1 == 0:
                continue
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            # Only consider near-horizontal lines (within 15 degrees)
            if abs(angle) < 15:
                angles.append(angle)

        if not angles:
            return 0.0

        # Median angle (robust to outliers)
        angles.sort()
        median_angle = angles[len(angles) // 2]
        return median_angle

    except ImportError:
        logger.warning("[M2] OpenCV não disponível — deskew desativado")
        return 0.0
    except Exception as e:
        logger.warning(f"[M2] Erro na detecção de skew: {e}")
        return 0.0


def _apply_deskew(png_bytes: bytes, angle: float) -> tuple[bytes, int, int]:
    """
    Aplicar rotação para corrigir inclinação.

    Returns:
        (png_bytes_corrigido, width, height)
    """
    try:
        import cv2
        import numpy as np

        # Decode
        nparr = np.frombuffer(png_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            return png_bytes, 0, 0

        h, w = img.shape[:2]
        center = (w // 2, h // 2)

        # Rotation matrix (negative angle to correct the skew)
        rotation_matrix = cv2.getRotationMatrix2D(center, -angle, 1.0)

        # Calculate new bounding box dimensions
        cos_val = abs(rotation_matrix[0, 0])
        sin_val = abs(rotation_matrix[0, 1])
        new_w = int(h * sin_val + w * cos_val)
        new_h = int(h * cos_val + w * sin_val)

        # Adjust center
        rotation_matrix[0, 2] += (new_w - w) / 2
        rotation_matrix[1, 2] += (new_h - h) / 2

        # Apply rotation with white background
        rotated = cv2.warpAffine(
            img, rotation_matrix, (new_w, new_h),
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )

        # Encode back to PNG
        success, encoded = cv2.imencode(".png", rotated)
        if success:
            return encoded.tobytes(), new_w, new_h

        return png_bytes, w, h

    except Exception as e:
        logger.warning(f"[M2] Erro ao aplicar deskew: {e}")
        return png_bytes, 0, 0


def get_page_count(file_bytes: bytes) -> int:
    """Obter número de páginas do PDF sem processar imagens."""
    try:
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0

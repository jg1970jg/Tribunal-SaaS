# -*- coding: utf-8 -*-
"""
PDF SEGURO - Extração página-a-página com controlo de cobertura.

Garante ZERO omissões silenciosas:
- Cada página é extraída individualmente
- Páginas problemáticas são identificadas
- Permite reparação humana guiada
- Outputs auditáveis por página
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
from collections import Counter
import io

from src.config import LOG_LEVEL, OUTPUT_DIR

logger = logging.getLogger(__name__)


# ============================================================================
# REGEX DETERMINÍSTICOS PARA DETEÇÃO INTRA-PÁGINA
# ============================================================================

# Datas em formato português: DD/MM/AAAA, DD-MM-AAAA, DD.MM.AAAA
REGEX_DATAS_PT = re.compile(
    r'\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\b'
    r'|'
    r'\b(\d{1,2}\s+de\s+(?:janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+de\s+\d{4})\b',
    re.IGNORECASE
)

# Valores em Euro: €X.XXX,XX ou EUR X.XXX,XX ou X.XXX,XX €
REGEX_VALORES_EURO = re.compile(
    r'€\s*[\d\.,]+|\bEUR\s*[\d\.,]+|[\d\.,]+\s*€|[\d\.,]+\s*euros?\b',
    re.IGNORECASE
)

# Artigos legais portugueses: Art. Xº, Artigo Xº, DL n.º X/AAAA, Lei n.º X/AAAA
REGEX_ARTIGOS_PT = re.compile(
    r'\b(?:art(?:igo)?\.?\s*\d+[º°]?(?:\s*,?\s*n\.?[º°]?\s*\d+)?)'
    r'|'
    r'\b(?:DL|D\.?L\.?|Decreto[- ]Lei)\s*n\.?[º°]?\s*\d+[/-]\d+'
    r'|'
    r'\b(?:Lei)\s*n\.?[º°]?\s*\d+[/-]\d+'
    r'|'
    r'\bC(?:ódigo)?\s*(?:Civil|Penal|Trabalho|Processo)',
    re.IGNORECASE
)


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class PageMetrics:
    """Métricas de uma página."""
    chars_raw: int = 0
    chars_clean: int = 0
    noise_ratio: float = 0.0
    has_images: bool = False
    line_count: int = 0
    # Deteção intra-página
    dates_detected: List[str] = field(default_factory=list)
    values_detected: List[str] = field(default_factory=list)
    legal_refs_detected: List[str] = field(default_factory=list)
    page_hash: str = ""  # SHA-256 do texto da página


@dataclass
class PageRecord:
    """Registo completo de uma página."""
    page_num: int  # 1-based
    text_raw: str = ""
    text_clean: str = ""
    image_path: str = ""
    metrics: PageMetrics = field(default_factory=PageMetrics)
    status_inicial: str = "OK"  # OK / SUSPEITA / SEM_TEXTO
    status_final: str = "OK"  # OK / SUSPEITA / VISUAL_ONLY / REPARADA
    # Flags de cobertura
    covered_by: Dict[str, bool] = field(default_factory=dict)  # {E1: True, E2: False, ...}
    coverage_status: str = ""  # COBERTA / PARCIAL / NAO_COBERTA
    # Flags de suspeita intra-página
    flags: List[str] = field(default_factory=list)  # SUSPEITA_DATAS, SUSPEITA_VALORES, etc.
    # Override info
    override_type: Optional[str] = None  # upload / manual_transcription / visual_only
    override_text: str = ""
    override_note: str = ""
    # OCR auto-retry info
    ocr_attempted: bool = False
    ocr_success: bool = False
    ocr_chars: int = 0
    status_before_ocr: Optional[str] = None
    status_after_ocr: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "page_num": self.page_num,
            "text_raw_preview": self.text_raw[:500] + "..." if len(self.text_raw) > 500 else self.text_raw,
            "text_clean_preview": self.text_clean[:500] + "..." if len(self.text_clean) > 500 else self.text_clean,
            "text_raw_length": len(self.text_raw),
            "text_clean_length": len(self.text_clean),
            "image_path": self.image_path,
            "metrics": asdict(self.metrics),
            "status_inicial": self.status_inicial,
            "status_final": self.status_final,
            "covered_by": self.covered_by,
            "coverage_status": self.coverage_status,
            "flags": self.flags,
            "override_type": self.override_type,
            "override_note": self.override_note,
            # OCR auto-retry info
            "ocr_attempted": self.ocr_attempted,
            "ocr_success": self.ocr_success,
            "ocr_chars": self.ocr_chars,
            "status_before_ocr": self.status_before_ocr,
            "status_after_ocr": self.status_after_ocr,
        }


@dataclass
class PDFSafeResult:
    """Resultado completo da extração PDF Seguro."""
    filename: str
    total_pages: int
    pages: List[PageRecord] = field(default_factory=list)
    document_provenance: List[str] = field(default_factory=list)  # Headers/footers removidos
    pages_ok: int = 0
    pages_suspeita: int = 0
    pages_sem_texto: int = 0
    extraction_time: datetime = field(default_factory=datetime.now)
    # OCR auto-retry statistics
    ocr_attempted: int = 0
    ocr_recovered: int = 0
    ocr_failed: int = 0

    def to_dict(self) -> Dict:
        # Calculate final status counts (after OCR)
        pages_ok_final = sum(1 for p in self.pages if p.status_final == "OK")
        pages_reparada = sum(1 for p in self.pages if p.status_final == "REPARADA")
        pages_suspeita_final = sum(1 for p in self.pages if p.status_final == "SUSPEITA")
        pages_sem_texto_final = sum(1 for p in self.pages if p.status_final == "SEM_TEXTO")
        pages_visual_only = sum(1 for p in self.pages if p.status_final == "VISUAL_ONLY")

        return {
            "filename": self.filename,
            "total_pages": self.total_pages,
            "pages": [p.to_dict() for p in self.pages],
            "document_provenance": self.document_provenance,
            # Status inicial (before OCR)
            "pages_ok_inicial": self.pages_ok,
            "pages_suspeita_inicial": self.pages_suspeita,
            "pages_sem_texto_inicial": self.pages_sem_texto,
            # Status final (after OCR)
            "pages_ok_final": pages_ok_final,
            "pages_reparada": pages_reparada,
            "pages_suspeita_final": pages_suspeita_final,
            "pages_sem_texto_final": pages_sem_texto_final,
            "pages_visual_only": pages_visual_only,
            # Summary (backward compat)
            "pages_ok": pages_ok_final + pages_reparada,  # Readable pages
            "pages_suspeita": pages_suspeita_final,
            "pages_sem_texto": pages_sem_texto_final,
            "extraction_time": self.extraction_time.isoformat(),
            # OCR auto-retry statistics
            "ocr_attempted": self.ocr_attempted,
            "ocr_recovered": self.ocr_recovered,
            "ocr_failed": self.ocr_failed,
        }

    def get_problematic_pages(self) -> List[PageRecord]:
        """Retorna páginas com status SUSPEITA ou SEM_TEXTO."""
        return [p for p in self.pages if p.status_final in ["SUSPEITA", "SEM_TEXTO"]]

    def has_unresolved_pages(self) -> bool:
        """Verifica se há páginas problemáticas não resolvidas."""
        return len(self.get_problematic_pages()) > 0


# ============================================================================
# PDF SAFE LOADER
# ============================================================================

class PDFSafeLoader:
    """
    Carregador PDF Seguro - extração página-a-página com controlo total.
    """

    def __init__(self, dpi: int = 200, llm_client=None):
        self.dpi = dpi
        self._llm_client = llm_client
        self._vision_ocr_available = llm_client is not None
        self._tesseract_available = self._check_tesseract()
        if not self._tesseract_available and self._vision_ocr_available:
            logger.info("Vision OCR disponível como fallback (via LLM)")

    def _check_tesseract(self) -> bool:
        """Verifica se Tesseract OCR está disponível."""
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            logger.info("Tesseract OCR disponível")
            return True
        except Exception:
            logger.info("Tesseract OCR não disponível - OCR fallback desativado")
            return False

    def load_pdf_pages(
        self,
        pdf_bytes: bytes,
        filename: str,
        out_dir: Path
    ) -> PDFSafeResult:
        """
        Carrega PDF página-a-página com controlo total.

        Args:
            pdf_bytes: Bytes do PDF
            filename: Nome do ficheiro
            out_dir: Diretório de output para páginas

        Returns:
            PDFSafeResult com todas as páginas e métricas
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError("PyMuPDF não instalado. Execute: pip install pymupdf")

        # Criar diretório de páginas
        pages_dir = out_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        # Abrir PDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            total_pages = len(doc)

            logger.info(f"PDF Seguro: {filename} - {total_pages} páginas")

            # Extrair todas as páginas
            pages: List[PageRecord] = []
            all_first_lines: List[str] = []
            all_last_lines: List[str] = []

            for page_num in range(total_pages):
                page = doc[page_num]
                page_record = self._extract_page(page, page_num + 1, pages_dir)
                pages.append(page_record)

                # Recolher linhas para deteção de headers/footers
                lines = page_record.text_raw.split('\n')
                if lines:
                    all_first_lines.extend(lines[:5])
                    all_last_lines.extend(lines[-5:] if len(lines) >= 5 else lines)
        finally:
            doc.close()

        # Detetar e remover headers/footers
        provenance, pages = self._clean_headers_footers(pages, all_first_lines, all_last_lines)

        # Calcular métricas finais e detetar sinais
        for page in pages:
            self._detect_intra_page_signals(page)
            self._update_page_status(page)

        # AUTO-RETRY OCR para páginas problemáticas
        ocr_attempted_count = 0
        ocr_recovered_count = 0
        vision_pending_count = 0
        for page in pages:
            if page.status_inicial in ["SEM_TEXTO", "SUSPEITA"]:
                if self._tesseract_available:
                    self._auto_retry_ocr(page, pages_dir)
                    if page.ocr_attempted:
                        ocr_attempted_count += 1
                        if page.ocr_success:
                            ocr_recovered_count += 1
                            self._detect_intra_page_signals(page)
                else:
                    # Sem Tesseract: marcar para análise visual pelos extratores do pipeline
                    # Cada extrator (E1-E5) receberá a imagem e extrairá de forma independente
                    placeholder = (
                        f"[PÁGINA {page.page_num} - DOCUMENTO DIGITALIZADO - "
                        f"IMAGEM ANEXA PARA ANÁLISE VISUAL PELOS EXTRATORES]"
                    )
                    page.text_clean = placeholder
                    page.text_raw = placeholder
                    page.status_final = "VISUAL_PENDING"
                    if "VISUAL_PENDING_PIPELINE" not in page.flags:
                        page.flags.append("VISUAL_PENDING_PIPELINE")
                    vision_pending_count += 1
                    logger.info(
                        f"Página {page.page_num}: marcada para análise visual pelo pipeline "
                        f"(imagem: {page.image_path})"
                    )

        if ocr_attempted_count > 0:
            logger.info(f"Auto-retry OCR: {ocr_attempted_count} tentativas, {ocr_recovered_count} recuperadas")
        if vision_pending_count > 0:
            logger.info(
                f"📸 {vision_pending_count} página(s) marcada(s) para análise visual "
                f"pelos extratores do pipeline (TODOS os extratores lerão as imagens)"
            )

        # Criar resultado com estatísticas finais
        # Contar status FINAL (após OCR)
        pages_ok_final = sum(1 for p in pages if p.status_final == "OK")
        pages_reparada = sum(1 for p in pages if p.status_final == "REPARADA")
        pages_suspeita_final = sum(1 for p in pages if p.status_final == "SUSPEITA")
        pages_sem_texto_final = sum(1 for p in pages if p.status_final == "SEM_TEXTO")

        result = PDFSafeResult(
            filename=filename,
            total_pages=total_pages,
            pages=pages,
            document_provenance=provenance,
            # Status inicial (antes do OCR)
            pages_ok=sum(1 for p in pages if p.status_inicial == "OK"),
            pages_suspeita=sum(1 for p in pages if p.status_inicial == "SUSPEITA"),
            pages_sem_texto=sum(1 for p in pages if p.status_inicial == "SEM_TEXTO"),
            # OCR statistics
            ocr_attempted=ocr_attempted_count,
            ocr_recovered=ocr_recovered_count,
            ocr_failed=ocr_attempted_count - ocr_recovered_count,
        )

        # Guardar manifest
        self._save_manifest(result, out_dir)

        logger.info(f"PDF Seguro concluído: {result.pages_ok} OK, {result.pages_suspeita} SUSPEITA, {result.pages_sem_texto} SEM_TEXTO")

        return result

    def _extract_page(self, page, page_num: int, pages_dir: Path) -> PageRecord:
        """Extrai uma página individual."""
        import fitz

        # Extrair texto raw
        text_raw = page.get_text("text") or ""

        # Verificar se tem imagens
        has_images = len(page.get_images()) > 0

        # Renderizar imagem da página
        image_filename = f"page_{page_num:03d}.png"
        image_path = pages_dir / image_filename

        try:
            pix = page.get_pixmap(dpi=self.dpi)
            pix.save(str(image_path))
        except Exception as e:
            logger.warning(f"Erro ao renderizar página {page_num}: {e}")
            image_path = Path("")

        # Guardar texto raw
        text_raw_path = pages_dir / f"page_{page_num:03d}_text_raw.txt"
        with open(text_raw_path, 'w', encoding='utf-8') as f:
            f.write(text_raw)

        # Calcular métricas básicas
        chars_raw = len(text_raw)
        line_count = text_raw.count('\n') + 1 if text_raw else 0

        # Calcular noise ratio (proporção de caracteres não alfanuméricos)
        if chars_raw > 0:
            alnum_count = sum(1 for c in text_raw if c.isalnum() or c.isspace())
            noise_ratio = 1.0 - (alnum_count / chars_raw)
        else:
            noise_ratio = 1.0

        metrics = PageMetrics(
            chars_raw=chars_raw,
            chars_clean=chars_raw,  # Será atualizado após limpeza
            noise_ratio=noise_ratio,
            has_images=has_images,
            line_count=line_count,
        )

        # Determinar status inicial (has_images é apenas informativo, não condena)
        if chars_raw < 20:
            status = "SEM_TEXTO"
        elif chars_raw < 200 or noise_ratio > 0.25:
            status = "SUSPEITA"
        else:
            status = "OK"

        # Criar flags iniciais (has_images é informativo)
        initial_flags = []
        if has_images:
            initial_flags.append("HAS_IMAGES")

        return PageRecord(
            page_num=page_num,
            text_raw=text_raw,
            text_clean=text_raw,  # Será atualizado após limpeza
            image_path=str(image_path),
            metrics=metrics,
            status_inicial=status,
            status_final=status,
            flags=initial_flags,
        )

    def _clean_headers_footers(
        self,
        pages: List[PageRecord],
        first_lines: List[str],
        last_lines: List[str]
    ) -> Tuple[List[str], List[PageRecord]]:
        """
        Deteta e remove headers/footers repetidos.
        Conservador: só remove linhas que aparecem em >30% das páginas.
        """
        provenance = []
        total_pages = len(pages)
        threshold = 0.3

        # Contar frequência das linhas
        all_lines = first_lines + last_lines
        line_counts = Counter(line.strip() for line in all_lines if line.strip())

        # Identificar linhas a remover (repetidas e curtas/médias)
        lines_to_remove = set()
        for line, count in line_counts.items():
            frequency = count / total_pages
            # Remover se aparece em >30% das páginas e tem <100 chars
            if frequency > threshold and len(line) < 100:
                lines_to_remove.add(line)
                provenance.append(f"[freq={frequency:.1%}] {line[:80]}")

        if lines_to_remove:
            logger.info(f"Headers/footers detetados: {len(lines_to_remove)} linhas")

        # Aplicar limpeza a cada página
        for page in pages:
            cleaned_lines = []
            for line in page.text_raw.split('\n'):
                if line.strip() not in lines_to_remove:
                    cleaned_lines.append(line)

            page.text_clean = '\n'.join(cleaned_lines)
            page.metrics.chars_clean = len(page.text_clean)

        # Guardar texto limpo
        for page in pages:
            pages_dir = Path(page.image_path).parent if page.image_path else None
            if pages_dir:
                text_clean_path = pages_dir / f"page_{page.page_num:03d}_text_clean.txt"
                with open(text_clean_path, 'w', encoding='utf-8') as f:
                    f.write(page.text_clean)

        return provenance, pages

    def _detect_intra_page_signals(self, page: PageRecord):
        """Deteta sinais (datas, valores, artigos) no texto da página."""
        import hashlib
        text = page.text_clean

        # SHA-256 hash da página para auditoria
        page.metrics.page_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        # Detetar datas
        dates = REGEX_DATAS_PT.findall(text)
        page.metrics.dates_detected = [d[0] or d[1] for d in dates if d[0] or d[1]]

        # Detetar valores
        values = REGEX_VALORES_EURO.findall(text)
        page.metrics.values_detected = values

        # Detetar referências legais
        legal_refs = REGEX_ARTIGOS_PT.findall(text)
        page.metrics.legal_refs_detected = legal_refs

    def _update_page_status(self, page: PageRecord):
        """Atualiza status da página após análise completa."""
        # Re-avaliar com base em chars_clean (após limpeza de headers/footers)
        if page.metrics.chars_clean < 20:
            page.status_inicial = "SEM_TEXTO"
            page.status_final = "SEM_TEXTO"
        elif page.metrics.chars_clean < 200 or page.metrics.noise_ratio > 0.25:
            page.status_inicial = "SUSPEITA"
            page.status_final = "SUSPEITA"
        else:
            # CORREÇÃO: Resetar para OK se passou nos thresholds
            # (mesmo que tenha sido marcada SUSPEITA inicialmente por ter imagens)
            page.status_inicial = "OK"
            page.status_final = "OK"

    def _save_manifest(self, result: PDFSafeResult, out_dir: Path):
        """Guarda o manifest com todas as páginas."""
        manifest_path = out_dir / "pages_manifest.json"
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"Manifest guardado: {manifest_path}")

    def ocr_page(self, image_path: str, lang: str = "por") -> str:
        """
        Aplica OCR a uma página (se Tesseract disponível).

        Args:
            image_path: Caminho para a imagem da página
            lang: Idioma para OCR (por=português)

        Returns:
            Texto extraído por OCR ou string vazia se não disponível
        """
        if not self._tesseract_available:
            logger.warning("OCR não disponível - Tesseract não instalado")
            return ""

        try:
            import pytesseract
            from PIL import Image

            img = Image.open(image_path)
            text = pytesseract.image_to_string(img, lang=lang)
            logger.info(f"OCR aplicado: {len(text)} caracteres extraídos")
            return text
        except Exception as e:
            logger.error(f"Erro no OCR: {e}")
            return ""

    def _auto_retry_ocr(self, page: PageRecord, pages_dir: Path) -> PageRecord:
        """
        Tenta recuperar texto via OCR para páginas problemáticas.

        Só executa se:
        - Tesseract disponível
        - status_inicial é SEM_TEXTO, SUSPEITA, ou VISUAL_ONLY
        - Imagem existe

        Args:
            page: PageRecord a processar
            pages_dir: Diretório das páginas

        Returns:
            PageRecord atualizado (mesmo objeto, mutado)
        """
        # Verificar se precisa de OCR
        problematic_statuses = ["SEM_TEXTO", "SUSPEITA", "VISUAL_ONLY"]
        if page.status_inicial not in problematic_statuses:
            return page

        # Verificar se Tesseract está disponível
        if not self._tesseract_available:
            logger.debug(f"Página {page.page_num}: OCR não disponível (Tesseract não instalado)")
            return page

        # Verificar se imagem existe
        if not page.image_path or not Path(page.image_path).exists():
            logger.warning(f"Página {page.page_num}: imagem não encontrada para OCR")
            return page

        # Guardar status antes do OCR
        page.status_before_ocr = page.status_inicial
        page.ocr_attempted = True

        logger.info(f"Página {page.page_num}: tentando OCR (status atual: {page.status_inicial})")

        try:
            # Aplicar OCR
            ocr_text = self.ocr_page(page.image_path, lang="por")
            page.ocr_chars = len(ocr_text)

            # Avaliar resultado do OCR
            ocr_chars_clean = len(ocr_text.strip())

            if ocr_chars_clean >= 50:
                # OCR produziu texto útil
                page.ocr_success = True

                # Atualizar texto
                page.text_raw = ocr_text
                page.text_clean = ocr_text  # Será limpo depois

                # Atualizar métricas
                page.metrics.chars_raw = len(ocr_text)
                page.metrics.chars_clean = len(ocr_text)
                page.metrics.line_count = ocr_text.count('\n') + 1

                # Recalcular noise ratio
                if len(ocr_text) > 0:
                    alnum_count = sum(1 for c in ocr_text if c.isalnum() or c.isspace())
                    page.metrics.noise_ratio = 1.0 - (alnum_count / len(ocr_text))

                # Determinar novo status
                if page.metrics.chars_clean >= 200 and page.metrics.noise_ratio <= 0.25:
                    page.status_final = "OK"
                    page.status_after_ocr = "OK"
                    logger.info(f"Página {page.page_num}: OCR recuperou texto com sucesso → OK")
                else:
                    page.status_final = "REPARADA"
                    page.status_after_ocr = "REPARADA"
                    logger.info(f"Página {page.page_num}: OCR recuperou texto parcial → REPARADA")

                # Guardar texto OCR
                ocr_text_path = pages_dir / f"page_{page.page_num:03d}_ocr.txt"
                with open(ocr_text_path, 'w', encoding='utf-8') as f:
                    f.write(ocr_text)

                # Adicionar flag
                if "OCR_RECOVERED" not in page.flags:
                    page.flags.append("OCR_RECOVERED")

            else:
                # OCR não produziu texto útil
                page.ocr_success = False
                page.status_after_ocr = page.status_inicial  # Mantém status original
                logger.info(f"Página {page.page_num}: OCR não recuperou texto útil ({ocr_chars_clean} chars)")

                # Adicionar flag
                if "OCR_FAILED" not in page.flags:
                    page.flags.append("OCR_FAILED")

        except Exception as e:
            logger.error(f"Página {page.page_num}: erro no OCR: {e}")
            page.ocr_success = False
            page.status_after_ocr = page.status_inicial

            if "OCR_ERROR" not in page.flags:
                page.flags.append("OCR_ERROR")

        return page

    def _auto_retry_vision_ocr(self, page: PageRecord, pages_dir: Path) -> PageRecord:
        """
        Tenta recuperar texto via Vision OCR (LLM com visão) para páginas problemáticas.

        Usado como fallback quando Tesseract não está disponível.
        Envia a imagem PNG da página ao LLM e pede transcrição do texto.
        """
        from src.config import VISION_OCR_MODEL, VISION_OCR_MAX_TOKENS, VISION_OCR_TEMPERATURE

        problematic_statuses = ["SEM_TEXTO", "SUSPEITA", "VISUAL_ONLY"]
        if page.status_inicial not in problematic_statuses:
            return page

        if not self._vision_ocr_available or self._llm_client is None:
            logger.debug(f"Página {page.page_num}: Vision OCR não disponível")
            return page

        if not page.image_path or not Path(page.image_path).exists():
            logger.warning(f"Página {page.page_num}: imagem não encontrada para Vision OCR")
            return page

        page.status_before_ocr = page.status_inicial
        page.ocr_attempted = True

        logger.info(f"Página {page.page_num}: tentando Vision OCR (status atual: {page.status_inicial})")

        try:
            prompt = (
                "Extraia TODO o texto visível nesta imagem de documento digitalizado. "
                "Mantenha a formatação original o mais possível. "
                "Inclua todos os elementos: datas, valores monetários, nomes, moradas, "
                "referências legais (artigos, decretos-lei, leis), números de processo, "
                "assinaturas legíveis, carimbos, cabeçalhos e rodapés. "
                "Transcreva fielmente sem interpretar ou resumir o conteúdo. "
                "Se houver tabelas, reproduza-as em formato de texto. "
                "Responda APENAS com o texto extraído, sem comentários adicionais."
            )

            response = self._llm_client.chat_vision(
                model=VISION_OCR_MODEL,
                prompt=prompt,
                image_path=page.image_path,
                max_tokens=VISION_OCR_MAX_TOKENS,
                temperature=VISION_OCR_TEMPERATURE,
            )

            if not response.success:
                logger.warning(f"Página {page.page_num}: Vision OCR falhou: {response.error}")
                page.ocr_success = False
                page.status_after_ocr = page.status_inicial
                if "VISION_OCR_FAILED" not in page.flags:
                    page.flags.append("VISION_OCR_FAILED")
                return page

            ocr_text = response.content.strip()
            page.ocr_chars = len(ocr_text)
            ocr_chars_clean = len(ocr_text)

            if ocr_chars_clean >= 50:
                page.ocr_success = True
                page.text_raw = ocr_text
                page.text_clean = ocr_text
                page.metrics.chars_raw = len(ocr_text)
                page.metrics.chars_clean = len(ocr_text)
                page.metrics.line_count = ocr_text.count('\n') + 1

                if len(ocr_text) > 0:
                    alnum_count = sum(1 for c in ocr_text if c.isalnum() or c.isspace())
                    page.metrics.noise_ratio = 1.0 - (alnum_count / len(ocr_text))

                if page.metrics.chars_clean >= 200 and page.metrics.noise_ratio <= 0.25:
                    page.status_final = "OK"
                    page.status_after_ocr = "OK"
                    logger.info(f"Página {page.page_num}: Vision OCR recuperou texto com sucesso → OK ({ocr_chars_clean} chars)")
                else:
                    page.status_final = "REPARADA"
                    page.status_after_ocr = "REPARADA"
                    logger.info(f"Página {page.page_num}: Vision OCR recuperou texto parcial → REPARADA ({ocr_chars_clean} chars)")

                # Guardar texto Vision OCR
                ocr_text_path = pages_dir / f"page_{page.page_num:03d}_vision_ocr.txt"
                with open(ocr_text_path, 'w', encoding='utf-8') as f:
                    f.write(ocr_text)

                if "VISION_OCR_RECOVERED" not in page.flags:
                    page.flags.append("VISION_OCR_RECOVERED")
            else:
                page.ocr_success = False
                page.status_after_ocr = page.status_inicial
                logger.info(f"Página {page.page_num}: Vision OCR não recuperou texto útil ({ocr_chars_clean} chars)")
                if "VISION_OCR_FAILED" not in page.flags:
                    page.flags.append("VISION_OCR_FAILED")

        except Exception as e:
            logger.error(f"Página {page.page_num}: erro no Vision OCR: {e}")
            page.ocr_success = False
            page.status_after_ocr = page.status_inicial
            if "VISION_OCR_ERROR" not in page.flags:
                page.flags.append("VISION_OCR_ERROR")

        return page


# ============================================================================
# BATCHING AUTOMÁTICO
# ============================================================================

def batch_pages(pages: List[PageRecord], max_chars: int = 50000) -> List[List[Dict]]:
    """
    Divide páginas em lotes por limite de caracteres.

    Cada lote inclui contexto de páginas adjacentes para resolver
    páginas de continuação.

    Args:
        pages: Lista de PageRecord
        max_chars: Máximo de caracteres por lote

    Returns:
        Lista de lotes, cada lote é lista de dicts com page_num, text, prev_tail, next_head
    """
    batches = []
    current_batch = []
    current_chars = 0

    for i, page in enumerate(pages):
        # Usar texto final (override se existir, senão text_clean)
        text = page.override_text if page.override_text else page.text_clean

        # Obter contexto de páginas adjacentes
        prev_tail = ""
        next_head = ""

        # Sempre incluir contexto de páginas adjacentes (overlap 10%)
        if i > 0:
            prev_text = pages[i-1].override_text or pages[i-1].text_clean
            prev_tail = prev_text[-300:] if len(prev_text) > 300 else prev_text
        if i < len(pages) - 1:
            next_text = pages[i+1].override_text or pages[i+1].text_clean
            next_head = next_text[:300] if len(next_text) > 300 else next_text

        page_entry = {
            "page_num": page.page_num,
            "text": text,
            "prev_tail": prev_tail,
            "next_head": next_head,
            "status": page.status_final,
        }

        entry_chars = len(text) + len(prev_tail) + len(next_head)

        # Verificar se cabe no lote atual
        if current_chars + entry_chars > max_chars and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_chars = 0

        current_batch.append(page_entry)
        current_chars += entry_chars

    # Adicionar último lote
    if current_batch:
        batches.append(current_batch)

    logger.info(f"Páginas divididas em {len(batches)} lotes")
    return batches


# ============================================================================
# MATRIZ DE COBERTURA
# ============================================================================

@dataclass
class CoverageMatrix:
    """Matriz de cobertura de páginas pelos extratores."""
    pages: Dict[int, Dict] = field(default_factory=dict)  # page_num -> info

    def add_extraction(self, extractor_id: str, page_nums: List[int]):
        """Adiciona cobertura de um extrator."""
        for pn in page_nums:
            if pn not in self.pages:
                self.pages[pn] = {"covered_by": {}, "status": "NAO_COBERTA"}
            self.pages[pn]["covered_by"][extractor_id] = True

    def add_unreadable(self, extractor_id: str, page_num: int, reason: str):
        """Marca página como ilegível por um extrator."""
        if page_num not in self.pages:
            self.pages[page_num] = {"covered_by": {}, "status": "NAO_COBERTA", "unreadable_reasons": {}}
        if "unreadable_reasons" not in self.pages[page_num]:
            self.pages[page_num]["unreadable_reasons"] = {}
        self.pages[page_num]["unreadable_reasons"][extractor_id] = reason

    def finalize(self, total_pages: int):
        """Calcula status final de cada página."""
        for pn in range(1, total_pages + 1):
            if pn not in self.pages:
                self.pages[pn] = {"covered_by": {}, "status": "NAO_COBERTA"}

            covered_count = sum(1 for v in self.pages[pn]["covered_by"].values() if v)

            if covered_count == 0:
                self.pages[pn]["status"] = "NAO_COBERTA"
            elif covered_count < 3:
                self.pages[pn]["status"] = "PARCIAL"
            else:
                self.pages[pn]["status"] = "COBERTA"

    def to_dict(self) -> Dict:
        return {"pages": self.pages}

    def save(self, out_dir: Path):
        """Guarda a matriz de cobertura."""
        path = out_dir / "coverage_matrix.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"Matriz de cobertura guardada: {path}")


def update_page_coverage(
    pages: List[PageRecord],
    coverage: CoverageMatrix,
    pdf_result: PDFSafeResult
) -> List[PageRecord]:
    """
    Atualiza status das páginas com base na cobertura e flags intra-página.

    Args:
        pages: Lista de PageRecord
        coverage: Matriz de cobertura dos extratores
        pdf_result: Resultado original do PDF Seguro

    Returns:
        Lista de PageRecord atualizada
    """
    for page in pages:
        pn = page.page_num

        # Atualizar cobertura
        if pn in coverage.pages:
            page.covered_by = coverage.pages[pn].get("covered_by", {})
            page.coverage_status = coverage.pages[pn].get("status", "NAO_COBERTA")
        else:
            page.coverage_status = "NAO_COBERTA"

        # Determinar flags de suspeita
        flags = []

        # Flag: NAO_COBERTA ou PARCIAL
        if page.coverage_status in ["NAO_COBERTA", "PARCIAL"]:
            flags.append(f"COBERTURA_{page.coverage_status}")

        # Verificar sinais detetados vs extraídos
        # (Esta verificação seria feita após receber extrações dos LLMs)

        page.flags = flags

        # Atualizar status final
        if page.override_type:
            page.status_final = "REPARADA"
        elif page.status_inicial == "SEM_TEXTO":
            page.status_final = "SEM_TEXTO"
        elif flags or page.status_inicial == "SUSPEITA":
            page.status_final = "SUSPEITA"
        else:
            page.status_final = "OK"

    return pages


# ============================================================================
# OVERRIDE / REPARAÇÃO
# ============================================================================

def save_override(
    out_dir: Path,
    page_num: int,
    override_type: str,
    text: str = "",
    note: str = "",
    original_image: str = ""
) -> Dict:
    """
    Guarda override de uma página.

    Args:
        out_dir: Diretório de output
        page_num: Número da página
        override_type: upload / manual_transcription / visual_only
        text: Texto final (vazio se visual_only)
        note: Nota do utilizador
        original_image: Caminho para imagem original

    Returns:
        Dict com informação do override
    """
    overrides_dir = out_dir / "overrides"
    overrides_dir.mkdir(parents=True, exist_ok=True)

    override_info = {
        "page_num": page_num,
        "override_type": override_type,
        "timestamp": datetime.now().isoformat(),
        "user_note": note,
        "original_page_png": original_image,
        "final_text_used": text,
    }

    # Guardar JSON
    json_path = overrides_dir / f"page_{page_num:03d}_override.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(override_info, f, ensure_ascii=False, indent=2)

    # Guardar texto se existir
    if text and override_type != "visual_only":
        text_path = overrides_dir / f"page_{page_num:03d}_manual.txt"
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(text)

    logger.info(f"Override guardado para página {page_num}: {override_type}")
    return override_info


def load_overrides(out_dir: Path) -> Dict[int, Dict]:
    """
    Carrega todos os overrides de um run.

    Returns:
        Dict de page_num -> override_info
    """
    overrides_dir = out_dir / "overrides"
    if not overrides_dir.exists():
        return {}

    overrides = {}
    for json_file in overrides_dir.glob("page_*_override.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                info = json.load(f)
                overrides[info["page_num"]] = info
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"Override corrompido ignorado: {json_file.name} - {e}")

    return overrides


def apply_overrides(pages: List[PageRecord], overrides: Dict[int, Dict]) -> List[PageRecord]:
    """
    Aplica overrides às páginas.

    Args:
        pages: Lista de PageRecord
        overrides: Dict de page_num -> override_info

    Returns:
        Lista de PageRecord com overrides aplicados
    """
    for page in pages:
        if page.page_num in overrides:
            override = overrides[page.page_num]
            page.override_type = override["override_type"]
            page.override_text = override.get("final_text_used", "")
            page.override_note = override.get("user_note", "")

            if override["override_type"] == "visual_only":
                page.status_final = "VISUAL_ONLY"
            else:
                page.status_final = "REPARADA"

    return pages


# ============================================================================
# EXPORTAÇÃO DE PÁGINAS
# ============================================================================

def export_selected_pages(
    pdf_bytes: bytes,
    page_nums: List[int],
    out_path: Path,
    overrides_dir: Optional[Path] = None
) -> bool:
    """
    Exporta páginas selecionadas para um novo PDF.

    Args:
        pdf_bytes: Bytes do PDF original
        page_nums: Lista de números de página (1-based)
        out_path: Caminho do PDF de output
        overrides_dir: Diretório de overrides (para incluir uploads substitutos)

    Returns:
        True se sucesso
    """
    try:
        import fitz

        # Abrir PDF original
        src_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        # Criar novo PDF
        dst_doc = fitz.open()

        for pn in sorted(page_nums):
            # Verificar se há upload substituto
            if overrides_dir:
                upload_path = overrides_dir / f"page_{pn:03d}_upload.pdf"
                if upload_path.exists():
                    # Inserir página do upload
                    upload_doc = fitz.open(str(upload_path))
                    dst_doc.insert_pdf(upload_doc, from_page=0, to_page=0)
                    upload_doc.close()
                    continue

            # Inserir página original (0-based index)
            if 1 <= pn <= len(src_doc):
                dst_doc.insert_pdf(src_doc, from_page=pn-1, to_page=pn-1)

        # Guardar
        dst_doc.save(str(out_path))
        dst_doc.close()
        src_doc.close()

        logger.info(f"Exportadas {len(page_nums)} páginas para: {out_path}")
        return True

    except Exception as e:
        logger.error(f"Erro ao exportar páginas: {e}")
        return False


# ============================================================================
# DETETOR INTRA-PÁGINA
# ============================================================================

def detetor_intra_pagina(
    pages: List[PageRecord],
    llm_extraction_text: str,
    extractor_id: str = "LLM"
) -> List[Dict]:
    """
    Compara sinais detetados por regex com conteúdo extraído pelo LLM.
    Identifica suspeitas de omissão.

    Args:
        pages: Lista de PageRecord com métricas de deteção
        llm_extraction_text: Texto da extração do LLM
        extractor_id: Identificador do extrator (E1, E2, E3)

    Returns:
        Lista de suspeitas: [{page_num, signal_type, detected, found_in_llm, missing}]
    """
    suspeitas = []
    llm_text_lower = llm_extraction_text.lower()

    for page in pages:
        page_suspeitas = []

        # Verificar datas
        for date_str in page.metrics.dates_detected:
            # Normalizar formato para busca
            date_normalized = date_str.replace("/", "").replace("-", "").replace(".", "")
            # Também tentar formato original
            found = (
                date_str.lower() in llm_text_lower or
                date_normalized in llm_text_lower.replace("/", "").replace("-", "").replace(".", "")
            )
            if not found:
                page_suspeitas.append({
                    "signal_type": "DATA",
                    "detected": date_str,
                    "found_in_llm": False,
                })

        # Verificar valores monetários
        for value_str in page.metrics.values_detected:
            # Normalizar valor para busca
            value_digits = re.sub(r'[^\d]', '', value_str)
            found = (
                value_str.lower() in llm_text_lower or
                (len(value_digits) >= 3 and value_digits in re.sub(r'[^\d]', '', llm_text_lower))
            )
            if not found:
                page_suspeitas.append({
                    "signal_type": "VALOR",
                    "detected": value_str,
                    "found_in_llm": False,
                })

        # Verificar referências legais
        for legal_ref in page.metrics.legal_refs_detected:
            # Normalizar referência
            ref_normalized = legal_ref.lower().replace(".", "").replace(" ", "")
            found = (
                legal_ref.lower() in llm_text_lower or
                ref_normalized in llm_text_lower.replace(".", "").replace(" ", "")
            )
            if not found:
                page_suspeitas.append({
                    "signal_type": "REF_LEGAL",
                    "detected": legal_ref,
                    "found_in_llm": False,
                })

        # Se houver suspeitas, adicionar flag à página
        if page_suspeitas:
            suspeitas.append({
                "page_num": page.page_num,
                "extractor_id": extractor_id,
                "missing_signals": page_suspeitas,
                "total_missing": len(page_suspeitas),
            })

            # Adicionar flags à página
            for s in page_suspeitas:
                flag = f"SUSPEITA_{s['signal_type']}_NAO_EXTRAIDO"
                if flag not in page.flags:
                    page.flags.append(flag)

    return suspeitas


def verificar_cobertura_sinais(
    pages: List[PageRecord],
    extractor_outputs: Dict[str, str]
) -> Dict:
    """
    Verifica cobertura de sinais por todos os extratores.

    Args:
        pages: Lista de PageRecord
        extractor_outputs: Dict de extractor_id -> texto da extração

    Returns:
        Relatório de cobertura de sinais
    """
    report = {
        "total_signals_detected": 0,
        "signals_by_type": {"DATA": 0, "VALOR": 0, "REF_LEGAL": 0},
        "extractor_coverage": {},
        "uncovered_signals": [],
    }

    # Contar sinais totais
    for page in pages:
        report["signals_by_type"]["DATA"] += len(page.metrics.dates_detected)
        report["signals_by_type"]["VALOR"] += len(page.metrics.values_detected)
        report["signals_by_type"]["REF_LEGAL"] += len(page.metrics.legal_refs_detected)

    report["total_signals_detected"] = sum(report["signals_by_type"].values())

    # Verificar cobertura por extrator
    for ext_id, ext_text in extractor_outputs.items():
        suspeitas = detetor_intra_pagina(pages, ext_text, ext_id)
        total_missing = sum(s["total_missing"] for s in suspeitas)
        report["extractor_coverage"][ext_id] = {
            "pages_with_missing": len(suspeitas),
            "total_missing_signals": total_missing,
            "details": suspeitas,
        }

    # Identificar sinais não cobertos por nenhum extrator
    for page in pages:
        page_uncovered = {
            "page_num": page.page_num,
            "uncovered": [],
        }

        # Para cada sinal, verificar se foi coberto por pelo menos 1 extrator
        all_signals = (
            [("DATA", d) for d in page.metrics.dates_detected] +
            [("VALOR", v) for v in page.metrics.values_detected] +
            [("REF_LEGAL", r) for r in page.metrics.legal_refs_detected]
        )

        for signal_type, signal_val in all_signals:
            covered = False
            for ext_id, ext_text in extractor_outputs.items():
                if signal_val.lower() in ext_text.lower():
                    covered = True
                    break
            if not covered:
                page_uncovered["uncovered"].append({
                    "type": signal_type,
                    "value": signal_val,
                })

        if page_uncovered["uncovered"]:
            report["uncovered_signals"].append(page_uncovered)

    return report


# ============================================================================
# INSTÂNCIA GLOBAL
# ============================================================================

_global_pdf_safe_loader: Optional[PDFSafeLoader] = None


def get_pdf_safe_loader(llm_client=None) -> PDFSafeLoader:
    """Retorna o carregador PDF Seguro global, com Vision OCR se llm_client fornecido."""
    global _global_pdf_safe_loader
    if _global_pdf_safe_loader is None:
        _global_pdf_safe_loader = PDFSafeLoader(llm_client=llm_client)
    elif llm_client is not None and not _global_pdf_safe_loader._vision_ocr_available:
        # Atualizar cliente se ainda não tinha Vision OCR
        _global_pdf_safe_loader._llm_client = llm_client
        _global_pdf_safe_loader._vision_ocr_available = True
        logger.info("Vision OCR ativado no carregador PDF Seguro existente")
    return _global_pdf_safe_loader

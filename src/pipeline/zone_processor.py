# -*- coding: utf-8 -*-
"""
PROCESSADOR POR ZONAS - Tribunal SaaS
============================================================
Divide documentos grandes em zonas (por páginas ou por chars)
e processa cada zona pelo pipeline completo.

PORQUÊ:
- Documentos >50 páginas excedem a janela de contexto dos LLMs
- "Lost in the Middle": modelos perdem info no meio de textos longos
- Processamento por zonas garante cobertura completa

ARQUITECTURA:
1. Dividir documento em zonas (com overlap entre zonas)
2. Processar cada zona: Extratores → Auditores → Juízes
3. Consolidar: Presidente recebe resumos de TODAS as zonas

INTEGRAÇÃO:
- Chamado pelo processor.py quando doc > ZONE_THRESHOLD
- Usa os mesmos modelos e prompts do pipeline normal
- Resultado final é compatível com PipelineResult

============================================================
"""

import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from pathlib import Path


logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURAÇÃO DE ZONAS
# ============================================================================

# Threshold em chars para activar processamento por zonas
ZONE_THRESHOLD_CHARS = 80_000  # ~50 páginas (1600 chars/pág)

# Tamanho máximo de cada zona em chars
ZONE_MAX_CHARS = 40_000  # ~25 páginas por zona

# Overlap entre zonas em chars (garante que info nas fronteiras não se perde)
ZONE_OVERLAP_CHARS = 2_000  # ~1.25 páginas de overlap

# Máximo de zonas (segurança contra documentos gigantes)
MAX_ZONES = 10


# ============================================================================
# DATACLASSES
# ============================================================================

@dataclass
class DocumentZone:
    """Uma zona de um documento."""
    zone_id: int
    start_char: int
    end_char: int
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    text: str = ""
    num_chars: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "zone_id": self.zone_id,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "num_chars": self.num_chars,
        }


@dataclass
class ZoneResult:
    """Resultado do processamento de uma zona."""
    zone: DocumentZone
    extracao: str = ""        # Markdown da extracção consolidada
    auditoria: str = ""       # Markdown da auditoria consolidada
    parecer: str = ""         # Markdown do parecer dos juízes
    confidence: float = 0.85
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "zone": self.zone.to_dict(),
            "extracao_chars": len(self.extracao),
            "auditoria_chars": len(self.auditoria),
            "parecer_chars": len(self.parecer),
            "confidence": self.confidence,
            "num_errors": len(self.errors),
        }


@dataclass
class ZoneProcessingPlan:
    """Plano de processamento por zonas."""
    total_chars: int
    total_pages: Optional[int] = None
    zones: List[DocumentZone] = field(default_factory=list)
    use_zones: bool = False
    reason: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "total_chars": self.total_chars,
            "total_pages": self.total_pages,
            "num_zones": len(self.zones),
            "use_zones": self.use_zones,
            "reason": self.reason,
            "zones": [z.to_dict() for z in self.zones],
        }


# ============================================================================
# DIVISÃO EM ZONAS
# ============================================================================

def should_use_zones(document_text: str) -> bool:
    """Verifica se o documento deve ser processado por zonas."""
    return len(document_text) > ZONE_THRESHOLD_CHARS


def create_zone_plan(
    document_text: str,
    page_mapper: Optional[Any] = None,
    zone_max_chars: int = ZONE_MAX_CHARS,
    zone_overlap: int = ZONE_OVERLAP_CHARS,
) -> ZoneProcessingPlan:
    """
    Cria plano de divisão em zonas.
    
    Tenta dividir em fronteiras de página se page_mapper disponível.
    Caso contrário, divide por chars com overlap.
    
    Args:
        document_text: Texto completo do documento
        page_mapper: CharToPageMapper (opcional)
        zone_max_chars: Tamanho máximo por zona
        zone_overlap: Overlap entre zonas
    
    Returns:
        ZoneProcessingPlan com as zonas definidas
    """
    total_chars = len(document_text)
    
    plan = ZoneProcessingPlan(
        total_chars=total_chars,
    )
    
    # Documento pequeno — sem zonas
    if total_chars <= ZONE_THRESHOLD_CHARS:
        plan.use_zones = False
        plan.reason = f"Documento pequeno ({total_chars:,} chars < {ZONE_THRESHOLD_CHARS:,} threshold)"
        return plan
    
    plan.use_zones = True
    
    # Tentar divisão por páginas
    if page_mapper is not None and hasattr(page_mapper, 'get_page_boundaries'):
        zones = _split_by_pages(document_text, page_mapper, zone_max_chars, zone_overlap)
        plan.reason = f"Divisão por páginas: {len(zones)} zonas"
    else:
        zones = _split_by_chars(document_text, zone_max_chars, zone_overlap)
        plan.reason = f"Divisão por chars: {len(zones)} zonas"
    
    # Limitar número de zonas
    if len(zones) > MAX_ZONES:
        logger.warning(f"Demasiadas zonas ({len(zones)}), limitando a {MAX_ZONES}")
        # Recalcular com zonas maiores
        new_zone_size = total_chars // MAX_ZONES + zone_overlap
        zones = _split_by_chars(document_text, new_zone_size, zone_overlap)
        plan.reason = f"Divisão ajustada: {len(zones)} zonas (max_zone_chars={new_zone_size:,})"
    
    plan.zones = zones
    
    # Tentar obter páginas para cada zona
    if page_mapper is not None and hasattr(page_mapper, 'get_page'):
        for zone in zones:
            try:
                zone.start_page = page_mapper.get_page(zone.start_char)
                zone.end_page = page_mapper.get_page(zone.end_char - 1)
            except Exception:
                pass
        plan.total_pages = zone.end_page if zones else None
    
    logger.info(
        f"[ZONAS] Plano: {len(zones)} zonas para {total_chars:,} chars "
        f"(max {zone_max_chars:,}/zona, overlap {zone_overlap:,})"
    )
    
    return plan


def _split_by_chars(
    document_text: str,
    zone_max_chars: int,
    zone_overlap: int,
) -> List[DocumentZone]:
    """Divide documento por chars com overlap."""
    total = len(document_text)
    zones = []
    zone_id = 1
    start = 0
    
    while start < total:
        end = min(start + zone_max_chars, total)
        
        # Tentar encontrar um parágrafo perto do fim para cortar limpo
        if end < total:
            # Procurar \n\n dentro dos últimos 500 chars
            search_start = max(end - 500, start)
            last_para = document_text.rfind("\n\n", search_start, end)
            if last_para > start + zone_max_chars // 2:
                end = last_para + 2  # Incluir o \n\n
        
        zone_text = document_text[start:end]
        
        zone = DocumentZone(
            zone_id=zone_id,
            start_char=start,
            end_char=end,
            text=zone_text,
            num_chars=len(zone_text),
        )
        zones.append(zone)
        
        zone_id += 1
        # Avançar com overlap (garantir progresso mínimo)
        new_start = end - zone_overlap
        if new_start <= start:
            # Prevenir loop infinito: avançar pelo menos 1 char
            new_start = start + max(1, zone_max_chars // 2)
        start = new_start

        # Evitar loop infinito
        if start >= total:
            break
    
    return zones


def _split_by_pages(
    document_text: str,
    page_mapper: Any,
    zone_max_chars: int,
    zone_overlap: int,
) -> List[DocumentZone]:
    """
    Divide documento por fronteiras de página.
    Agrupa páginas até atingir zone_max_chars.
    """
    try:
        boundaries = page_mapper.get_page_boundaries()
    except (AttributeError, Exception):
        return _split_by_chars(document_text, zone_max_chars, zone_overlap)
    
    if not boundaries:
        return _split_by_chars(document_text, zone_max_chars, zone_overlap)
    
    zones = []
    zone_id = 1
    zone_start_char = 0
    zone_start_page = 1
    current_chars = 0
    
    for page_num, (page_start, page_end) in enumerate(boundaries, 1):
        page_chars = page_end - page_start
        
        # Se adicionar esta página excede o máximo, fechar zona actual
        if current_chars + page_chars > zone_max_chars and current_chars > 0:
            # Fechar zona com overlap
            overlap_end = min(page_start + zone_overlap, len(document_text))
            
            zone = DocumentZone(
                zone_id=zone_id,
                start_char=zone_start_char,
                end_char=overlap_end,
                start_page=zone_start_page,
                end_page=page_num - 1,
                text=document_text[zone_start_char:overlap_end],
                num_chars=overlap_end - zone_start_char,
            )
            zones.append(zone)
            
            # Nova zona começa com overlap
            zone_id += 1
            zone_start_char = max(page_start - zone_overlap, zone_start_char)
            zone_start_page = page_num
            current_chars = page_chars + min(zone_overlap, page_start - zone_start_char)
        else:
            current_chars += page_chars
    
    # Última zona
    if zone_start_char < len(document_text):
        zone = DocumentZone(
            zone_id=zone_id,
            start_char=zone_start_char,
            end_char=len(document_text),
            start_page=zone_start_page,
            end_page=len(boundaries),
            text=document_text[zone_start_char:],
            num_chars=len(document_text) - zone_start_char,
        )
        zones.append(zone)
    
    return zones


# ============================================================================
# CONSOLIDAÇÃO DE ZONAS
# ============================================================================

def build_zone_summary(zone_results: List[ZoneResult]) -> str:
    """
    Constrói resumo consolidado de todas as zonas para o Presidente.
    
    O Presidente recebe este resumo como input e produz a decisão final.
    """
    parts = []
    parts.append(f"# ANÁLISE POR ZONAS ({len(zone_results)} zonas processadas)\n")
    
    for zr in zone_results:
        zone = zr.zone
        pages_info = ""
        if zone.start_page and zone.end_page:
            pages_info = f" (Páginas {zone.start_page}-{zone.end_page})"
        
        parts.append(f"\n## ZONA {zone.zone_id}{pages_info}")
        parts.append(f"**Chars:** {zone.start_char:,}-{zone.end_char:,} ({zone.num_chars:,} chars)")
        parts.append(f"**Confiança:** {zr.confidence:.0%}")
        
        if zr.errors:
            parts.append(f"**Warnings:** {len(zr.errors)}")
        
        # Extracção resumida
        if zr.extracao:
            parts.append(f"\n### Extracção (Zona {zone.zone_id})")
            # Limitar a 3000 chars por zona para não exceder contexto do Presidente
            extracao_trimmed = zr.extracao[:3000]
            if len(zr.extracao) > 3000:
                extracao_trimmed += f"\n\n[... truncado, {len(zr.extracao):,} chars total]"
            parts.append(extracao_trimmed)
        
        # Auditoria resumida
        if zr.auditoria:
            parts.append(f"\n### Auditoria (Zona {zone.zone_id})")
            auditoria_trimmed = zr.auditoria[:2000]
            if len(zr.auditoria) > 2000:
                auditoria_trimmed += f"\n\n[... truncado, {len(zr.auditoria):,} chars total]"
            parts.append(auditoria_trimmed)
        
        # Parecer resumido
        if zr.parecer:
            parts.append(f"\n### Parecer Jurídico (Zona {zone.zone_id})")
            parecer_trimmed = zr.parecer[:2000]
            if len(zr.parecer) > 2000:
                parecer_trimmed += f"\n\n[... truncado, {len(zr.parecer):,} chars total]"
            parts.append(parecer_trimmed)
        
        parts.append("\n---")
    
    # Sumário global
    avg_confidence = sum(zr.confidence for zr in zone_results) / len(zone_results) if zone_results else 0
    total_errors = sum(len(zr.errors) for zr in zone_results)
    
    parts.append(f"\n## SUMÁRIO GLOBAL")
    parts.append(f"- **Zonas processadas:** {len(zone_results)}")
    parts.append(f"- **Confiança média:** {avg_confidence:.0%}")
    parts.append(f"- **Total warnings:** {total_errors}")
    
    return "\n".join(parts)


def merge_zone_confidences(zone_results: List[ZoneResult]) -> float:
    """
    Calcula confiança final ponderada por tamanho de cada zona.
    Zonas maiores têm mais peso.
    """
    if not zone_results:
        return 0.0
    
    total_weight = sum(zr.zone.num_chars for zr in zone_results)
    if total_weight == 0:
        return 0.0
    
    weighted_sum = sum(
        zr.confidence * zr.zone.num_chars 
        for zr in zone_results
    )
    
    return weighted_sum / total_weight


def merge_zone_errors(zone_results: List[ZoneResult]) -> List[str]:
    """Consolida erros de todas as zonas."""
    all_errors = []
    for zr in zone_results:
        for err in zr.errors:
            prefixed = f"[Zona {zr.zone.zone_id}] {err}"
            all_errors.append(prefixed)
    return all_errors


# ============================================================================
# LOGGING DE ZONAS
# ============================================================================

def log_zone_plan(plan: ZoneProcessingPlan, output_dir: Optional[Path] = None):
    """Grava plano de zonas em ficheiro JSON."""
    import json
    
    if output_dir is None:
        return
    
    filepath = output_dir / "zone_plan.json"
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"[ZONAS] Plano guardado: {filepath}")
    except Exception as e:
        logger.warning(f"[ZONAS] Erro ao guardar plano: {e}")


def log_zone_results(zone_results: List[ZoneResult], output_dir: Optional[Path] = None):
    """Grava resultados por zona em ficheiro JSON."""
    import json
    
    if output_dir is None:
        return
    
    filepath = output_dir / "zone_results.json"
    try:
        data = {
            "num_zones": len(zone_results),
            "overall_confidence": merge_zone_confidences(zone_results),
            "total_errors": sum(len(zr.errors) for zr in zone_results),
            "zones": [zr.to_dict() for zr in zone_results],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[ZONAS] Resultados guardados: {filepath}")
    except Exception as e:
        logger.warning(f"[ZONAS] Erro ao guardar resultados: {e}")


# ============================================================================
# TESTE
# ============================================================================

if __name__ == "__main__":
    print("=== Teste Zone Processor ===\n")
    
    # Criar documento teste
    doc_text = "Página 1. " * 5000  # ~50K chars
    doc_text += "Página 2. " * 5000  # Total ~100K chars
    
    print(f"Documento: {len(doc_text):,} chars")
    print(f"Usar zonas: {should_use_zones(doc_text)}")
    
    plan = create_zone_plan(doc_text)
    print(f"\nPlano: {plan.reason}")
    print(f"Zonas: {len(plan.zones)}")
    
    for zone in plan.zones:
        print(f"  Zona {zone.zone_id}: chars {zone.start_char:,}-{zone.end_char:,} ({zone.num_chars:,} chars)")
    
    # Simular resultados
    zone_results = []
    for zone in plan.zones:
        zr = ZoneResult(
            zone=zone,
            extracao=f"Extracção da zona {zone.zone_id}...",
            auditoria=f"Auditoria da zona {zone.zone_id}...",
            parecer=f"Parecer da zona {zone.zone_id}...",
            confidence=0.85,
        )
        zone_results.append(zr)
    
    summary = build_zone_summary(zone_results)
    print(f"\nResumo consolidado: {len(summary):,} chars")
    print(f"Confiança ponderada: {merge_zone_confidences(zone_results):.0%}")

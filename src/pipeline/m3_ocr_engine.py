# ============================================================================
# Pipeline v4.2 — M3: OCR Multi-Motor via Eden AI
# ============================================================================
# Envia imagens de páginas para Eden AI com múltiplos providers OCR
# (Google Vision, Azure/Microsoft, AWS/Amazon Textract).
# Aplica consenso ao nível de texto entre providers.
# Checkpoint por página na tabela document_pages.
# ============================================================================

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests as http_requests

from src.config import EDEN_AI_API_KEY, V42_OCR_BATCH_SIZE

logger = logging.getLogger(__name__)

EDEN_AI_BASE_URL = "https://api.edenai.run/v3"
EDEN_AI_UPLOAD_URL = f"{EDEN_AI_BASE_URL}/upload"
EDEN_AI_UNIVERSAL_URL = f"{EDEN_AI_BASE_URL}/universal-ai"

# OCR providers in priority order
OCR_PROVIDERS = ["google", "microsoft", "amazon"]
PRIMARY_PROVIDERS = ["google", "microsoft"]
FALLBACK_PROVIDERS = ["amazon"]

# Model patterns for Eden AI
OCR_MODEL_PATTERN = "ocr/ocr/{provider}"
FINANCIAL_MODEL_PATTERN = "ocr/financial_parser/{provider}"


@dataclass
class OCRWord:
    """Palavra individual detectada por OCR."""
    text: str
    confidence: float
    provider: str


@dataclass
class OCRPageResult:
    """Resultado OCR de uma página."""
    page_num: int
    consensus_text: str
    providers_used: list[str]
    provider_texts: dict[str, str]   # {provider: raw_text}
    confidence: float
    word_count: int
    processing_time: float           # seconds
    errors: list[str] = field(default_factory=list)


@dataclass
class OCRDocumentResult:
    """Resultado OCR do documento completo."""
    pages: list[OCRPageResult]
    full_text: str
    total_confidence: float
    total_words: int
    total_processing_time: float
    providers_used: list[str]
    file_ids: dict[int, str]         # {page_num: eden_ai_file_id}


def ocr_document(
    page_images: list,  # list[PageImage] from m2_preprocessing
    analysis_id: str,
    circuit_breaker=None,
    supabase_client=None,
    api_key: Optional[str] = None,
) -> OCRDocumentResult:
    """
    M3: OCR Multi-Motor para todas as páginas do documento.

    Processa páginas em lotes para gerir memória.

    Args:
        page_images: lista de PageImage do M2
        analysis_id: ID da análise (para checkpoints)
        circuit_breaker: CircuitBreaker opcional
        supabase_client: cliente Supabase para checkpoints
        api_key: Eden AI API key (usa config se não fornecida)

    Returns:
        OCRDocumentResult com texto de todas as páginas
    """
    key = api_key or EDEN_AI_API_KEY
    if not key:
        raise ValueError("EDEN_AI_API_KEY não configurada")

    total_pages = len(page_images)
    logger.info(f"[M3] OCR Multi-Motor: {total_pages} páginas, batch_size={V42_OCR_BATCH_SIZE}")

    all_results = []
    all_file_ids = {}
    start_time = time.time()

    # Verificar checkpoints existentes
    completed_pages = set()
    if supabase_client:
        completed_pages = _get_completed_pages(supabase_client, analysis_id)
        if completed_pages:
            logger.info(f"[M3] Retomando: {len(completed_pages)} páginas já processadas")

    # Processar em lotes
    for batch_start in range(0, total_pages, V42_OCR_BATCH_SIZE):
        batch_end = min(batch_start + V42_OCR_BATCH_SIZE, total_pages)
        batch = page_images[batch_start:batch_end]

        logger.info(f"[M3] Lote {batch_start + 1}-{batch_end}/{total_pages}")

        for page_img in batch:
            if page_img.page_num in completed_pages:
                # Carregar resultado do checkpoint
                cached = _load_page_checkpoint(supabase_client, analysis_id, page_img.page_num)
                if cached:
                    all_results.append(cached)
                    continue

            result, file_id = _ocr_single_page(
                page_img, key, circuit_breaker
            )
            all_results.append(result)
            if file_id:
                all_file_ids[page_img.page_num] = file_id

            # Checkpoint
            if supabase_client:
                _save_page_checkpoint(supabase_client, analysis_id, result)

    # Ordenar por número de página
    all_results.sort(key=lambda r: r.page_num)

    # Construir texto completo
    full_text = "\n\n".join(r.consensus_text for r in all_results if r.consensus_text)

    # Estatísticas
    total_time = time.time() - start_time
    providers_set = set()
    total_words = 0
    confidence_sum = 0
    for r in all_results:
        providers_set.update(r.providers_used)
        total_words += r.word_count
        confidence_sum += r.confidence

    avg_confidence = confidence_sum / len(all_results) if all_results else 0

    logger.info(
        f"[M3] OCR concluído: {total_pages} páginas, {total_words} palavras, "
        f"confiança média {avg_confidence:.1%}, {total_time:.1f}s"
    )

    return OCRDocumentResult(
        pages=all_results,
        full_text=full_text,
        total_confidence=avg_confidence,
        total_words=total_words,
        total_processing_time=total_time,
        providers_used=list(providers_set),
        file_ids=all_file_ids,
    )


def _ocr_single_page(
    page_img,
    api_key: str,
    circuit_breaker=None,
) -> tuple[OCRPageResult, Optional[str]]:
    """
    OCR de uma única página com múltiplos providers.

    Returns:
        (OCRPageResult, file_id ou None)
    """
    page_num = page_img.page_num
    start_time = time.time()
    errors = []
    provider_texts = {}
    file_id = None

    # 1. Upload da imagem para Eden AI
    try:
        file_id = _upload_image(page_img.image_bytes, page_num, api_key)
    except Exception as e:
        error_msg = f"Upload falhou página {page_num}: {e}"
        logger.error(f"[M3] {error_msg}")
        return OCRPageResult(
            page_num=page_num,
            consensus_text="",
            providers_used=[],
            provider_texts={},
            confidence=0,
            word_count=0,
            processing_time=time.time() - start_time,
            errors=[error_msg],
        ), None

    # 2. OCR com providers primários
    for provider in PRIMARY_PROVIDERS:
        if circuit_breaker and not circuit_breaker.can_call(f"edenai_{provider}"):
            logger.warning(f"[M3] Circuit breaker OPEN para {provider}, a saltar")
            continue

        try:
            text = _call_ocr_provider(file_id, provider, api_key)
            provider_texts[provider] = text
            if circuit_breaker:
                circuit_breaker.record_success(f"edenai_{provider}")
        except Exception as e:
            errors.append(f"{provider}: {e}")
            if circuit_breaker:
                circuit_breaker.record_failure(f"edenai_{provider}", str(e))
            logger.warning(f"[M3] Provider {provider} falhou para página {page_num}: {e}")

    # 3. Fallback se providers primários falharam ou discordam muito
    if len(provider_texts) < 2:
        for provider in FALLBACK_PROVIDERS:
            if provider in provider_texts:
                continue
            if circuit_breaker and not circuit_breaker.can_call(f"edenai_{provider}"):
                continue
            try:
                text = _call_ocr_provider(file_id, provider, api_key)
                provider_texts[provider] = text
                if circuit_breaker:
                    circuit_breaker.record_success(f"edenai_{provider}")
            except Exception as e:
                errors.append(f"{provider}: {e}")
                if circuit_breaker:
                    circuit_breaker.record_failure(f"edenai_{provider}", str(e))

    # 4. Consenso
    consensus_text, confidence = _build_consensus(provider_texts)
    word_count = len(consensus_text.split()) if consensus_text else 0

    processing_time = time.time() - start_time
    logger.info(
        f"[M3] Página {page_num}: {word_count} palavras, "
        f"{len(provider_texts)} providers, confiança {confidence:.1%}, "
        f"{processing_time:.1f}s"
    )

    return OCRPageResult(
        page_num=page_num,
        consensus_text=consensus_text,
        providers_used=list(provider_texts.keys()),
        provider_texts=provider_texts,
        confidence=confidence,
        word_count=word_count,
        processing_time=processing_time,
        errors=errors,
    ), file_id


def _upload_image(image_bytes: bytes, page_num: int, api_key: str) -> str:
    """Upload imagem para Eden AI, retorna file_id."""
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (f"page_{page_num}.png", image_bytes, "image/png")}

    response = http_requests.post(
        EDEN_AI_UPLOAD_URL,
        headers=headers,
        files=files,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    file_id = data.get("file_id")
    if not file_id:
        raise ValueError(f"Eden AI upload não retornou file_id: {data}")
    return file_id


def _call_ocr_provider(file_id: str, provider: str, api_key: str) -> str:
    """Chamar OCR de um provider específico via Eden AI."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": f"ocr/ocr/{provider}",
        "input": {
            "file": file_id,
            "language": "pt",
        },
    }

    response = http_requests.post(
        EDEN_AI_UNIVERSAL_URL,
        headers=headers,
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()

    text = ""
    output = data.get("output", {})
    if isinstance(output, dict):
        text = output.get("text", "")
    elif isinstance(output, str):
        text = output

    return text.strip()


def _build_consensus(provider_texts: dict[str, str]) -> tuple[str, float]:
    """
    Construir texto consenso a partir de múltiplos providers.

    Estratégia simplificada (sem bounding boxes word-level):
    - Se 2+ providers: usar o texto mais longo (mais completo)
    - Se textos muito similares: confiança alta
    - Se textos diferentes: confiança média

    Returns:
        (consensus_text, confidence)
    """
    if not provider_texts:
        return ("", 0.0)

    if len(provider_texts) == 1:
        # Apenas um provider
        text = list(provider_texts.values())[0]
        return (text, 0.6)

    # Múltiplos providers: comparar textos
    texts = list(provider_texts.values())
    providers = list(provider_texts.keys())

    # Usar o texto mais longo como base (mais completo)
    best_idx = max(range(len(texts)), key=lambda i: len(texts[i]))
    best_text = texts[best_idx]

    # Calcular similaridade entre providers
    similarity = _text_similarity(texts[0], texts[1] if len(texts) > 1 else texts[0])

    if similarity > 0.95:
        confidence = 1.0   # Forte concordância
    elif similarity > 0.80:
        confidence = 0.85   # Boa concordância
    elif similarity > 0.60:
        confidence = 0.70   # Concordância moderada
    else:
        confidence = 0.50   # Pouca concordância

    # Se temos 3 providers e 2 concordam, confiança sobe
    if len(texts) >= 3:
        sim_01 = _text_similarity(texts[0], texts[1])
        sim_02 = _text_similarity(texts[0], texts[2])
        sim_12 = _text_similarity(texts[1], texts[2])
        max_sim = max(sim_01, sim_02, sim_12)
        if max_sim > 0.90:
            confidence = min(confidence + 0.1, 1.0)

    return (best_text, confidence)


def _text_similarity(text_a: str, text_b: str) -> float:
    """
    Calcular similaridade simples entre dois textos.

    Usa razão de caracteres comuns (sequenciais).
    """
    if not text_a or not text_b:
        return 0.0

    # Normalizar
    a = text_a.lower().strip()
    b = text_b.lower().strip()

    if a == b:
        return 1.0

    # Ratio simples baseado em comprimento
    shorter = min(len(a), len(b))
    longer = max(len(a), len(b))

    if longer == 0:
        return 1.0

    # Contar caracteres comuns nas mesmas posições
    matches = sum(1 for i in range(shorter) if a[i] == b[i])
    positional_sim = matches / longer

    # Contar palavras comuns (order-independent)
    words_a = set(a.split())
    words_b = set(b.split())
    if words_a or words_b:
        word_overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)
    else:
        word_overlap = 1.0

    # Média ponderada
    return 0.4 * positional_sim + 0.6 * word_overlap


# ============================================================================
# Checkpointing (Supabase)
# ============================================================================

def _get_completed_pages(supabase_client, analysis_id: str) -> set[int]:
    """Obter páginas já processadas com sucesso."""
    try:
        result = supabase_client.table("document_pages").select(
            "page_num"
        ).eq(
            "analysis_id", analysis_id
        ).eq(
            "ocr_status", "completed"
        ).execute()
        return {row["page_num"] for row in (result.data or [])}
    except Exception as e:
        logger.warning(f"[M3] Erro ao ler checkpoints: {e}")
        return set()


def _save_page_checkpoint(supabase_client, analysis_id: str, result: OCRPageResult) -> None:
    """Guardar checkpoint de página processada."""
    try:
        supabase_client.table("document_pages").upsert({
            "analysis_id": analysis_id,
            "doc_id": analysis_id,
            "page_num": result.page_num,
            "ocr_status": "completed" if result.consensus_text else "failed",
            "ocr_text": result.consensus_text,
            "ocr_providers": result.provider_texts,
            "ocr_confidence": result.confidence,
            "ocr_word_count": result.word_count,
        }, on_conflict="analysis_id,page_num").execute()
    except Exception as e:
        logger.warning(f"[M3] Erro ao guardar checkpoint página {result.page_num}: {e}")


def _load_page_checkpoint(
    supabase_client, analysis_id: str, page_num: int
) -> Optional[OCRPageResult]:
    """Carregar resultado de checkpoint."""
    try:
        result = supabase_client.table("document_pages").select("*").eq(
            "analysis_id", analysis_id
        ).eq(
            "page_num", page_num
        ).eq(
            "ocr_status", "completed"
        ).execute()

        if result.data:
            row = result.data[0]
            return OCRPageResult(
                page_num=row["page_num"],
                consensus_text=row.get("ocr_text", ""),
                providers_used=list((row.get("ocr_providers") or {}).keys()),
                provider_texts=row.get("ocr_providers") or {},
                confidence=row.get("ocr_confidence", 0),
                word_count=row.get("ocr_word_count", 0),
                processing_time=0,
            )
    except Exception as e:
        logger.warning(f"[M3] Erro ao carregar checkpoint página {page_num}: {e}")
    return None

# ============================================================================
# Pipeline v4.2 — M7: Análise Jurídica por Chunk
# ============================================================================
# Envia cada chunk para Claude Sonnet para análise jurídica detalhada.
# Produz output estruturado: questões jurídicas, argumentos, referências.
# ============================================================================

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from src.config import V42_ANALYSIS_MODEL

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM_PROMPT = """Você é um analista jurídico especializado em direito português.
Analise o texto fornecido e extraia TODAS as informações juridicamente relevantes.

RESPONDA em JSON com esta estrutura exacta:
{
  "items": [
    {
      "item_type": "fact|date|amount|legal_ref|entity|table|other",
      "value": "descrição ou valor normalizado do item",
      "raw_text": "texto exacto como aparece no documento",
      "context": "frase ou parágrafo completo onde aparece",
      "confidence": 0.95,
      "category": "facto_relevante|data_chave|valor_monetario|referencia_legal|parte_processual|outro"
    }
  ],
  "legal_issues": [
    {
      "issue": "descrição da questão jurídica",
      "relevant_law": "artigo ou lei aplicável",
      "assessment": "análise breve"
    }
  ],
  "arguments": [
    {
      "argument": "argumento identificado",
      "side": "autor|réu|tribunal",
      "strength": "forte|moderado|fraco"
    }
  ]
}

REGRAS:
1. Extraia FACTOS concretos (datas, nomes, valores, eventos)
2. Identifique TODAS as referências legais (artigos, leis, decretos)
3. Identifique TODAS as partes (autor, réu, testemunhas, peritos)
4. Identifique valores monetários e datas chave
5. Não invente informação — apenas extraia o que está no texto
6. Use português de Portugal (não brasileiro)
7. Confidence: 0.0-1.0 baseada na clareza da informação no texto"""

ANALYSIS_USER_PROMPT = """Área do direito: {area}

Analise este excerto de documento jurídico (chunk {chunk_index}/{total_chunks}, páginas {page_start}-{page_end}):

---
{text}
---

Extraia todas as informações juridicamente relevantes em formato JSON."""


@dataclass
class AnalyzedItem:
    """Item individual extraído da análise."""
    item_type: str
    value: str
    raw_text: str
    context: str
    confidence: float
    category: str = "outro"


@dataclass
class ChunkAnalysis:
    """Resultado da análise jurídica de um chunk."""
    chunk_index: int
    items: list[AnalyzedItem] = field(default_factory=list)
    legal_issues: list[dict] = field(default_factory=list)
    arguments: list[dict] = field(default_factory=list)
    model_used: str = ""
    tokens_used: int = 0
    processing_time: float = 0
    error: Optional[str] = None


def analyze_chunks(
    chunks: list,  # list[SemanticChunk] from m6_chunking
    area_direito: str,
    model: Optional[str] = None,
) -> list[ChunkAnalysis]:
    """
    M7: Análise jurídica de cada chunk.

    Args:
        chunks: chunks semânticos do M6
        area_direito: área do direito (civil, penal, trabalho, etc.)
        model: modelo LLM a usar (default: V42_ANALYSIS_MODEL)

    Returns:
        Lista de ChunkAnalysis
    """
    from src.llm_client import call_llm

    analysis_model = model or V42_ANALYSIS_MODEL
    total_chunks = len(chunks)
    results = []

    logger.info(f"[M7] Análise jurídica: {total_chunks} chunks com {analysis_model}")

    for chunk in chunks:
        start_time = time.time()

        prompt = ANALYSIS_USER_PROMPT.format(
            area=area_direito,
            chunk_index=chunk.chunk_index + 1,
            total_chunks=total_chunks,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            text=chunk.text,
        )

        try:
            response = call_llm(
                model=analysis_model,
                prompt=prompt,
                system_prompt=ANALYSIS_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=8192,
            )

            analysis = _parse_analysis_response(
                response.content, chunk.chunk_index
            )
            analysis.model_used = analysis_model
            analysis.tokens_used = response.tokens_used
            analysis.processing_time = time.time() - start_time

            logger.info(
                f"[M7] Chunk {chunk.chunk_index}: "
                f"{len(analysis.items)} items, "
                f"{len(analysis.legal_issues)} questões jurídicas, "
                f"{analysis.tokens_used} tokens"
            )

        except Exception as e:
            logger.error(f"[M7] Erro no chunk {chunk.chunk_index}: {e}")
            analysis = ChunkAnalysis(
                chunk_index=chunk.chunk_index,
                error=str(e),
                processing_time=time.time() - start_time,
            )

        results.append(analysis)

    total_items = sum(len(a.items) for a in results)
    total_tokens = sum(a.tokens_used for a in results)
    logger.info(f"[M7] Análise concluída: {total_items} items, {total_tokens} tokens")

    return results


def _parse_analysis_response(content: str, chunk_index: int) -> ChunkAnalysis:
    """Parse da resposta JSON do LLM."""
    analysis = ChunkAnalysis(chunk_index=chunk_index)

    # Limpar resposta
    text = content.strip()

    # Remover markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Tentar reparar JSON
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                data = repaired
            else:
                logger.warning(f"[M7] JSON não reparável no chunk {chunk_index}")
                # Fallback: criar item genérico com o texto
                analysis.items.append(AnalyzedItem(
                    item_type="other",
                    value=content[:500],
                    raw_text=content[:200],
                    context="",
                    confidence=0.5,
                ))
                return analysis
        except Exception:
            analysis.items.append(AnalyzedItem(
                item_type="other",
                value=content[:500],
                raw_text=content[:200],
                context="",
                confidence=0.5,
            ))
            return analysis

    # Parse items
    for item_data in data.get("items", []):
        analysis.items.append(AnalyzedItem(
            item_type=item_data.get("item_type", "other"),
            value=item_data.get("value", ""),
            raw_text=item_data.get("raw_text", ""),
            context=item_data.get("context", ""),
            confidence=float(item_data.get("confidence", 0.7)),
            category=item_data.get("category", "outro"),
        ))

    analysis.legal_issues = data.get("legal_issues", [])
    analysis.arguments = data.get("arguments", [])

    return analysis

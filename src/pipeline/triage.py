# -*- coding: utf-8 -*-
"""
FASE 0 — TRIAGEM + NORMALIZAÇÃO (v4.0 Handover)

Classifica o domínio jurídico do documento via 3 IAs baratas em paralelo:
  T1: GPT-4o-mini
  T2: Gemini 3 Flash Preview
  T3: Llama 4 8B

Votação: 2/3 concordam → domínio definido. Empate → "multi-dominio"
Detecção de fotos: ≤20 OK, >20 aviso, >50 modo fila
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from src.config import AREAS_DIREITO

logger = logging.getLogger(__name__)

# Modelos de triagem (baratos e rápidos)
TRIAGE_MODELS = [
    {"id": "T1", "model": "openai/gpt-4o-mini", "label": "GPT-4o-mini"},
    {"id": "T2", "model": "google/gemini-3-flash-preview", "label": "Gemini Flash"},
    {"id": "T3", "model": "meta-llama/llama-3.1-8b-instruct", "label": "Llama 3.1 8B"},
]

TRIAGE_SYSTEM_PROMPT = """You are a legal document classifier. Given a snippet of a document, determine:
1. The primary legal domain (choose ONE from the list below)
2. Up to 5 keywords that summarize the document's subject matter
3. Estimated number of photos/images mentioned or described

LEGAL DOMAINS (choose exactly one):
- Civil
- Penal
- Trabalho
- Família
- Administrativo
- Constitucional
- Comercial
- Tributário
- Ambiental
- Consumidor
- Multi-domínio (if multiple equally apply)

OUTPUT FORMAT (JSON only):
{"domain": "Civil", "keywords": ["contrato", "arrendamento", "renda"], "photo_count_estimate": 0, "confidence": 0.9}

Respond with ONLY the JSON. No text before or after."""


@dataclass
class TriageResult:
    """Resultado da Fase 0 — Triagem."""
    domain: str = "Civil"  # Default
    domain_confidence: float = 0.0
    keywords: List[str] = field(default_factory=list)
    photo_estimate: int = 0
    photo_warning: str = ""  # "", "warning", "queue_mode"
    votes: Dict[str, str] = field(default_factory=dict)  # {T1: "Civil", T2: "Civil", T3: "Penal"}
    consensus: str = "none"  # "unanimous", "majority", "split"
    duration_ms: float = 0.0
    cost_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "domain_confidence": self.domain_confidence,
            "keywords": self.keywords,
            "photo_estimate": self.photo_estimate,
            "photo_warning": self.photo_warning,
            "votes": self.votes,
            "consensus": self.consensus,
            "duration_ms": self.duration_ms,
            "cost_usd": round(self.cost_usd, 4),
        }


class TriageProcessor:
    """
    Fase 0 — Triagem de documentos.

    Classifica domínio jurídico via 3 IAs baratas em paralelo.
    Injecta marcadores [Pág_X] no texto.
    Deteta presença excessiva de fotos/imagens.
    """

    def __init__(self, llm_client, cost_controller=None):
        """
        Args:
            llm_client: OpenRouterClient para chamadas LLM
            cost_controller: CostController opcional para rastrear custos
        """
        self.llm_client = llm_client
        self.cost_controller = cost_controller

    def run(self, text: str, filename: str = "", num_pages: int = 0) -> TriageResult:
        """
        Executa a triagem do documento.

        Args:
            text: Texto completo do documento
            filename: Nome do ficheiro (para contexto)
            num_pages: Número de páginas (se conhecido)

        Returns:
            TriageResult com domínio, keywords e alertas
        """
        start_time = time.time()
        result = TriageResult()

        # Criar snippet para classificação (primeiros ~3000 chars + últimos ~1000)
        snippet = self._create_snippet(text, filename, num_pages)

        # Classificar em paralelo com 3 IAs
        votes = {}
        all_keywords = []
        photo_estimates = []

        def _classify(triage_model):
            tid = triage_model["id"]
            model = triage_model["model"]
            try:
                response = self.llm_client.chat_simple(
                    model=model,
                    prompt=snippet,
                    system_prompt=TRIAGE_SYSTEM_PROMPT,
                    temperature=0.0,
                    max_tokens=256,
                )

                # Registar custos
                if self.cost_controller:
                    pt = response.prompt_tokens or (len(snippet) // 4)
                    ct = response.completion_tokens or (len(response.content) // 4)
                    try:
                        self.cost_controller.register_usage(
                            phase=f"triage_{tid}",
                            model=model,
                            prompt_tokens=pt,
                            completion_tokens=ct,
                        )
                    except Exception as e:
                        logger.warning(f"[TRIAGE] Falha ao registar custo {tid}: {e}")

                # Parse JSON response
                import json
                from src.pipeline.extractor_json import extract_json_from_text
                parsed = extract_json_from_text(response.content)
                if parsed:
                    domain = parsed.get("domain", "Civil")
                    kws = parsed.get("keywords", [])
                    photos = parsed.get("photo_count_estimate", 0)
                    return tid, domain, kws, photos
                else:
                    logger.warning(f"[TRIAGE] {tid} ({model}): JSON parse failed")
                    return tid, "Civil", [], 0

            except Exception as e:
                logger.error(f"[TRIAGE] {tid} ({model}) failed: {e}")
                return tid, "Civil", [], 0

        # Executar em paralelo
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_classify, m): m["id"] for m in TRIAGE_MODELS}
            for future in as_completed(futures):
                tid = futures[future]
                try:
                    tid_result, domain, kws, photos = future.result()
                    # Normalizar domínio
                    domain = self._normalize_domain(domain)
                    votes[tid_result] = domain
                    all_keywords.extend(kws)
                    photo_estimates.append(photos)
                    logger.info(f"[TRIAGE] {tid_result}: domain={domain}, keywords={kws[:3]}")
                except Exception as e:
                    logger.error(f"[TRIAGE] {tid} exception: {e}")
                    votes[tid] = "Civil"

        # Votação por maioria
        result.votes = votes
        domain_counts: Dict[str, int] = {}
        for d in votes.values():
            domain_counts[d] = domain_counts.get(d, 0) + 1

        # Determinar consenso
        if not domain_counts:
            result.domain = "Civil"
            result.consensus = "none"
            result.domain_confidence = 0.0
        else:
            max_count = max(domain_counts.values())
            winners = [d for d, c in domain_counts.items() if c == max_count]

            if max_count == 3:
                result.domain = winners[0]
                result.consensus = "unanimous"
                result.domain_confidence = 1.0
            elif max_count == 2:
                result.domain = winners[0]
                result.consensus = "majority"
                result.domain_confidence = 0.75
            else:
                result.domain = "Multi-domínio"
                result.consensus = "split"
                result.domain_confidence = 0.33

        # Keywords deduplicadas
        seen = set()
        unique_kws = []
        for kw in all_keywords:
            kw_lower = kw.lower().strip()
            if kw_lower not in seen and kw_lower:
                seen.add(kw_lower)
                unique_kws.append(kw)
        result.keywords = unique_kws[:10]

        # Photo estimation
        if photo_estimates:
            result.photo_estimate = max(photo_estimates)
            if result.photo_estimate > 50:
                result.photo_warning = "queue_mode"
            elif result.photo_estimate > 20:
                result.photo_warning = "warning"

        result.duration_ms = (time.time() - start_time) * 1000

        logger.info(
            f"[TRIAGE] Resultado: domain={result.domain} ({result.consensus}), "
            f"keywords={result.keywords[:5]}, photos={result.photo_estimate}, "
            f"duration={result.duration_ms:.0f}ms"
        )

        return result

    def _create_snippet(self, text: str, filename: str, num_pages: int) -> str:
        """Cria snippet representativo do documento para classificação."""
        # Primeiros 3000 chars + últimos 1000 chars
        head = text[:3000]
        tail = text[-1000:] if len(text) > 4000 else ""

        snippet = f"""DOCUMENTO: {filename}
Páginas: {num_pages or 'desconhecido'}
Tamanho: {len(text):,} caracteres

INÍCIO DO DOCUMENTO:
{head}
"""
        if tail:
            snippet += f"""
[...]

FINAL DO DOCUMENTO:
{tail}
"""
        return snippet

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        """Normaliza domínio para um dos valores válidos."""
        domain_lower = domain.lower().strip()

        # Mapeamento fuzzy
        mapping = {
            "civil": "Civil",
            "penal": "Penal",
            "criminal": "Penal",
            "trabalho": "Trabalho",
            "laboral": "Trabalho",
            "família": "Família",
            "familia": "Família",
            "administrativo": "Administrativo",
            "constitucional": "Constitucional",
            "comercial": "Comercial",
            "tributário": "Tributário",
            "tributario": "Tributário",
            "fiscal": "Tributário",
            "ambiental": "Ambiental",
            "consumidor": "Consumidor",
            "multi-domínio": "Multi-domínio",
            "multi-dominio": "Multi-domínio",
            "multiple": "Multi-domínio",
        }

        return mapping.get(domain_lower, "Civil")


def inject_page_markers(text: str, page_breaks: Optional[List[int]] = None) -> str:
    """
    Injecta marcadores [Pág_X] no texto do documento.

    Args:
        text: Texto do documento
        page_breaks: Lista de posições (char offsets) onde as páginas mudam.
                     Se None, tenta detectar form-feeds ou markers existentes.

    Returns:
        Texto com marcadores [Pág_X] inseridos
    """
    if not text:
        return text

    # Se já tem marcadores, não adicionar mais
    if "[Pág_" in text or "[Pag_" in text:
        return text

    # Se page_breaks fornecido, usar
    if page_breaks:
        result_parts = []
        prev_pos = 0
        for i, pos in enumerate(page_breaks):
            if pos > prev_pos:
                result_parts.append(text[prev_pos:pos])
            result_parts.append(f"\n[Pág_{i + 1}]\n")
            prev_pos = pos
        if prev_pos < len(text):
            result_parts.append(text[prev_pos:])
        return "".join(result_parts)

    # Tentar detectar form-feeds (\f)
    import re
    parts = re.split(r'\f', text)
    if len(parts) > 1:
        result_parts = []
        for i, part in enumerate(parts):
            if i > 0:
                result_parts.append(f"\n[Pág_{i + 1}]\n")
            result_parts.append(part)
        return "".join(result_parts)

    # Sem page breaks detectados, retornar como está
    return text

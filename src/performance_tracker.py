# -*- coding: utf-8 -*-
"""
PERFORMANCE TRACKER - Sistema de Feedback de Performance de IA
==============================================================
Guarda metricas granulares de cada chamada de IA e gera
adaptive hints para melhorar prompts futuros.
"""

import time
import json
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger(__name__)

CACHE_TTL = 300  # 5 minutos


@dataclass
class ModelHints:
    """Hints adaptativos em cache para um par modelo+role."""
    model: str
    role: str
    hints: List[str] = field(default_factory=list)
    hint_text: str = ""
    total_calls: int = 0
    success_rate: float = 1.0
    top_errors: Dict[str, int] = field(default_factory=dict)
    loaded_at: float = 0.0


# ============================================================
# REGRAS DE ADAPTIVE HINTS
# ============================================================

HINT_RULES = [
    {
        "code": "CITE_EXACT",
        "field": "excerpt_mismatches",
        "threshold": 0.15,  # >15% das chamadas
        "text": (
            "CITACAO CRITICA: Copia o texto EXACTAMENTE como aparece no documento, "
            "caracter por caracter. NAO parafrasear nem resumir. Se nao encontrares "
            "o texto exacto, usa excerpt vazio."
        ),
    },
    {
        "code": "VALID_RANGE",
        "field": "range_invalids",
        "threshold": 0.10,
        "text": (
            "OFFSETS CRITICOS: Verifica que start_char < end_char E ambos estao "
            "dentro do comprimento do documento. Se nao tiveres certeza dos "
            "offsets exactos, omite o campo ou usa 0."
        ),
    },
    {
        "code": "VALID_JSON",
        "field": "error_recovered",
        "threshold": 0.08,
        "text": (
            "FORMATO JSON: A tua resposta DEVE ser JSON valido. Sem comentarios, "
            "sem texto antes ou depois do JSON. Comeca com { e termina com }. "
            "Sem trailing commas. Todas as strings entre aspas duplas."
        ),
    },
    {
        "code": "REQUIRE_CITATIONS",
        "field": "missing_citations",
        "threshold": 0.10,
        "text": (
            "OBRIGATORIO: Cada finding/decision_point DEVE ter pelo menos 1 "
            "citation com evidence_item_id valido. Findings sem citations "
            "serao rejeitados automaticamente."
        ),
    },
    {
        "code": "CHECK_PAGES",
        "field": "page_mismatches",
        "threshold": 0.15,
        "text": (
            "PAGINAS: Verifica que page_num corresponde aos offsets "
            "start_char/end_char. Usa o mapeamento chars-para-pagina fornecido."
        ),
    },
    {
        "code": "OFFSET_CARE",
        "field": "offset_wrongs",
        "threshold": 0.20,
        "text": (
            "OFFSETS: Os offsets start_char/end_char devem apontar para a "
            "posicao EXACTA do texto no documento. Nao estimes - verifica."
        ),
    },
]

RELIABILITY_HINT = (
    "NOTA DE QUALIDADE: Historicamente, este modelo tem taxa de sucesso "
    "abaixo do esperado para esta tarefa. Concentra-te na completude, "
    "correctude e formato da resposta."
)


class PerformanceTracker:
    """
    Singleton que:
    1. Regista metricas por chamada no Supabase (fire-and-forget)
    2. Fornece adaptive hints em cache
    """

    _instance = None
    _lock = Lock()

    def __init__(self, supabase_client):
        self.sb = supabase_client
        self._hints_cache: Dict[Tuple[str, str], ModelHints] = {}
        self._cache_lock = Lock()
        self._cache_loaded_at: float = 0
        self._summary_cache: List[Dict] = []

    @classmethod
    def get_instance(cls, supabase_client=None):
        """Retorna instancia singleton."""
        with cls._lock:
            if cls._instance is None and supabase_client is not None:
                cls._instance = cls(supabase_client)
            return cls._instance

    # ============================================================
    # RECORDING
    # ============================================================

    def record_call(
        self,
        run_id: str,
        model: str,
        phase: str,
        role: str,
        tier: str = "bronze",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost_usd: float = 0.0,
        pricing_source: str = "",
        latency_ms: float = 0.0,
        success: bool = True,
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
        was_retry: bool = False,
        retry_number: int = 0,
        retry_of_id: Optional[str] = None,
        adaptive_hints_used: Optional[List[str]] = None,
        analysis_id: Optional[str] = None,
        # v5.2: Campos extra
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        finish_reason: str = "",
        api_used: str = "",
    ) -> Optional[str]:
        """Insere uma linha em model_performance. Fire-and-forget."""
        try:
            row = {
                "run_id": run_id,
                "model": model,
                "phase": phase,
                "role": role,
                "tier": tier,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost_usd": float(cost_usd),
                "pricing_source": pricing_source or "unknown",
                "latency_ms": float(latency_ms),
                "success": success,
                "error_message": (error_message or "")[:500] if error_message else None,
                "error_type": error_type,
                "was_retry": was_retry,
                "retry_number": retry_number,
                "adaptive_hints_used": adaptive_hints_used or [],
            }
            if retry_of_id:
                row["retry_of_id"] = retry_of_id
            if analysis_id:
                row["analysis_id"] = analysis_id
            # v5.2: Campos extra (graceful — ignora se colunas não existem no Supabase)
            if cached_tokens is not None:
                row["cached_tokens"] = cached_tokens
            if reasoning_tokens is not None:
                row["reasoning_tokens"] = reasoning_tokens
            if finish_reason:
                row["finish_reason"] = finish_reason
            if api_used:
                row["api_used"] = api_used

            result = self.sb.table("model_performance").insert(row).execute()
            record_id = result.data[0]["id"] if result.data else None
            if record_id:
                logger.debug(f"[PERF] Recorded: {role} {model} success={success}")
            return record_id
        except Exception as e:
            logger.warning(f"[PERF] Failed to record call: {e}")
            return None

    def record_integrity_attribution(
        self,
        run_id: str,
        role: str,
        excerpt_mismatches: int = 0,
        range_invalids: int = 0,
        offset_imprecises: int = 0,
        offset_wrongs: int = 0,
        page_mismatches: int = 0,
        missing_citations: int = 0,
        error_recovered: bool = False,
        findings_count: int = 0,
        citations_count: int = 0,
        confidence_raw: Optional[float] = None,
        confidence_adjusted: Optional[float] = None,
        penalty_total: Optional[float] = None,
    ):
        """Actualiza colunas de qualidade para uma chamada especifica."""
        try:
            update_data = {
                "excerpt_mismatches": excerpt_mismatches,
                "range_invalids": range_invalids,
                "offset_imprecises": offset_imprecises,
                "offset_wrongs": offset_wrongs,
                "page_mismatches": page_mismatches,
                "missing_citations": missing_citations,
                "error_recovered": error_recovered,
                "findings_count": findings_count,
                "citations_count": citations_count,
            }
            if confidence_raw is not None:
                update_data["confidence_raw"] = float(confidence_raw)
            if confidence_adjusted is not None:
                update_data["confidence_adjusted"] = float(confidence_adjusted)
            if penalty_total is not None:
                update_data["penalty_total"] = float(penalty_total)

            # FIX 2026-02-14: Limitar a 1 row para evitar update múltiplo
            self.sb.table("model_performance").update(
                update_data
            ).eq("run_id", run_id).eq("role", role).eq(
                "was_retry", False
            ).limit(1).execute()
            logger.debug(f"[PERF] Integrity attributed: {role}")
        except Exception as e:
            logger.warning(f"[PERF] Failed to update integrity for {role}: {e}")

    def link_document_id(self, run_id: str, document_id: str):
        """Liga document_id a todos os registos de um run."""
        try:
            self.sb.table("model_performance").update(
                {"document_id": document_id}
            ).eq("run_id", run_id).execute()
        except Exception as e:
            logger.warning(f"[PERF] Failed to link document_id: {e}")

    # ============================================================
    # ADAPTIVE HINTS (CACHE)
    # ============================================================

    def get_adaptive_hints(self, model: str, role: str) -> ModelHints:
        """Retorna hints em cache. Se cache miss, retorna vazio."""
        key = (model, role)
        now = time.time()

        with self._cache_lock:
            if key in self._hints_cache:
                cached = self._hints_cache[key]
                if now - cached.loaded_at < CACHE_TTL:
                    return cached

        # Cache miss - retorna vazio (nao bloqueia)
        return ModelHints(model=model, role=role, loaded_at=now)

    def refresh_cache(self):
        """Carrega dados agregados do Supabase para cache em memoria."""
        try:
            # Buscar ultimos 30 dias de performance agrupado por modelo+role
            result = self.sb.table("model_performance").select(
                "model, role, success, excerpt_mismatches, range_invalids, "
                "offset_imprecises, offset_wrongs, page_mismatches, "
                "missing_citations, error_recovered, latency_ms, "
                "total_tokens, cost_usd"
            ).order("created_at", desc=True).limit(2000).execute()

            rows = result.data or []
            if not rows:
                logger.info("[PERF] No performance data yet - cache empty")
                return

            # Agregar por modelo+role
            agg: Dict[Tuple[str, str], Dict] = {}
            for row in rows:
                key = (row["model"], row["role"])
                if key not in agg:
                    agg[key] = {
                        "total_calls": 0,
                        "successful": 0,
                        "excerpt_mismatches": 0,
                        "range_invalids": 0,
                        "offset_imprecises": 0,
                        "offset_wrongs": 0,
                        "page_mismatches": 0,
                        "missing_citations": 0,
                        "error_recovered": 0,
                        "total_latency": 0.0,
                        "total_tokens": 0,
                        "total_cost": 0.0,
                    }
                a = agg[key]
                a["total_calls"] += 1
                if row["success"]:
                    a["successful"] += 1
                a["excerpt_mismatches"] += row.get("excerpt_mismatches") or 0
                a["range_invalids"] += row.get("range_invalids") or 0
                a["offset_imprecises"] += row.get("offset_imprecises") or 0
                a["offset_wrongs"] += row.get("offset_wrongs") or 0
                a["page_mismatches"] += row.get("page_mismatches") or 0
                a["missing_citations"] += row.get("missing_citations") or 0
                if row.get("error_recovered"):
                    a["error_recovered"] += 1
                a["total_latency"] += float(row.get("latency_ms") or 0)
                a["total_tokens"] += row.get("total_tokens") or 0
                a["total_cost"] += float(row.get("cost_usd") or 0)

            # Construir hints
            new_cache: Dict[Tuple[str, str], ModelHints] = {}
            now = time.time()

            for (model, role), stats in agg.items():
                tc = max(stats["total_calls"], 1)
                success_rate = stats["successful"] / tc
                top_errors = {}
                hints_list = []
                hint_texts = []

                for rule in HINT_RULES:
                    field_val = stats.get(rule["field"], 0)
                    rate = field_val / tc
                    if rate > rule["threshold"]:
                        hints_list.append(rule["code"])
                        hint_texts.append(rule["text"])
                        top_errors[rule["field"]] = field_val

                if success_rate < 0.85:
                    hints_list.append("RELIABILITY")
                    hint_texts.append(RELIABILITY_HINT)

                hint_text = ""
                if hint_texts:
                    hint_text = "\n\n".join(hint_texts)
                    logger.info(
                        f"[PERF] Hints for {model}/{role}: "
                        f"{hints_list} (calls={tc}, success={success_rate:.0%})"
                    )

                new_cache[(model, role)] = ModelHints(
                    model=model,
                    role=role,
                    hints=hints_list,
                    hint_text=hint_text,
                    total_calls=tc,
                    success_rate=success_rate,
                    top_errors=top_errors,
                    loaded_at=now,
                )

            with self._cache_lock:
                self._hints_cache = new_cache
                self._summary_cache = [
                    {
                        "model": model,
                        "role": role,
                        "total_calls": stats["total_calls"],
                        "successful": stats["successful"],
                        "success_rate": round(stats["successful"] / max(stats["total_calls"], 1) * 100, 1),
                        "avg_latency_ms": round(stats["total_latency"] / max(stats["total_calls"], 1), 1),
                        "avg_tokens": round(stats["total_tokens"] / max(stats["total_calls"], 1)),
                        "avg_cost_usd": round(stats["total_cost"] / max(stats["total_calls"], 1), 6),
                        "total_cost_usd": round(stats["total_cost"], 4),
                        "excerpt_mismatches": stats["excerpt_mismatches"],
                        "range_invalids": stats["range_invalids"],
                        "page_mismatches": stats["page_mismatches"],
                        "missing_citations": stats["missing_citations"],
                        "error_recovered": stats["error_recovered"],
                        "hints_active": len(h.hints) if (h := new_cache.get((model, role))) else 0,
                    }
                    for (model, role), stats in agg.items()
                ]
                self._cache_loaded_at = time.time()
            logger.info(f"[PERF] Cache refreshed: {len(new_cache)} model+role pairs")

        except Exception as e:
            logger.warning(f"[PERF] Failed to refresh cache: {e}")

    def get_summary(self) -> List[Dict]:
        """Retorna o resumo em cache para o dashboard admin."""
        return self._summary_cache


# ============================================================
# QUALITY GATE
# ============================================================

def check_response_quality(content: str, role_name: str) -> Optional[Dict]:
    """
    Verifica qualidade da resposta de IA.
    Retorna None se OK, ou dict com detalhes do problema.
    """
    if not content or len(content.strip()) < 20:
        return {
            "code": "EMPTY_RESPONSE",
            "critical": True,
            "msg": "Resposta vazia ou demasiado curta",
        }

    # Verificacoes JSON para roles que devem retornar JSON
    # FIX v4.0: "agregador" produz Markdown, não JSON — excluir do quality gate JSON
    is_json_role = any(
        tag in role_name.lower()
        for tag in ("_json", "consolidador", "chefe")
    ) and "agregador" not in role_name.lower()
    if not is_json_role:
        return None

    cleaned = content.strip()
    # Remover markdown code blocks
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        ).strip()

    if not cleaned.startswith("{") and not cleaned.startswith("["):
        return {
            "code": "JSON_NO_OPEN_BRACE",
            "critical": True,
            "msg": "Resposta JSON nao comeca com { ou [",
        }

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return {
            "code": "JSON_INVALID",
            "critical": True,
            "msg": f"JSON invalido: {str(e)[:100]}",
        }

    # JSON valido mas sem findings
    if isinstance(parsed, dict):
        findings = parsed.get("findings", parsed.get("decision_points", []))
        if isinstance(findings, list) and len(findings) == 0:
            # Verificar se devia ter findings
            if "findings" in parsed or "decision_points" in parsed:
                return {
                    "code": "EMPTY_FINDINGS",
                    "critical": True,
                    "msg": "JSON valido mas com 0 findings/decision_points",
                }

        # JSON valido mas sem citations
        # FIX v5.0: Contar citations de AMBOS os campos possíveis (citations + supporting_citations)
        # FIX v5.2: Aceitar citações com apenas page_num ou excerpt (sem exigir offsets)
        total_citations = 0
        for item in parsed.get("findings", []):
            if isinstance(item, dict):
                for cit in item.get("citations", []):
                    if isinstance(cit, dict) and (cit.get("page_num") or cit.get("excerpt") or cit.get("start_char") is not None):
                        total_citations += 1
                    elif isinstance(cit, dict):
                        total_citations += 1  # Contar mesmo citação vazia (melhor que 0)
        for item in parsed.get("decision_points", []):
            if isinstance(item, dict):
                # Somar ambos os campos — prompt pede "citations" mas IA pode usar "supporting_citations"
                for cit in item.get("citations", []):
                    if isinstance(cit, dict):
                        total_citations += 1
                for cit in item.get("supporting_citations", []):
                    if isinstance(cit, dict):
                        total_citations += 1

        has_items = len(parsed.get("findings", [])) > 0 or len(parsed.get("decision_points", [])) > 0
        if has_items and total_citations == 0:
            return {
                "code": "NO_CITATIONS",
                "critical": True,
                "msg": "Findings/points existem mas com 0 citations",
            }

    return None


def build_retry_prompt(
    original_system: str,
    quality_issue: Dict,
    retry_number: int,
    adaptive_hints: str = "",
) -> Tuple[str, float]:
    """
    Constroi prompt melhorado para retry.
    Retorna (system_prompt, temperatura).
    """
    code = quality_issue["code"]

    RETRY_INSTRUCTIONS = {
        "EMPTY_RESPONSE": (
            "A tua resposta anterior foi VAZIA. Desta vez, DEVES produzir "
            "uma resposta completa e detalhada. Analisa o conteudo integralmente."
        ),
        "JSON_INVALID": (
            "A tua resposta anterior NAO era JSON valido. Desta vez:\n"
            "1. Responde APENAS com JSON valido\n"
            "2. Sem texto antes ou depois do JSON\n"
            "3. Sem markdown code blocks\n"
            "4. Sem trailing commas\n"
            "5. Todas as strings entre aspas duplas"
        ),
        "JSON_NO_OPEN_BRACE": (
            "A tua resposta anterior continha texto antes do JSON. "
            "Responde APENAS com o objecto JSON, comecando com { e terminando com }. "
            "Sem explicacoes, sem markdown, sem prefacios."
        ),
        "EMPTY_FINDINGS": (
            "A tua resposta anterior tinha 0 findings. O conteudo fornecido "
            "TEM materia para analisar. Re-analisa e fornece findings detalhados "
            "com citations."
        ),
        "NO_CITATIONS": (
            "A tua resposta anterior tinha findings/pontos mas ZERO citations. "
            "CADA finding/decision_point DEVE ter pelo menos 1 citation no campo "
            '"citations": [{"doc_id": "...", "start_char": N, "end_char": N, '
            '"page_num": N, "excerpt": "texto exacto"}]. '
            "NAO uses 'supporting_citations' — usa 'citations'."
        ),
    }

    instruction = RETRY_INSTRUCTIONS.get(
        code, f"Corrige o problema: {quality_issue['msg']}"
    )

    # Temperatura decresce com cada retry
    temperatures = {1: 0.2, 2: 0.1}
    temp = temperatures.get(retry_number, 0.1)

    # Retry 2 e mais agressivo
    if retry_number >= 2:
        instruction = (
            f"ATENCAO MAXIMA - SEGUNDA TENTATIVA DE CORRECCAO.\n"
            f"{instruction}\n"
            f"Se nao conseguires resolver, retorna o melhor resultado possivel "
            f"dentro do formato JSON valido."
        )

    enhanced = (
        f"{original_system}\n\n"
        f"=== TENTATIVA DE CORRECCAO #{retry_number} ===\n"
        f"{instruction}"
    )

    if adaptive_hints:
        enhanced += f"\n\n=== HISTORICO DE QUALIDADE ===\n{adaptive_hints}"

    return enhanced, temp


def classify_error(error_message: str) -> str:
    """Classifica o tipo de erro para tracking."""
    if not error_message:
        return "UNKNOWN"

    msg = error_message.lower()
    if "timeout" in msg:
        return "TIMEOUT"
    if "rate limit" in msg or "429" in msg:
        return "RATE_LIMIT"
    if "json" in msg or "parse" in msg:
        return "JSON_PARSE"
    if "empty" in msg or "vazio" in msg or "content empty" in msg:
        return "EMPTY_RESPONSE"
    if "budget" in msg or "exceed" in msg:
        return "BUDGET_EXCEEDED"
    if "500" in msg or "502" in msg or "503" in msg:
        return "SERVER_ERROR"
    if "401" in msg or "403" in msg or "api_key" in msg or "api key" in msg or "invalid key" in msg:
        return "AUTH_ERROR"
    return "OTHER"

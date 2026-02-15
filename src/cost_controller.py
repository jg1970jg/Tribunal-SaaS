# -*- coding: utf-8 -*-
"""
CONTROLO DE CUSTOS - Tribunal SaaS
============================================================
Monitoriza e controla custos/tokens por execução do pipeline.
Preços DINÂMICOS via OpenRouter API com cache de 24h.
Bloqueia execução se limites forem excedidos.
============================================================
"""

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from threading import Lock
import httpx

logger = logging.getLogger(__name__)

# ============================================================
# PREÇOS HARDCODED — FALLBACK de último recurso (Fev 2026)
# ============================================================

HARDCODED_PRICING = {
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai/gpt-4.1": {"input": 2.00, "output": 8.00},          # NOVO: suplente failover
    "openai/gpt-5.2": {"input": 1.75, "output": 14.00},
    "openai/gpt-5.2-pro": {"input": 21.00, "output": 168.00},
    "anthropic/claude-opus-4.6": {"input": 5.00, "output": 25.00},  # NOVO: actualizado de 4.5
    "anthropic/claude-opus-4.5": {"input": 5.00, "output": 25.00},  # Mantido para historico
    "anthropic/claude-sonnet-4.5": {"input": 3.00, "output": 15.00},  # NOVO: substitui Opus em E1/A2/J2
    "anthropic/claude-3-5-sonnet": {"input": 6.00, "output": 30.00},
    "anthropic/claude-3.5-haiku": {"input": 0.25, "output": 1.25},
    "google/gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
    "google/gemini-3-pro-preview": {"input": 2.00, "output": 12.00},
    "deepseek/deepseek-chat": {"input": 0.28, "output": 0.42},
    "meta-llama/llama-4-maverick": {"input": 0.20, "output": 0.60},
    "mistralai/mistral-medium-3": {"input": 1.00, "output": 4.00},
    "x-ai/grok-4": {"input": 3.00, "output": 15.00},
    "x-ai/grok-4.1-fast": {"input": 0.20, "output": 0.50},
    "x-ai/grok-4.1": {"input": 0.20, "output": 0.50},
    "anthropic/claude-haiku-4.5": {"input": 1.00, "output": 5.00},
    "default": {"input": 1.00, "output": 4.00},
}

# Alias: MODEL_PRICING aponta para hardcoded para retrocompatibilidade
MODEL_PRICING = HARDCODED_PRICING


# ============================================================
# DYNAMIC PRICING — Preços reais via OpenRouter API
# ============================================================

class DynamicPricing:
    """
    Busca preços reais da OpenRouter API com cache de 24h.

    Hierarquia de fontes:
    1. [PRECO-LIVE] — OpenRouter API (fresh fetch)
    2. [PRECO-CACHE] — Cache em memória (<24h ou stale se API down)
    3. [PRECO-HARDCODED] — Tabela hardcoded (último recurso)
    """

    OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
    CACHE_TTL_HOURS = 24
    FETCH_TIMEOUT = 15  # segundos

    # Cache de classe (partilhado entre todas as instâncias/runs)
    _cache: Dict[str, Dict[str, float]] = {}
    _cache_timestamp: Optional[datetime] = None
    _cache_lock = Lock()
    _models_used: Dict[str, Dict] = {}  # {model: {input, output, fonte}}

    @classmethod
    def fetch_openrouter_prices(cls) -> bool:
        """
        Busca preços de TODOS os modelos da OpenRouter API.

        Returns:
            True se fetch bem-sucedido, False caso contrário.
        """
        try:
            logger.info("[PRECO] Buscando preços da OpenRouter API...")
            # FIX 2026-02-14: context manager para fechar client mesmo com excepção
            with httpx.Client(timeout=cls.FETCH_TIMEOUT) as client:
                response = client.get(cls.OPENROUTER_URL)

            if response.status_code != 200:
                logger.warning(f"[PRECO] OpenRouter API retornou status {response.status_code}")
                return False

            data = response.json()
            models = data.get("data", [])

            if not models:
                logger.warning("[PRECO] OpenRouter API retornou lista vazia")
                return False

            new_cache = {}
            for model in models:
                model_id = model.get("id", "")
                pricing = model.get("pricing")

                if not model_id or not pricing:
                    continue

                prompt_str = pricing.get("prompt", "0")
                completion_str = pricing.get("completion", "0")

                try:
                    # Preços vêm em USD/token — converter para USD/1M tokens
                    input_per_1m = float(prompt_str) * 1_000_000
                    output_per_1m = float(completion_str) * 1_000_000
                except (ValueError, TypeError):
                    continue

                if input_per_1m > 0 or output_per_1m > 0:
                    new_cache[model_id.lower()] = {
                        "input": round(input_per_1m, 4),
                        "output": round(output_per_1m, 4),
                    }

            with cls._cache_lock:
                cls._cache = new_cache
                cls._cache_timestamp = datetime.now()

            logger.info(
                f"[PRECO-LIVE] Carregados preços de {len(new_cache)} modelos da OpenRouter "
                f"(timestamp: {cls._cache_timestamp.strftime('%H:%M:%S')})"
            )
            return True

        except httpx.TimeoutException:
            logger.warning("[PRECO] Timeout ao buscar preços da OpenRouter API")
            return False
        except Exception as e:
            logger.warning(f"[PRECO] Erro ao buscar preços da OpenRouter API: {e}")
            return False

    @classmethod
    def _is_cache_valid(cls) -> bool:
        """Verifica se o cache está dentro do TTL."""
        if not cls._cache_timestamp or not cls._cache:
            return False
        age_hours = (datetime.now() - cls._cache_timestamp).total_seconds() / 3600
        return age_hours < cls.CACHE_TTL_HOURS

    @classmethod
    def _is_cache_stale(cls) -> bool:
        """Verifica se existe cache (mesmo que expirado)."""
        return bool(cls._cache) and cls._cache_timestamp is not None

    @classmethod
    def get_pricing(cls, model: str) -> Dict:
        """
        Retorna preço para um modelo com hierarquia:
        1. Cache válido (<24h) → [PRECO-LIVE] ou [PRECO-CACHE]
        2. Se cache expirado → tentar fetch → [PRECO-LIVE]
        3. Se fetch falhar → cache stale → [PRECO-CACHE]
        4. Se sem cache → hardcoded → [PRECO-HARDCODED]

        Returns:
            {"input": float, "output": float, "fonte": str}
        """
        model_clean = model.lower().strip()

        # 1. Cache válido
        if cls._is_cache_valid():
            price = cls._lookup_in_cache(model_clean)
            if price:
                result = {**price, "fonte": "openrouter_live"}
                cls._track_model(model_clean, result)
                return result

        # 2. Cache expirado — tentar refresh
        if not cls._is_cache_valid():
            fetched = cls.fetch_openrouter_prices()
            if fetched:
                price = cls._lookup_in_cache(model_clean)
                if price:
                    result = {**price, "fonte": "openrouter_live"}
                    cls._track_model(model_clean, result)
                    return result

        # 3. Cache stale (API down mas temos dados antigos)
        if cls._is_cache_stale():
            price = cls._lookup_in_cache(model_clean)
            if price:
                age_hours = (datetime.now() - cls._cache_timestamp).total_seconds() / 3600
                logger.warning(
                    f"[PRECO-CACHE] Usando cache de {age_hours:.1f}h para {model} "
                    f"(API indisponível)"
                )
                result = {**price, "fonte": f"cache_{age_hours:.0f}h"}
                cls._track_model(model_clean, result)
                return result

        # 4. Fallback hardcoded
        price = cls._lookup_hardcoded(model_clean)
        logger.warning(f"[PRECO-HARDCODED] Usando preço hardcoded para {model}")
        result = {**price, "fonte": "hardcoded"}
        cls._track_model(model_clean, result)
        return result

    @classmethod
    def _normalize(cls, name: str) -> str:
        """Normaliza nome de modelo para comparação (3-5 == 3.5)."""
        return name.replace("-", ".").replace("_", ".")

    @classmethod
    def _lookup_in_cache(cls, model_clean: str) -> Optional[Dict[str, float]]:
        """Procura preço no cache (exact → normalized → partial)."""
        with cls._cache_lock:
            # Exact match
            if model_clean in cls._cache:
                return cls._cache[model_clean]

            # Normalized match (3-5 == 3.5, etc.)
            model_norm = cls._normalize(model_clean)
            for key in cls._cache:
                if cls._normalize(key) == model_norm:
                    return cls._cache[key]

            # Partial match
            for key in cls._cache:
                if key in model_clean or model_clean in key:
                    return cls._cache[key]

        return None

    @classmethod
    def _lookup_hardcoded(cls, model_clean: str) -> Dict[str, float]:
        """Procura preço na tabela hardcoded."""
        if model_clean in HARDCODED_PRICING:
            return HARDCODED_PRICING[model_clean]

        for key in HARDCODED_PRICING:
            if key in model_clean or model_clean in key:
                return HARDCODED_PRICING[key]

        return HARDCODED_PRICING["default"]

    @classmethod
    def _track_model(cls, model: str, pricing: Dict):
        """Guarda pricing usado para cada modelo (para relatório final)."""
        cls._models_used[model] = {
            "input": pricing["input"],
            "output": pricing["output"],
            "fonte": pricing["fonte"],
        }

    @classmethod
    def get_models_used(cls) -> Dict[str, Dict]:
        """Retorna preços usados por modelo neste run."""
        return dict(cls._models_used)

    @classmethod
    def get_pricing_source(cls) -> str:
        """Retorna a fonte principal dos preços."""
        if cls._is_cache_valid():
            return "openrouter_live"
        if cls._is_cache_stale():
            age = (datetime.now() - cls._cache_timestamp).total_seconds() / 3600
            return f"cache_{age:.0f}h"
        return "hardcoded"

    @classmethod
    def get_cache_info(cls) -> Dict:
        """Retorna info sobre o estado do cache."""
        return {
            "has_cache": bool(cls._cache),
            "cache_size": len(cls._cache),
            "cache_valid": cls._is_cache_valid(),
            "cache_timestamp": cls._cache_timestamp.isoformat() if cls._cache_timestamp else None,
            "cache_age_hours": round(
                (datetime.now() - cls._cache_timestamp).total_seconds() / 3600, 1
            ) if cls._cache_timestamp else None,
        }

    @classmethod
    def prefetch(cls):
        """Pre-fetch não bloqueante. Chama no início do pipeline."""
        if not cls._is_cache_valid():
            cls.fetch_openrouter_prices()

    @classmethod
    def reset(cls):
        """Reset do cache e tracking (para testes)."""
        with cls._cache_lock:
            cls._cache = {}
            cls._cache_timestamp = None
            cls._models_used = {}


@dataclass
class PhaseUsage:
    """Uso de uma fase específica do pipeline."""
    phase: str          # "fase1_E1", "fase2_A1", etc.
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    pricing_source: str = ""  # "openrouter_live", "cache_Xh", "hardcoded"
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "phase": self.phase,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "pricing_source": self.pricing_source,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class RunUsage:
    """Uso total de uma execução do pipeline."""
    run_id: str
    phases: List[PhaseUsage] = field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    budget_limit_usd: float = 15.0
    token_limit: int = 1200000
    blocked: bool = False
    block_reason: Optional[str] = None
    timestamp_start: datetime = field(default_factory=datetime.now)
    timestamp_end: Optional[datetime] = None

    def to_dict(self) -> Dict:
        return {
            "run_id": self.run_id,
            "phases": [p.to_dict() for p in self.phases],
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "budget_limit_usd": self.budget_limit_usd,
            "token_limit": self.token_limit,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "timestamp_start": self.timestamp_start.isoformat(),
            "timestamp_end": self.timestamp_end.isoformat() if self.timestamp_end else None,
        }


class BudgetExceededError(Exception):
    """Exceção lançada quando o budget é excedido."""
    def __init__(self, message: str, current_cost: float, budget_limit: float):
        self.current_cost = current_cost
        self.budget_limit = budget_limit
        super().__init__(message)


class TokenLimitExceededError(Exception):
    """Exceção lançada quando o limite de tokens é excedido."""
    def __init__(self, message: str, current_tokens: int, token_limit: int):
        self.current_tokens = current_tokens
        self.token_limit = token_limit
        super().__init__(message)


class CostController:
    """
    Controlador de custos para o pipeline.
    Usa DynamicPricing para preços reais via OpenRouter API.

    Uso:
        controller = CostController(run_id="xxx", budget_limit=5.0)
        controller.register_usage("fase1_E1", "openai/gpt-4o", 1000, 500)
        if controller.can_continue():
            # continuar processamento
    """

    def __init__(
        self,
        run_id: str,
        budget_limit_usd: Optional[float] = None,
        token_limit: Optional[int] = None,
    ):
        self.run_id = run_id

        # Limites (do .env ou defaults)
        self.budget_limit = budget_limit_usd or float(os.getenv("MAX_BUDGET_USD", "15.0"))
        self.token_limit = token_limit or int(os.getenv("MAX_TOKENS_TOTAL", "1200000"))

        # Uso
        self.usage = RunUsage(
            run_id=run_id,
            budget_limit_usd=self.budget_limit,
            token_limit=self.token_limit,
        )

        # Thread safety
        self._lock = Lock()

        # FIX 2026-02-14: Limpar _models_used entre runs (evita leak/contaminação)
        DynamicPricing._models_used = {}

        # Pre-fetch preços (usa cache se válido, fetch se expirado)
        DynamicPricing.prefetch()

        logger.info(
            f"CostController inicializado: budget=${self.budget_limit:.2f}, "
            f"tokens={self.token_limit:,}, "
            f"precos={DynamicPricing.get_pricing_source()}"
        )

    def get_model_pricing(self, model: str) -> Dict[str, float]:
        """Retorna preços para um modelo via DynamicPricing."""
        pricing = DynamicPricing.get_pricing(model)
        # Retornar apenas input/output (sem 'fonte') para compatibilidade
        return {"input": pricing["input"], "output": pricing["output"]}

    def calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Calcula custo de uma chamada em USD."""
        pricing = self.get_model_pricing(model)
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    def register_usage(
        self,
        phase: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        raise_on_exceed: bool = True,
    ) -> PhaseUsage:
        """Regista uso de uma chamada LLM."""
        with self._lock:
            # Obter pricing com fonte
            pricing = DynamicPricing.get_pricing(model)
            fonte = pricing["fonte"]

            # Calcular custo
            input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
            output_cost = (completion_tokens / 1_000_000) * pricing["output"]
            cost = input_cost + output_cost
            total_tokens = prompt_tokens + completion_tokens

            # Criar registo
            phase_usage = PhaseUsage(
                phase=phase,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
                pricing_source=fonte,
            )

            # Adicionar ao total
            self.usage.phases.append(phase_usage)
            self.usage.total_prompt_tokens += prompt_tokens
            self.usage.total_completion_tokens += completion_tokens
            self.usage.total_tokens += total_tokens
            self.usage.total_cost_usd += cost

            # Log detalhado com fonte do preço
            logger.info(
                f"[CUSTO] {phase}: {model} | "
                f"{prompt_tokens:,}+{completion_tokens:,}={total_tokens:,} tokens | "
                f"${cost:.4f} [{fonte.upper()}] | "
                f"Total: ${self.usage.total_cost_usd:.4f}/{self.budget_limit:.2f}"
            )

            # Verificar limites
            if raise_on_exceed:
                self._check_limits()
            else:
                self._warn_limits()

        return phase_usage

    def _warn_limits(self):
        """Loga WARNING se limites foram excedidos (sem bloquear)."""
        if self.usage.total_cost_usd > self.budget_limit and not self.usage.blocked:
            logger.warning(
                f"[CUSTO-ALERTA] Budget excedido: "
                f"${self.usage.total_cost_usd:.4f} > ${self.budget_limit:.2f} "
                f"(run={self.run_id}) — continuando sem bloquear"
            )
        if self.usage.total_tokens > self.token_limit and not self.usage.blocked:
            logger.warning(
                f"[CUSTO-ALERTA] Tokens excedidos: "
                f"{self.usage.total_tokens:,} > {self.token_limit:,} "
                f"(run={self.run_id}) — continuando sem bloquear"
            )

    def _check_limits(self):
        """Verifica se limites foram excedidos."""
        if self.usage.total_cost_usd > self.budget_limit:
            self.usage.blocked = True
            self.usage.block_reason = f"Budget excedido: ${self.usage.total_cost_usd:.4f} > ${self.budget_limit:.2f}"
            logger.error(f"BLOQUEADO: {self.usage.block_reason}")
            raise BudgetExceededError(
                self.usage.block_reason,
                self.usage.total_cost_usd,
                self.budget_limit,
            )

        if self.usage.total_tokens > self.token_limit:
            self.usage.blocked = True
            self.usage.block_reason = f"Tokens excedidos: {self.usage.total_tokens:,} > {self.token_limit:,}"
            logger.error(f"BLOQUEADO: {self.usage.block_reason}")
            raise TokenLimitExceededError(
                self.usage.block_reason,
                self.usage.total_tokens,
                self.token_limit,
            )

    def can_continue(self) -> bool:
        """Verifica se pode continuar processamento."""
        with self._lock:
            return (
                not self.usage.blocked
                and self.usage.total_cost_usd < self.budget_limit
                and self.usage.total_tokens < self.token_limit
            )

    def get_remaining_budget(self) -> float:
        """Retorna budget restante em USD."""
        return max(0, self.budget_limit - self.usage.total_cost_usd)

    def get_remaining_tokens(self) -> int:
        """Retorna tokens restantes."""
        return max(0, self.token_limit - self.usage.total_tokens)

    def get_usage_percentage(self) -> Dict[str, float]:
        """Retorna percentagem de uso."""
        return {
            "budget_pct": (self.usage.total_cost_usd / self.budget_limit) * 100 if self.budget_limit > 0 else 0,
            "tokens_pct": (self.usage.total_tokens / self.token_limit) * 100 if self.token_limit > 0 else 0,
        }

    def finalize(self) -> RunUsage:
        """Finaliza a execução e retorna uso total."""
        with self._lock:
            self.usage.timestamp_end = datetime.now()
            return self.usage

    def get_summary(self) -> Dict:
        """Retorna resumo de uso para UI."""
        pcts = self.get_usage_percentage()
        return {
            "run_id": self.run_id,
            "total_tokens": self.usage.total_tokens,
            "total_cost_usd": round(self.usage.total_cost_usd, 4),
            "budget_limit_usd": self.budget_limit,
            "token_limit": self.token_limit,
            "budget_remaining_usd": round(self.get_remaining_budget(), 4),
            "tokens_remaining": self.get_remaining_tokens(),
            "budget_pct": round(pcts["budget_pct"], 1),
            "tokens_pct": round(pcts["tokens_pct"], 1),
            "num_phases": len(self.usage.phases),
            "blocked": self.usage.blocked,
            "block_reason": self.usage.block_reason,
        }

    def get_cost_by_phase(self) -> Dict[str, float]:
        """Retorna custo agrupado por fase (fase1, fase2, fase3, fase4)."""
        PHASE_MAP = {
            "fase1": "fase1", "extrator": "fase1", "agregador": "fase1",
            "fase2": "fase2", "auditor": "fase2", "chefe": "fase2",
            "fase3": "fase3", "juiz": "fase3",
            "fase4": "fase4", "presidente": "fase4",
        }
        costs = {}
        for phase in self.usage.phases:
            base = phase.phase.split("_")[0] if "_" in phase.phase else phase.phase
            mapped = PHASE_MAP.get(base, base)
            if mapped not in costs:
                costs[mapped] = 0.0
            costs[mapped] += phase.cost_usd
        return {k: round(v, 4) for k, v in costs.items()}

    def get_pricing_info(self) -> Dict:
        """Retorna informação sobre preços usados neste run."""
        return {
            "fonte": DynamicPricing.get_pricing_source(),
            "timestamp": DynamicPricing._cache_timestamp.isoformat() if DynamicPricing._cache_timestamp else None,
            "cache_info": DynamicPricing.get_cache_info(),
            "precos_por_modelo": DynamicPricing.get_models_used(),
        }


# ============================================================
# Instância global (opcional)
# ============================================================

_current_controller: Optional[CostController] = None


def get_cost_controller() -> Optional[CostController]:
    """Retorna controlador atual (se existir)."""
    global _current_controller
    return _current_controller


def set_cost_controller(controller: CostController):
    """Define controlador global."""
    global _current_controller
    _current_controller = controller


def clear_cost_controller():
    """Limpa controlador global."""
    global _current_controller
    _current_controller = None

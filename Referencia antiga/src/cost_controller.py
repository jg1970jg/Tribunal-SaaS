# -*- coding: utf-8 -*-
"""
CONTROLO DE CUSTOS - Tribunal GoldenMaster
============================================================
Monitoriza e controla custos/tokens por execução do pipeline.
Bloqueia execução se limites forem excedidos.
============================================================
"""

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from threading import Lock

logger = logging.getLogger(__name__)


# ============================================================
# PREÇOS POR MODELO (USD por 1M tokens) — Atualizado Fev 2026
# ============================================================
# Fontes: platform.openai.com/docs/pricing, platform.claude.com/docs/en/about-claude/pricing,
#          ai.google.dev/gemini-api/docs/pricing, docs.x.ai/developers/models,
#          api-docs.deepseek.com/quick_start/pricing
MODEL_PRICING = {
    # OpenAI (Fev 2026)
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai/gpt-5.2": {"input": 1.75, "output": 14.00},
    "openai/gpt-5.2-pro": {"input": 21.00, "output": 168.00},
    # Anthropic (Fev 2026 — Opus 4.5 desceu 66% vs Opus 4)
    "anthropic/claude-opus-4.5": {"input": 5.00, "output": 25.00},
    "anthropic/claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "anthropic/claude-3.5-haiku": {"input": 0.25, "output": 1.25},
    # Google (Fev 2026)
    "google/gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
    "google/gemini-3-pro-preview": {"input": 2.00, "output": 12.00},
    # DeepSeek (V3.2 — set 2025)
    "deepseek/deepseek-chat": {"input": 0.28, "output": 0.42},
    # Qwen
    "qwen/qwen-235b-instruct": {"input": 0.20, "output": 0.60},
    # xAI (Fev 2026)
    "x-ai/grok-4.1-fast": {"input": 0.20, "output": 0.50},
    "x-ai/grok-4.1": {"input": 0.20, "output": 0.50},
    # Default para modelos desconhecidos
    "default": {"input": 1.00, "output": 4.00},
}


@dataclass
class PhaseUsage:
    """Uso de uma fase específica do pipeline."""
    phase: str  # "fase1_E1", "fase2_A1", etc.
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "phase": self.phase,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
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
    budget_limit_usd: float = 5.0
    token_limit: int = 500000
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

    Funcionalidades:
    - Contabiliza tokens e custos por fase
    - Bloqueia execução se exceder limites
    - Gera relatórios de uso

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
        """
        Args:
            run_id: ID da execução
            budget_limit_usd: Limite de custo em USD (None = usar env var ou 5.0)
            token_limit: Limite de tokens (None = usar env var ou 500000)
        """
        self.run_id = run_id

        # Limites (do .env ou defaults)
        self.budget_limit = budget_limit_usd or float(os.getenv("MAX_BUDGET_USD", "5.0"))
        self.token_limit = token_limit or int(os.getenv("MAX_TOKENS_TOTAL", "500000"))

        # Uso
        self.usage = RunUsage(
            run_id=run_id,
            budget_limit_usd=self.budget_limit,
            token_limit=self.token_limit,
        )

        # Thread safety
        self._lock = Lock()

        logger.info(f"CostController inicializado: budget=${self.budget_limit:.2f}, tokens={self.token_limit:,}")

    def get_model_pricing(self, model: str) -> Dict[str, float]:
        """Retorna preços para um modelo."""
        # Normalizar nome do modelo
        model_clean = model.lower().strip()

        # Procurar match exato
        if model_clean in MODEL_PRICING:
            return MODEL_PRICING[model_clean]

        # Procurar match parcial
        for key in MODEL_PRICING:
            if key in model_clean or model_clean in key:
                return MODEL_PRICING[key]

        # Default
        logger.warning(f"Modelo não encontrado na tabela de preços: {model}, usando default")
        return MODEL_PRICING["default"]

    def calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """
        Calcula custo de uma chamada.

        Returns:
            Custo em USD
        """
        pricing = self.get_model_pricing(model)

        # Converter de preço por 1M tokens para tokens reais
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
        """
        Regista uso de uma chamada LLM.

        Args:
            phase: Nome da fase (ex: "fase1_E1", "fase2_A1")
            model: Nome do modelo
            prompt_tokens: Tokens de input
            completion_tokens: Tokens de output
            raise_on_exceed: Se True, levanta exceção se limites excedidos

        Returns:
            PhaseUsage com detalhes

        Raises:
            BudgetExceededError: Se budget excedido
            TokenLimitExceededError: Se tokens excedidos
        """
        with self._lock:
            # Calcular custo
            cost = self.calculate_cost(model, prompt_tokens, completion_tokens)
            total_tokens = prompt_tokens + completion_tokens

            # Criar registo
            phase_usage = PhaseUsage(
                phase=phase,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
            )

            # Adicionar ao total
            self.usage.phases.append(phase_usage)
            self.usage.total_prompt_tokens += prompt_tokens
            self.usage.total_completion_tokens += completion_tokens
            self.usage.total_tokens += total_tokens
            self.usage.total_cost_usd += cost

            # Log
            logger.info(
                f"[CUSTO] {phase}: {model} | "
                f"{total_tokens:,} tokens | "
                f"${cost:.4f} | "
                f"Total: ${self.usage.total_cost_usd:.4f}/{self.budget_limit:.2f}"
            )

            # Verificar limites
            if raise_on_exceed:
                self._check_limits()

            return phase_usage

    def _check_limits(self):
        """Verifica se limites foram excedidos."""
        # Verificar budget
        if self.usage.total_cost_usd > self.budget_limit:
            self.usage.blocked = True
            self.usage.block_reason = f"Budget excedido: ${self.usage.total_cost_usd:.4f} > ${self.budget_limit:.2f}"
            logger.error(f"BLOQUEADO: {self.usage.block_reason}")
            raise BudgetExceededError(
                self.usage.block_reason,
                self.usage.total_cost_usd,
                self.budget_limit,
            )

        # Verificar tokens
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
        """
        Verifica se pode continuar processamento.

        Returns:
            True se dentro dos limites
        """
        with self._lock:
            return (
                not self.usage.blocked and
                self.usage.total_cost_usd < self.budget_limit and
                self.usage.total_tokens < self.token_limit
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
        # Mapeamento de role_name → fase do pipeline
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

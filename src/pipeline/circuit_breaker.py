# ============================================================================
# Pipeline v4.2 — Circuit Breaker
# ============================================================================
# Circuit breaker Redis-backed para tracking de falhas por provider.
# Previne chamadas repetidas a providers que estão em falha.
#
# Estados:
#   CLOSED   → Normal, chamadas passam
#   OPEN     → Provider em falha, chamadas rejeitadas
#   HALF_OPEN → A testar recuperação (1 chamada de teste)
# ============================================================================

import time
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStatus:
    state: CircuitState
    failure_count: int
    last_failure_time: float
    last_success_time: float


class CircuitBreaker:
    """
    Circuit breaker por provider.

    Usa Redis se disponível, senão usa memória local (fallback).
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
        redis_client=None,
        key_prefix: str = "cb:",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._redis = redis_client
        self._key_prefix = key_prefix
        # Fallback in-memory storage
        self._local_state: dict[str, CircuitStatus] = {}

    def _get_key(self, provider: str) -> str:
        return f"{self._key_prefix}{provider}"

    def _get_status(self, provider: str) -> CircuitStatus:
        """Get current circuit status for a provider."""
        if self._redis:
            try:
                key = self._get_key(provider)
                data = self._redis.hgetall(key)
                if data:
                    return CircuitStatus(
                        state=CircuitState(data.get(b"state", b"closed").decode()),
                        failure_count=int(data.get(b"failure_count", b"0")),
                        last_failure_time=float(data.get(b"last_failure_time", b"0")),
                        last_success_time=float(data.get(b"last_success_time", b"0")),
                    )
            except Exception as e:
                logger.warning(f"Redis circuit breaker read failed for {provider}: {e}")

        return self._local_state.get(
            provider,
            CircuitStatus(
                state=CircuitState.CLOSED,
                failure_count=0,
                last_failure_time=0,
                last_success_time=0,
            ),
        )

    def _set_status(self, provider: str, status: CircuitStatus) -> None:
        """Persist circuit status."""
        self._local_state[provider] = status

        if self._redis:
            try:
                key = self._get_key(provider)
                self._redis.hset(
                    key,
                    mapping={
                        "state": status.state.value,
                        "failure_count": str(status.failure_count),
                        "last_failure_time": str(status.last_failure_time),
                        "last_success_time": str(status.last_success_time),
                    },
                )
                # Auto-expire after 10 minutes
                self._redis.expire(key, 600)
            except Exception as e:
                logger.warning(f"Redis circuit breaker write failed for {provider}: {e}")

    def can_call(self, provider: str) -> bool:
        """
        Check if a call to this provider should be allowed.

        Returns True if CLOSED or HALF_OPEN (test call), False if OPEN.
        """
        status = self._get_status(provider)

        if status.state == CircuitState.CLOSED:
            return True

        if status.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            elapsed = time.time() - status.last_failure_time
            if elapsed >= self.recovery_timeout:
                # Transition to HALF_OPEN
                status.state = CircuitState.HALF_OPEN
                self._set_status(provider, status)
                logger.info(f"Circuit {provider}: OPEN → HALF_OPEN (recovery test)")
                return True
            return False

        # HALF_OPEN: allow one test call
        return True

    def record_success(self, provider: str) -> None:
        """Record a successful call. Resets circuit to CLOSED."""
        status = self._get_status(provider)
        if status.state != CircuitState.CLOSED:
            logger.info(f"Circuit {provider}: {status.state.value} → CLOSED (success)")
        status.state = CircuitState.CLOSED
        status.failure_count = 0
        status.last_success_time = time.time()
        self._set_status(provider, status)

    def record_failure(self, provider: str, error: Optional[str] = None) -> None:
        """Record a failed call. May transition to OPEN."""
        status = self._get_status(provider)
        status.failure_count += 1
        status.last_failure_time = time.time()

        if status.state == CircuitState.HALF_OPEN:
            # Test call failed → back to OPEN
            status.state = CircuitState.OPEN
            logger.warning(f"Circuit {provider}: HALF_OPEN → OPEN (test failed: {error})")
        elif status.failure_count >= self.failure_threshold:
            status.state = CircuitState.OPEN
            logger.warning(
                f"Circuit {provider}: CLOSED → OPEN "
                f"({status.failure_count} failures, threshold={self.failure_threshold})"
            )

        self._set_status(provider, status)

    def get_all_statuses(self) -> dict[str, CircuitStatus]:
        """Get status of all known providers."""
        return dict(self._local_state)

    def reset(self, provider: str) -> None:
        """Manually reset a circuit to CLOSED."""
        self._set_status(
            provider,
            CircuitStatus(
                state=CircuitState.CLOSED,
                failure_count=0,
                last_failure_time=0,
                last_success_time=time.time(),
            ),
        )
        logger.info(f"Circuit {provider}: manually reset to CLOSED")

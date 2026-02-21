# ============================================================================
# Pipeline v4.2 — Celery Configuration
# ============================================================================
# Configura Celery com Redis como broker para processamento assíncrono
# de OCR (Eden AI), limpeza LLM, e análise jurídica.
# ============================================================================

import os
import logging

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    from celery import Celery

    celery_app = Celery(
        "tribunal_pipeline",
        broker=REDIS_URL,
        backend=REDIS_URL,
    )

    celery_app.conf.update(
        # Serialization
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        # Timezone
        timezone="UTC",
        enable_utc=True,
        # Task tracking
        task_track_started=True,
        task_acks_late=True,
        # Memory: process one task at a time (Render 2GB RAM)
        worker_prefetch_multiplier=1,
        worker_concurrency=1,
        # Time limits
        task_soft_time_limit=300,   # 5 min soft limit
        task_time_limit=600,        # 10 min hard limit
        # Results
        result_expires=3600,        # 1 hour
        # Connection
        broker_connection_retry_on_startup=True,
        broker_connection_retry=True,
        broker_connection_max_retries=10,
    )

    CELERY_AVAILABLE = True
    logger.info(f"Celery configurado com broker: {REDIS_URL[:20]}...")

except ImportError:
    celery_app = None
    CELERY_AVAILABLE = False
    logger.warning("Celery não disponível — pipeline v4.2 irá executar de forma síncrona")

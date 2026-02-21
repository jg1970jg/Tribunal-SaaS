# ============================================================================
# Pipeline v4.2 — Celery Worker Entry Point
# ============================================================================
# Ponto de entrada para o worker Celery no Render.
#
# Iniciar com:
#   celery -A celery_worker worker --loglevel=info --concurrency=1
# ============================================================================

from src.pipeline.celery_app import celery_app  # noqa: F401

# Importar módulos de tasks para que o Celery os descubra
# (serão criados nas fases seguintes)
try:
    import src.pipeline.m3_ocr_engine  # noqa: F401
except ImportError:
    pass

try:
    import src.pipeline.m3b_multifeature  # noqa: F401
except ImportError:
    pass

try:
    import src.pipeline.m4_llm_cleaning  # noqa: F401
except ImportError:
    pass

try:
    import src.pipeline.m7_legal_analysis  # noqa: F401
except ImportError:
    pass

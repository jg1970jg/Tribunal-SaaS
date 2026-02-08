# -*- coding: utf-8 -*-
"""
Tribunal SaaS V2 - Package src
Estende o package para incluir modulos da Referencia antiga/src.
"""

import sys
from pathlib import Path

__version__ = "2.0.0"

_ref_root = Path(__file__).resolve().parent.parent / "Referencia antiga"
_ref_src = str(_ref_root / "src")

# 1. Estender __path__ para que `from src.config import ...`
#    encontre modulos em Referencia antiga/src/
if _ref_src not in __path__:
    __path__.append(_ref_src)

# 2. Adicionar Referencia antiga/ ao sys.path para modulos
#    importados pela referencia a nivel raiz (ex: prompts_maximos)
_ref_root_str = str(_ref_root)
if _ref_root_str not in sys.path:
    sys.path.append(_ref_root_str)

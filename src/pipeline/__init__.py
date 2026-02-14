# -*- coding: utf-8 -*-
"""
Módulo de pipeline - orquestração do processo de análise.
"""

from .processor import TribunalProcessor, PipelineResult, FaseResult

__all__ = ["TribunalProcessor", "PipelineResult", "FaseResult"]

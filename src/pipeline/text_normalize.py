# -*- coding: utf-8 -*-
"""
Normalização de Texto Unificada para o Pipeline do Tribunal.

Este módulo centraliza TODA a normalização de texto usada para:
- IntegrityValidator (excerpt matching)
- CharToPageMapper (marcadores de página)
- Comparação de conteúdo entre fases

REGRAS:
1. Uma única função de normalização para consistência
2. Debug info disponível (raw vs normalized)
3. Configurável mas com defaults seguros
"""

import re
import unicodedata
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Set


logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

@dataclass
class NormalizationConfig:
    """Configuração de normalização."""
    remove_accents: bool = True
    lowercase: bool = True
    collapse_whitespace: bool = True
    remove_punctuation: bool = True
    keep_currency_symbols: bool = True  # Manter €$%
    keep_numbers: bool = True
    min_word_length: int = 1  # Palavras menores são removidas

    # OCR tolerance
    ocr_substitutions: bool = True  # 0→o, 1→l, etc.

    @classmethod
    def default(cls) -> 'NormalizationConfig':
        """Configuração padrão."""
        return cls()

    @classmethod
    def strict(cls) -> 'NormalizationConfig':
        """Configuração estrita (menos tolerante)."""
        return cls(
            ocr_substitutions=False,
            min_word_length=2,
        )

    @classmethod
    def ocr_tolerant(cls) -> 'NormalizationConfig':
        """Configuração tolerante para OCR ruidoso."""
        return cls(
            ocr_substitutions=True,
            min_word_length=1,
            remove_punctuation=True,
        )


# ============================================================================
# MAPEAMENTO OCR
# ============================================================================

# Substituições comuns de OCR
OCR_SUBSTITUTIONS = {
    '0': 'o',
    '1': 'l',
    '|': 'l',
    '!': 'i',
    '5': 's',
    '8': 'b',
    '@': 'a',
    '3': 'e',
    '4': 'a',
    '7': 't',
    '(': 'c',
    ')': 'j',
}

# Caracteres de moeda a preservar
CURRENCY_CHARS = set('€$%£¥')


# ============================================================================
# RESULTADO COM DEBUG
# ============================================================================

@dataclass
class NormalizationResult:
    """
    Resultado de normalização com informação de debug.
    """
    raw: str
    normalized: str
    words: Set[str]
    config_used: str  # "default", "strict", "ocr_tolerant", "custom"
    transformations_applied: int

    def __str__(self) -> str:
        return self.normalized

    def to_dict(self) -> dict:
        return {
            "raw": self.raw[:200] if self.raw else None,
            "normalized": self.normalized[:200] if self.normalized else None,
            "word_count": len(self.words),
            "config": self.config_used,
            "transformations": self.transformations_applied,
        }


# ============================================================================
# FUNÇÕES PRINCIPAIS
# ============================================================================

def normalize_for_matching(
    text: str,
    config: Optional[NormalizationConfig] = None,
    return_debug: bool = False
) -> str | NormalizationResult:
    """
    Normaliza texto para comparação/matching.

    Esta é a ÚNICA função de normalização a usar em todo o pipeline.

    Args:
        text: Texto a normalizar
        config: Configuração de normalização (default se None)
        return_debug: Se True, retorna NormalizationResult com debug info

    Returns:
        str normalizado ou NormalizationResult se return_debug=True
    """
    if config is None:
        config = NormalizationConfig.default()

    if not text:
        if return_debug:
            return NormalizationResult(
                raw="",
                normalized="",
                words=set(),
                config_used="default",
                transformations_applied=0,
            )
        return ""

    transformations = 0
    original = text

    # 1. Normalização Unicode (NFD decompõe acentos)
    if config.remove_accents:
        text = unicodedata.normalize("NFD", text)
        # Remover diacríticos (categoria Mn = Mark, Nonspacing)
        new_text = "".join(c for c in text if unicodedata.category(c) != "Mn")
        if new_text != text:
            transformations += 1
        text = new_text

    # 2. Substituições OCR
    if config.ocr_substitutions:
        chars = list(text)
        for i, c in enumerate(chars):
            if c in OCR_SUBSTITUTIONS:
                # Só substituir se não for dígito em contexto numérico
                # (ex: "€850" não deve virar "€bso")
                prev_is_digit = i > 0 and chars[i-1].isdigit()
                next_is_digit = i < len(chars)-1 and chars[i+1].isdigit()

                if not (prev_is_digit or next_is_digit):
                    chars[i] = OCR_SUBSTITUTIONS[c]
                    transformations += 1
        text = "".join(chars)

    # 3. Lowercase
    if config.lowercase:
        new_text = text.lower()
        if new_text != text:
            transformations += 1
        text = new_text

    # 4. Colapsar whitespace
    if config.collapse_whitespace:
        new_text = re.sub(r'\s+', ' ', text)
        if new_text != text:
            transformations += 1
        text = new_text

    # 5. Remover pontuação (mantendo símbolos de moeda se configurado)
    if config.remove_punctuation:
        if config.keep_currency_symbols:
            # Manter letras, números, espaços e símbolos de moeda
            pattern = r'[^\w\s€$%£¥]'
        else:
            pattern = r'[^\w\s]'

        new_text = re.sub(pattern, '', text)
        if new_text != text:
            transformations += 1
        text = new_text

    # 6. Strip final
    text = text.strip()

    # 7. Extrair palavras (para debug e matching por palavras)
    words = set(text.split())

    # Filtrar palavras por tamanho mínimo
    if config.min_word_length > 1:
        words = {w for w in words if len(w) >= config.min_word_length}

    if return_debug:
        config_name = "default"
        if config.ocr_substitutions and config.min_word_length == 1:
            config_name = "ocr_tolerant"
        elif not config.ocr_substitutions and config.min_word_length > 1:
            config_name = "strict"

        return NormalizationResult(
            raw=original,
            normalized=text,
            words=words,
            config_used=config_name,
            transformations_applied=transformations,
        )

    return text


def text_similarity_normalized(text1: str, text2: str, config: Optional[NormalizationConfig] = None) -> float:
    """
    Calcula similaridade Jaccard entre dois textos normalizados.

    Args:
        text1: Primeiro texto
        text2: Segundo texto
        config: Configuração de normalização

    Returns:
        float: Similaridade entre 0.0 e 1.0
    """
    if not text1 or not text2:
        return 0.0

    result1 = normalize_for_matching(text1, config, return_debug=True)
    result2 = normalize_for_matching(text2, config, return_debug=True)

    words1 = result1.words
    words2 = result2.words

    if not words1 or not words2:
        return 0.0

    intersection = words1 & words2
    union = words1 | words2

    return len(intersection) / len(union) if union else 0.0


def text_contains_normalized(
    haystack: str,
    needle: str,
    threshold: float = 0.7,
    config: Optional[NormalizationConfig] = None,
    return_debug: bool = False
) -> bool | Tuple[bool, dict]:
    """
    Verifica se haystack contém needle (com tolerância).

    Args:
        haystack: Texto maior (documento)
        needle: Texto a procurar (excerpt)
        threshold: Similaridade mínima para considerar match
        config: Configuração de normalização
        return_debug: Se True, retorna tuple (result, debug_info)

    Returns:
        bool ou (bool, dict) se return_debug=True
    """
    debug_info = {
        "method": None,
        "haystack_normalized": None,
        "needle_normalized": None,
        "match_ratio": 0.0,
    }

    if not haystack or not needle:
        if return_debug:
            return False, debug_info
        return False

    # Normalizar ambos
    if config is None:
        config = NormalizationConfig.ocr_tolerant()

    norm_haystack = normalize_for_matching(haystack, config, return_debug=True)
    norm_needle = normalize_for_matching(needle, config, return_debug=True)

    debug_info["haystack_normalized"] = norm_haystack.normalized[:100]
    debug_info["needle_normalized"] = norm_needle.normalized[:100]

    # Método 1: Contenção direta
    if norm_needle.normalized in norm_haystack.normalized:
        debug_info["method"] = "direct_containment"
        debug_info["match_ratio"] = 1.0
        if return_debug:
            return True, debug_info
        return True

    # Método 2: Todas as palavras do needle estão no haystack
    if norm_needle.words and norm_needle.words.issubset(norm_haystack.words):
        debug_info["method"] = "word_subset"
        debug_info["match_ratio"] = 1.0
        if return_debug:
            return True, debug_info
        return True

    # Método 3: Threshold de palavras em comum
    if norm_needle.words and norm_haystack.words:
        intersection = norm_needle.words & norm_haystack.words
        ratio = len(intersection) / len(norm_needle.words)
        debug_info["match_ratio"] = ratio

        if ratio >= threshold:
            debug_info["method"] = f"word_overlap_{ratio:.2f}"
            if return_debug:
                return True, debug_info
            return True

    debug_info["method"] = "no_match"
    if return_debug:
        return False, debug_info
    return False


# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def normalize_excerpt_for_debug(excerpt: str, actual_text: str) -> dict:
    """
    Compara excerpt com texto actual e retorna debug info.

    Args:
        excerpt: Excerpt esperado
        actual_text: Texto actual do documento

    Returns:
        Dict com informação de debug
    """
    config = NormalizationConfig.ocr_tolerant()

    norm_excerpt = normalize_for_matching(excerpt, config, return_debug=True)
    norm_actual = normalize_for_matching(actual_text, config, return_debug=True)

    match, match_debug = text_contains_normalized(
        actual_text, excerpt, threshold=0.6, config=config, return_debug=True
    )

    return {
        "excerpt": {
            "raw": excerpt[:200] if excerpt else None,
            "normalized": norm_excerpt.normalized[:200] if norm_excerpt.normalized else None,
            "word_count": len(norm_excerpt.words),
        },
        "actual": {
            "raw": actual_text[:200] if actual_text else None,
            "normalized": norm_actual.normalized[:200] if norm_actual.normalized else None,
            "word_count": len(norm_actual.words),
        },
        "match": match,
        "match_method": match_debug.get("method"),
        "match_ratio": match_debug.get("match_ratio"),
        "common_words": list(norm_excerpt.words & norm_actual.words)[:20],
        "missing_words": list(norm_excerpt.words - norm_actual.words)[:20],
    }


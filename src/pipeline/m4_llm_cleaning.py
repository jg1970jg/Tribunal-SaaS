# ============================================================================
# Pipeline v4.2 — M4: Limpeza de Texto via LLM
# ============================================================================
# Usa um LLM rápido e barato (Haiku) para corrigir artefactos OCR:
# - Palavras partidas ("con tra to" → "contrato")
# - Caracteres corrompidos ("Art1g0" → "Artigo")
# - Espaçamento errado
# - Artefactos de encoding
#
# REGRA CRÍTICA: Só limpeza, ZERO interpretação/resumo/paráfrase.
# O LLM NÃO pode adicionar conteúdo que não existe no texto OCR.
# ============================================================================

import difflib
import logging
from dataclasses import dataclass, field
from typing import Optional

from src.config import V42_CLEANING_MODEL

logger = logging.getLogger(__name__)

CLEANING_SYSTEM_PROMPT = """Você é uma ferramenta de correcção de OCR para documentos jurídicos portugueses.

REGRAS ABSOLUTAS:
1. Corrija APENAS erros óbvios de OCR (palavras partidas, caracteres corrompidos, espaçamento errado)
2. NÃO interprete, NÃO resuma, NÃO parafraseie
3. NÃO adicione texto que não existe no original
4. NÃO remova texto existente (excepto artefactos óbvios como "|||" ou "###")
5. Mantenha TODA a pontuação original (vírgulas, pontos, parênteses)
6. Mantenha TODOS os números exactamente como estão
7. Mantenha TODAS as datas, valores monetários, e referências legais exactamente como estão
8. Se não tem certeza se é erro OCR ou texto intencional, NÃO altere

EXEMPLOS de correcções aceitáveis:
- "con tra to" → "contrato"
- "Art1g0 5§" → "Artigo 5.º"
- "0 tribunal" → "O tribunal" (zero → O maiúsculo quando claro do contexto)
- "ré u" → "réu"
- "sen ten ça" → "sentença"
- "n.°" → "n.º"

EXEMPLOS de alterações PROIBIDAS:
- Mudar "o réu" para "o arguido" (sinónimo, mas altera significado)
- Adicionar palavras em falta que o OCR não detectou
- Reorganizar frases

Responda APENAS com o texto corrigido. Nada mais."""


CLEANING_USER_PROMPT_TEMPLATE = """Corrija os erros de OCR neste texto jurídico português. Responda APENAS com o texto corrigido:

---
{text}
---"""


@dataclass
class TextChange:
    """Uma alteração feita pela limpeza LLM."""
    original: str
    replacement: str
    line_num: int
    change_type: str   # "fix_word", "fix_spacing", "fix_encoding", "fix_punctuation"


@dataclass
class CleaningResult:
    """Resultado da limpeza de uma página."""
    page_num: int
    original_text: str
    cleaned_text: str
    changes: list[TextChange] = field(default_factory=list)
    model_used: str = ""
    tokens_used: int = 0
    was_cleaned: bool = False


def clean_ocr_pages(
    ocr_pages: list,  # list[OCRPageResult] from m3_ocr_engine
    model: Optional[str] = None,
    max_chars_per_call: int = 8000,
) -> list[CleaningResult]:
    """
    M4: Limpar texto OCR de todas as páginas.

    Usa LLM rápido para corrigir artefactos OCR.
    Processa cada página individualmente para manter mapeamento.

    Args:
        ocr_pages: páginas com texto OCR do M3
        model: modelo LLM a usar (default: V42_CLEANING_MODEL)
        max_chars_per_call: limite de caracteres por chamada LLM

    Returns:
        Lista de CleaningResult com texto limpo e diff
    """
    from src.llm_client import call_llm

    cleaning_model = model or V42_CLEANING_MODEL
    results = []

    logger.info(f"[M4] Limpeza LLM: {len(ocr_pages)} páginas com {cleaning_model}")

    for page in ocr_pages:
        original_text = page.consensus_text

        # Skip páginas vazias ou muito curtas
        if not original_text or len(original_text.strip()) < 10:
            results.append(CleaningResult(
                page_num=page.page_num,
                original_text=original_text,
                cleaned_text=original_text,
                was_cleaned=False,
            ))
            continue

        # Se texto é muito longo, dividir em partes
        if len(original_text) > max_chars_per_call:
            cleaned_text, changes, tokens = _clean_long_text(
                original_text, cleaning_model, max_chars_per_call
            )
        else:
            cleaned_text, changes, tokens = _clean_text(original_text, cleaning_model)

        results.append(CleaningResult(
            page_num=page.page_num,
            original_text=original_text,
            cleaned_text=cleaned_text,
            changes=changes,
            model_used=cleaning_model,
            tokens_used=tokens,
            was_cleaned=len(changes) > 0,
        ))

        if changes:
            logger.info(f"[M4] Página {page.page_num}: {len(changes)} correcções")

    total_changes = sum(len(r.changes) for r in results)
    total_tokens = sum(r.tokens_used for r in results)
    logger.info(f"[M4] Limpeza concluída: {total_changes} correcções, {total_tokens} tokens")

    return results


def _clean_text(text: str, model: str) -> tuple[str, list[TextChange], int]:
    """Limpar um bloco de texto via LLM."""
    from src.llm_client import call_llm

    prompt = CLEANING_USER_PROMPT_TEMPLATE.format(text=text)

    try:
        response = call_llm(
            model=model,
            prompt=prompt,
            system_prompt=CLEANING_SYSTEM_PROMPT,
            temperature=0.1,  # Baixa temperatura para limpeza determinística
            max_tokens=len(text) * 2,  # Margem para output
        )

        cleaned = response.content.strip()

        # Validação: texto limpo não deve ser drasticamente diferente
        if not cleaned:
            logger.warning("[M4] LLM retornou texto vazio, a usar original")
            return text, [], response.tokens_used

        # Se texto limpo é muito mais curto, provavelmente o LLM resumiu
        if len(cleaned) < len(text) * 0.5:
            logger.warning(
                f"[M4] Texto limpo muito curto ({len(cleaned)} vs {len(text)}), "
                f"possível resumo — a usar original"
            )
            return text, [], response.tokens_used

        # Se texto limpo é muito mais longo, LLM adicionou conteúdo
        if len(cleaned) > len(text) * 1.5:
            logger.warning(
                f"[M4] Texto limpo muito longo ({len(cleaned)} vs {len(text)}), "
                f"possível adição — a usar original"
            )
            return text, [], response.tokens_used

        # Calcular diff
        changes = _compute_diff(text, cleaned)

        return cleaned, changes, response.tokens_used

    except Exception as e:
        logger.error(f"[M4] Erro na limpeza LLM: {e}")
        return text, [], 0


def _clean_long_text(
    text: str,
    model: str,
    max_chars: int,
) -> tuple[str, list[TextChange], int]:
    """Limpar texto longo em partes."""
    parts = []
    all_changes = []
    total_tokens = 0

    # Dividir em parágrafos
    paragraphs = text.split("\n\n")
    current_part = ""

    for para in paragraphs:
        if len(current_part) + len(para) + 2 > max_chars and current_part:
            parts.append(current_part)
            current_part = para
        else:
            current_part = current_part + "\n\n" + para if current_part else para

    if current_part:
        parts.append(current_part)

    # Limpar cada parte
    cleaned_parts = []
    for part in parts:
        cleaned, changes, tokens = _clean_text(part, model)
        cleaned_parts.append(cleaned)
        all_changes.extend(changes)
        total_tokens += tokens

    return "\n\n".join(cleaned_parts), all_changes, total_tokens


def _compute_diff(original: str, cleaned: str) -> list[TextChange]:
    """Calcular diferenças entre texto original e limpo."""
    changes = []

    orig_lines = original.splitlines()
    clean_lines = cleaned.splitlines()

    matcher = difflib.SequenceMatcher(None, orig_lines, clean_lines)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag == "replace":
            for k, (orig_line, clean_line) in enumerate(
                zip(orig_lines[i1:i2], clean_lines[j1:j2])
            ):
                if orig_line != clean_line:
                    change_type = _classify_change(orig_line, clean_line)
                    changes.append(TextChange(
                        original=orig_line,
                        replacement=clean_line,
                        line_num=i1 + k + 1,
                        change_type=change_type,
                    ))

        elif tag == "delete":
            for k, line in enumerate(orig_lines[i1:i2]):
                changes.append(TextChange(
                    original=line,
                    replacement="",
                    line_num=i1 + k + 1,
                    change_type="removed",
                ))

        elif tag == "insert":
            for line in clean_lines[j1:j2]:
                changes.append(TextChange(
                    original="",
                    replacement=line,
                    line_num=i1 + 1,
                    change_type="inserted",
                ))

    return changes


def _classify_change(original: str, cleaned: str) -> str:
    """Classificar o tipo de alteração."""
    # Apenas espaçamento mudou
    if original.replace(" ", "") == cleaned.replace(" ", ""):
        return "fix_spacing"

    # Apenas pontuação mudou
    orig_alpha = "".join(c for c in original if c.isalnum())
    clean_alpha = "".join(c for c in cleaned if c.isalnum())
    if orig_alpha == clean_alpha:
        return "fix_punctuation"

    # Caracteres numéricos idênticos (possível fix de encoding)
    orig_nums = "".join(c for c in original if c.isdigit())
    clean_nums = "".join(c for c in cleaned if c.isdigit())
    if orig_nums == clean_nums and orig_alpha != clean_alpha:
        return "fix_encoding"

    return "fix_word"

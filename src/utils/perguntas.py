"""Utilidades para parsing e validação de perguntas do utilizador."""

import logging

logger = logging.getLogger(__name__)


def parse_perguntas(raw: str) -> list[str]:
    """
    Parse robusto de perguntas com separador ---.

    Args:
        raw: Texto bruto com perguntas (pode ter múltiplas linhas)

    Returns:
        Lista de perguntas (strings)
    """
    if not raw or not raw.strip():
        return []

    # Normalizar line endings (Windows/Mac/Linux)
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')

    # Split por linhas
    linhas = raw.split('\n')

    # Agrupar em blocos separados por ---
    blocos = []
    bloco_atual = []

    for linha in linhas:
        # Separador = linha com só "---" (aceita espaços e variações)
        linha_strip = linha.strip()
        if linha_strip in ['---', '—', '___', '- - -', '– – –']:
            if bloco_atual:
                texto = '\n'.join(bloco_atual).strip()
                if texto:  # Ignora blocos vazios
                    blocos.append(texto)
                bloco_atual = []
        else:
            bloco_atual.append(linha)

    # Adicionar último bloco
    if bloco_atual:
        texto = '\n'.join(bloco_atual).strip()
        if texto:
            blocos.append(texto)

    logger.info(f"Parsed {len(blocos)} perguntas do texto bruto")
    return blocos


def validar_perguntas(perguntas: list[str]) -> tuple[bool, str]:
    """
    Valida perguntas contra limites configurados.

    Args:
        perguntas: Lista de perguntas

    Returns:
        (pode_continuar: bool, mensagem: str)
    """
    from src.config import (
        MAX_PERGUNTAS_WARN,
        MAX_PERGUNTAS_HARD,
        MAX_CHARS_PERGUNTA_WARN,
        MAX_CHARS_PERGUNTA_HARD,
        MAX_CHARS_TOTAL_PERGUNTAS_HARD
    )

    if not perguntas:
        return True, "Sem perguntas"

    n_perguntas = len(perguntas)
    chars_total = sum(len(p) for p in perguntas)
    chars_max = max(len(p) for p in perguntas)

    # HARD limits (bloqueiam)
    if n_perguntas > MAX_PERGUNTAS_HARD:
        return False, f"Máximo {MAX_PERGUNTAS_HARD} perguntas (tem {n_perguntas})"

    if chars_max > MAX_CHARS_PERGUNTA_HARD:
        return False, f"Pergunta muito longa: {chars_max:,} chars (máx {MAX_CHARS_PERGUNTA_HARD:,})"

    if chars_total > MAX_CHARS_TOTAL_PERGUNTAS_HARD:
        return False, f"Total muito grande: {chars_total:,} chars (máx {MAX_CHARS_TOTAL_PERGUNTAS_HARD:,})"

    # WARN limits (avisam mas não bloqueiam)
    avisos = []

    if n_perguntas > MAX_PERGUNTAS_WARN:
        avisos.append(f"{n_perguntas} perguntas (recomendado max {MAX_PERGUNTAS_WARN})")

    if chars_max > MAX_CHARS_PERGUNTA_WARN:
        avisos.append(f"Pergunta com {chars_max:,} chars (recomendado max {MAX_CHARS_PERGUNTA_WARN:,})")

    if avisos:
        return True, "AVISO: " + " | ".join(avisos)

    return True, f"{n_perguntas} pergunta(s) válida(s)"

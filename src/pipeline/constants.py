# -*- coding: utf-8 -*-
"""
Constantes partilhadas do sistema TRIBUNAL SAAS.

Centraliza definicoes para evitar divergencias entre modulos.
"""

# Estados de paginas - bloqueantes (requerem atencao)
ESTADOS_BLOQUEANTES = ["SUSPEITA", "SEM_TEXTO", "NAO_COBERTA", "VISUAL_PENDING"]

# Estados de paginas - resolvidos (utilizador ja tratou)
ESTADOS_RESOLVIDOS = ["VISUAL_ONLY", "REPARADA"]

# Flags bloqueantes do detetor intra-pagina
FLAGS_BLOQUEANTES = [
    "SUSPEITA_DATA_NAO_EXTRAIDO",
    "SUSPEITA_VALOR_NAO_EXTRAIDO",
    "SUSPEITA_REF_LEGAL_NAO_EXTRAIDO",
    "COBERTURA_NAO_COBERTA",
    "COBERTURA_PARCIAL",
]

# Tipos de override validos
OVERRIDE_TYPES = ["visual_only", "manual_transcription", "upload_substituto", "upload"]


# =================================================================
# HELPERS - REGRAS DE PAGINAS
# =================================================================

def is_resolvida(page) -> bool:
    """
    Verifica se pagina foi explicitamente resolvida pelo utilizador.

    Uma pagina e considerada resolvida se:
    - Status final e VISUAL_ONLY ou REPARADA (resolucao por estado), OU
    - Tem override_type valido (resolucao por acao de reparacao)

    Args:
        page: Objeto PageRecord

    Returns:
        True se pagina foi resolvida, False caso contrario
    """
    # Verificar status resolvido
    status_resolvido = getattr(page, 'status_final', None) in ESTADOS_RESOLVIDOS

    # Verificar override aplicado
    override_type = getattr(page, 'override_type', None)
    tem_override = override_type in OVERRIDE_TYPES if override_type else False

    return status_resolvido or tem_override


def has_flags_bloqueantes(page) -> bool:
    """
    Verifica se pagina tem flags bloqueantes do detetor intra-pagina.

    Args:
        page: Objeto PageRecord

    Returns:
        True se tem flags bloqueantes, False caso contrario
    """
    if not hasattr(page, 'flags') or not page.flags:
        return False

    return any(flag in FLAGS_BLOQUEANTES for flag in page.flags)


def precisa_reparacao(page) -> bool:
    """
    Verifica se pagina precisa de reparacao (bloqueia analise).

    Uma pagina precisa de reparacao se:
    - Tem estado bloqueante (SUSPEITA, SEM_TEXTO, NAO_COBERTA), OU
    - Tem flags bloqueantes E NAO foi resolvida

    Args:
        page: Objeto PageRecord

    Returns:
        True se precisa reparacao, False caso contrario
    """
    # Se ja foi resolvida, nao precisa
    if is_resolvida(page):
        return False

    # Verificar estado bloqueante
    tem_estado_bloqueante = getattr(page, 'status_final', None) in ESTADOS_BLOQUEANTES

    # Verificar flags bloqueantes
    tem_flags = has_flags_bloqueantes(page)

    return tem_estado_bloqueante or tem_flags

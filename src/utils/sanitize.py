"""
SANITIZAÇÃO DE INPUTS - Prevenção de Path Traversal
=====================================================
Funções centralizadas para sanitizar run_id, filenames e outros
inputs que são usados em caminhos de ficheiros.
"""

import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Formato esperado de run_id: YYYYMMDD_HHMMSS_hex8 (ex: 20260203_154057_891a6226)
# Também aceita variações com prefixos como "pdf_safe_" ou UUIDs puros
_RUN_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_\-]{1,128}$')

# Caracteres proibidos em nomes de ficheiros (path traversal + especiais)
_UNSAFE_FILENAME_CHARS = re.compile(r'[/\\<>:"|?*\x00-\x1f]')


def sanitize_run_id(run_id: str) -> str:
    """
    Valida e sanitiza um run_id para uso seguro em caminhos de ficheiros.

    Rejeita qualquer run_id que contenha:
      - Sequências de path traversal (../, ..\\)
      - Caracteres de separador de caminho (/, \\)
      - Caracteres nulos
      - Strings vazias ou muito longas

    Args:
        run_id: O run_id a sanitizar

    Returns:
        O run_id sanitizado (inalterado se válido)

    Raises:
        ValueError: Se o run_id contém padrões perigosos
    """
    if not run_id or not isinstance(run_id, str):
        raise ValueError("run_id não pode ser vazio")

    run_id = run_id.strip()

    if not run_id:
        raise ValueError("run_id não pode ser vazio")

    # Rejeitar path traversal explícito
    if '..' in run_id:
        logger.warning(f"[SECURITY] Path traversal detectado em run_id: {run_id!r}")
        raise ValueError("run_id inválido: contém '..'")

    # Rejeitar separadores de caminho
    if '/' in run_id or '\\' in run_id:
        logger.warning(f"[SECURITY] Separador de caminho detectado em run_id: {run_id!r}")
        raise ValueError("run_id inválido: contém separador de caminho")

    # Rejeitar caracteres nulos
    if '\x00' in run_id:
        logger.warning("[SECURITY] Null byte detectado em run_id")
        raise ValueError("run_id inválido: contém null byte")

    # Validar formato (alfanumérico + _ + -)
    if not _RUN_ID_PATTERN.match(run_id):
        logger.warning(f"[SECURITY] run_id com formato inválido: {run_id!r}")
        raise ValueError("run_id inválido: formato não reconhecido")

    return run_id


def sanitize_filename(filename: str) -> str:
    """
    Sanitiza um nome de ficheiro para uso seguro.

    Remove componentes de caminho (diretórios) e caracteres perigosos.

    Args:
        filename: Nome do ficheiro a sanitizar

    Returns:
        Nome do ficheiro sanitizado (apenas o nome base, sem diretórios)

    Raises:
        ValueError: Se o filename resulta vazio após sanitização
    """
    if not filename or not isinstance(filename, str):
        raise ValueError("filename não pode ser vazio")

    # Extrair apenas o nome base (remove qualquer diretório)
    safe_name = Path(filename).name

    # Remover caracteres perigosos
    safe_name = _UNSAFE_FILENAME_CHARS.sub('_', safe_name)

    # Remover pontos iniciais (ficheiros ocultos)
    safe_name = safe_name.lstrip('.')

    # Limitar tamanho
    if len(safe_name) > 255:
        # Preservar extensão
        stem = Path(safe_name).stem[:200]
        suffix = Path(safe_name).suffix
        safe_name = stem + suffix

    if not safe_name:
        raise ValueError("filename inválido após sanitização")

    return safe_name


def safe_join_path(base_dir: Path, *parts: str) -> Path:
    """
    Junta caminhos de forma segura, verificando que o resultado
    está dentro do diretório base (prevenção de path traversal).

    Args:
        base_dir: Diretório base (confiável)
        *parts: Partes do caminho a juntar (potencialmente não confiáveis)

    Returns:
        Caminho seguro dentro de base_dir

    Raises:
        ValueError: Se o caminho resultante sai de base_dir
    """
    result = base_dir
    for part in parts:
        result = result / part

    # Resolver caminhos para verificar que não escapam do base_dir
    try:
        resolved = result.resolve()
        base_resolved = base_dir.resolve()

        if not str(resolved).startswith(str(base_resolved)):
            logger.warning(
                f"[SECURITY] Path traversal detectado: "
                f"resultado={resolved} fora de base={base_resolved}"
            )
            raise ValueError("Caminho resultante fora do diretório base")
    except OSError:
        # Em Windows, resolve() pode falhar para caminhos que não existem
        # Verificar textualmente como fallback
        result_str = str(result).replace("\\", "/")
        base_str = str(base_dir).replace("\\", "/")
        if not result_str.startswith(base_str):
            logger.warning(
                f"[SECURITY] Path traversal detectado (textual check): "
                f"resultado={result_str} fora de base={base_str}"
            )
            raise ValueError("Caminho resultante fora do diretório base")

    return result

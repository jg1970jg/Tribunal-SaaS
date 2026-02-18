# -*- coding: utf-8 -*-
"""
TRIBUNAL SAAS - Utilitário de Limpeza
============================================================
Remove pastas temporárias antigas (outputs/temp_*) de forma segura.
Não apaga runs válidos.
============================================================
"""

import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)


def get_temp_folders(output_dir: Path) -> List[Path]:
    """
    Lista todas as pastas temporárias em outputs/.

    Args:
        output_dir: Diretório de outputs

    Returns:
        Lista de paths para pastas temp_*
    """
    if not output_dir.exists():
        return []

    temp_folders = []
    for item in output_dir.iterdir():
        if item.is_dir() and item.name.startswith("temp_"):
            temp_folders.append(item)

    return sorted(temp_folders, key=lambda p: p.stat().st_mtime)


def get_folder_age(folder: Path) -> timedelta:
    """
    Retorna idade de uma pasta.

    Args:
        folder: Path da pasta

    Returns:
        timedelta com a idade
    """
    mtime = datetime.fromtimestamp(folder.stat().st_mtime)
    return datetime.now() - mtime


def get_folder_size(folder: Path) -> int:
    """
    Calcula tamanho total de uma pasta em bytes.

    Args:
        folder: Path da pasta

    Returns:
        Tamanho em bytes
    """
    total = 0
    for item in folder.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def format_size(size_bytes: int) -> str:
    """Formata tamanho em bytes para leitura humana."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def is_valid_run(folder: Path) -> bool:
    """
    Verifica se uma pasta é um run válido (não deve ser apagada).

    Um run válido tem:
    - resultado.json
    - Não começa com temp_

    Args:
        folder: Path da pasta

    Returns:
        True se for run válido
    """
    if folder.name.startswith("temp_"):
        return False

    resultado_path = folder / "resultado.json"
    return resultado_path.exists()


def cleanup_temp_folders(
    output_dir: Path,
    max_age_hours: int = 24,
    dry_run: bool = True,
) -> Tuple[int, int, List[str]]:
    """
    Remove pastas temporárias antigas.

    Args:
        output_dir: Diretório de outputs
        max_age_hours: Idade máxima em horas (pastas mais velhas são removidas)
        dry_run: Se True, apenas lista sem remover

    Returns:
        Tuple (pastas_removidas, bytes_libertados, lista_de_msgs)
    """
    temp_folders = get_temp_folders(output_dir)
    messages = []
    removed = 0
    bytes_freed = 0

    if not temp_folders:
        messages.append("Nenhuma pasta temp_* encontrada.")
        return 0, 0, messages

    messages.append(f"Encontradas {len(temp_folders)} pasta(s) temp_*")
    messages.append(f"Max age: {max_age_hours} horas | Dry run: {dry_run}")
    messages.append("-" * 50)

    max_age = timedelta(hours=max_age_hours)

    for folder in temp_folders:
        age = get_folder_age(folder)
        size = get_folder_size(folder)
        size_str = format_size(size)
        age_str = f"{age.total_seconds() / 3600:.1f}h"

        if age > max_age:
            if dry_run:
                messages.append(f"[DRY] Remover: {folder.name} ({size_str}, {age_str})")
            else:
                try:
                    shutil.rmtree(folder)
                    messages.append(f"[OK] Removido: {folder.name} ({size_str}, {age_str})")
                    removed += 1
                    bytes_freed += size
                except Exception as e:
                    messages.append(f"[ERRO] Falha ao remover {folder.name}: {e}")
                    logger.error(f"Erro ao remover {folder}: {e}")
        else:
            messages.append(f"[SKIP] Manter: {folder.name} ({size_str}, {age_str} < {max_age_hours}h)")

    messages.append("-" * 50)
    if dry_run:
        messages.append(f"Dry run: {len([f for f in temp_folders if get_folder_age(f) > max_age])} pasta(s) seriam removidas")
    else:
        messages.append(f"Removidas: {removed} pasta(s), {format_size(bytes_freed)} libertados")

    return removed, bytes_freed, messages


def cleanup_all_temp_folders(
    output_dir: Path,
    dry_run: bool = True,
) -> Tuple[int, int, List[str]]:
    """
    Remove TODAS as pastas temp_* (independente da idade).

    Args:
        output_dir: Diretório de outputs
        dry_run: Se True, apenas lista sem remover

    Returns:
        Tuple (pastas_removidas, bytes_libertados, lista_de_msgs)
    """
    return cleanup_temp_folders(output_dir, max_age_hours=0, dry_run=dry_run)


def get_cleanup_stats(output_dir: Path) -> dict:
    """
    Retorna estatísticas para cleanup.

    Args:
        output_dir: Diretório de outputs

    Returns:
        Dict com estatísticas
    """
    temp_folders = get_temp_folders(output_dir)

    if not temp_folders:
        return {
            "temp_count": 0,
            "temp_size_bytes": 0,
            "temp_size_str": "0 B",
            "oldest_age_hours": 0,
            "newest_age_hours": 0,
        }

    total_size = sum(get_folder_size(f) for f in temp_folders)
    ages = [get_folder_age(f).total_seconds() / 3600 for f in temp_folders]

    return {
        "temp_count": len(temp_folders),
        "temp_size_bytes": total_size,
        "temp_size_str": format_size(total_size),
        "oldest_age_hours": max(ages) if ages else 0,
        "newest_age_hours": min(ages) if ages else 0,
    }


# ============================================================
# CLI Interface
# ============================================================
def main():
    """CLI para cleanup."""
    import argparse
    import sys

    # Adicionar diretório raiz ao path
    root_dir = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(root_dir))

    from src.config import OUTPUT_DIR

    parser = argparse.ArgumentParser(
        description="Limpa pastas temporárias do Tribunal SaaS"
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=24,
        help="Idade máxima em horas (default: 24)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Remove TODAS as pastas temp_* (ignora --max-age)"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Executa remoção (sem esta flag, apenas mostra o que seria removido)"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("TRIBUNAL SAAS - Cleanup de Pastas Temporárias")
    print("=" * 60)
    print(f"Output dir: {OUTPUT_DIR}")
    print()

    # Mostrar stats
    stats = get_cleanup_stats(OUTPUT_DIR)
    print(f"Pastas temp_*: {stats['temp_count']}")
    print(f"Tamanho total: {stats['temp_size_str']}")
    if stats['temp_count'] > 0:
        print(f"Mais antiga: {stats['oldest_age_hours']:.1f}h")
        print(f"Mais recente: {stats['newest_age_hours']:.1f}h")
    print()

    dry_run = not args.execute

    if args.all:
        removed, freed, messages = cleanup_all_temp_folders(OUTPUT_DIR, dry_run=dry_run)
    else:
        removed, freed, messages = cleanup_temp_folders(OUTPUT_DIR, args.max_age, dry_run=dry_run)

    for msg in messages:
        print(msg)

    if dry_run:
        print()
        print("NOTA: Este foi um dry run. Use --execute para remover realmente.")


if __name__ == "__main__":
    main()

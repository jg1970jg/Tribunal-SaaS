# -*- coding: utf-8 -*-
"""
GESTÃO DE METADATA - Títulos e Descrições de Análises
═══════════════════════════════════════════════════════════════════════════

Permite dar títulos amigáveis às análises em vez de códigos.

FUNCIONALIDADES:
- Guardar/carregar metadata.json
- Listar análises com títulos
- Editar títulos de análises antigas
- Compatibilidade com análises sem metadata
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.utils.sanitize import sanitize_run_id

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# FUNÇÕES DE METADATA
# ═══════════════════════════════════════════════════════════════════════════

def guardar_metadata(
    run_id: str,
    output_dir: Path,
    titulo: str,
    descricao: str = "",
    area_direito: str = "",
    num_documentos: int = 1
):
    """
    Guarda metadata de uma análise.
    
    Args:
        run_id: ID da análise (ex: 20260203_154057_891a6226)
        output_dir: Pasta outputs
        titulo: Título dado pelo utilizador
        descricao: Descrição opcional
        area_direito: Área de direito
        num_documentos: Número de documentos analisados
    """
    run_id = sanitize_run_id(run_id)
    analise_dir = output_dir / run_id

    if not analise_dir.exists():
        logger.error(f"Análise não existe: {analise_dir}")
        return

    metadata = {
        "run_id": run_id,
        "titulo": titulo,
        "descricao": descricao,
        "area_direito": area_direito,
        "num_documentos": num_documentos,
        "data_criacao": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "versao_metadata": "1.0"
    }
    
    metadata_path = analise_dir / "metadata.json"
    
    try:
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✓ Metadata guardada: {titulo}")
    
    except Exception as e:
        logger.error(f"Erro ao guardar metadata: {e}")


def carregar_metadata(run_id: str, output_dir: Path) -> Optional[Dict]:
    """
    Carrega metadata de uma análise.

    Args:
        run_id: ID da análise
        output_dir: Pasta outputs

    Returns:
        Dict com metadata ou None se não existir
    """
    run_id = sanitize_run_id(run_id)
    analise_dir = output_dir / run_id
    metadata_path = analise_dir / "metadata.json"
    
    if not metadata_path.exists():
        return None
    
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    except Exception as e:
        logger.error(f"Erro ao carregar metadata: {e}")
        return None


def atualizar_metadata(
    run_id: str,
    output_dir: Path,
    titulo: Optional[str] = None,
    descricao: Optional[str] = None
):
    """
    Atualiza metadata existente.
    
    Args:
        run_id: ID da análise
        output_dir: Pasta outputs
        titulo: Novo título (None = não alterar)
        descricao: Nova descrição (None = não alterar)
    """
    run_id = sanitize_run_id(run_id)
    metadata = carregar_metadata(run_id, output_dir)

    if metadata is None:
        # Criar metadata nova se não existir
        metadata = {
            "run_id": run_id,
            "titulo": titulo or run_id,
            "descricao": descricao or "",
            "area_direito": "",
            "num_documentos": 0,
            "data_criacao": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "versao_metadata": "1.0"
        }
    else:
        # Atualizar campos
        if titulo is not None:
            metadata["titulo"] = titulo
        if descricao is not None:
            metadata["descricao"] = descricao
        
        metadata["data_atualizacao"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Guardar
    analise_dir = output_dir / run_id
    metadata_path = analise_dir / "metadata.json"
    
    try:
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✓ Metadata atualizada: {metadata['titulo']}")
    
    except Exception as e:
        logger.error(f"Erro ao atualizar metadata: {e}")


def listar_analises_com_titulos(output_dir: Path) -> List[Tuple[str, str, str]]:
    """
    Lista todas as análises com títulos.
    
    Args:
        output_dir: Pasta outputs
    
    Returns:
        Lista de tuplos: [(run_id, titulo_display, data), ...]
        Ordenado por data (mais recente primeiro)
    """
    analises = []
    
    if not output_dir.exists():
        return []
    
    # Procurar pastas de análises
    for item in output_dir.iterdir():
        if not item.is_dir() or item.name.startswith('.') or item.name.startswith('temp'):
            continue
        
        run_id = item.name
        
        # Tentar carregar metadata
        metadata = carregar_metadata(run_id, output_dir)
        
        if metadata:
            # Tem metadata - usar título
            titulo = metadata.get('titulo', run_id)
            data = metadata.get('data_criacao', '')
            
            # Extrair data para ordenação
            try:
                data_obj = datetime.strptime(data, "%Y-%m-%d %H:%M:%S")
                data_display = data_obj.strftime("%d/%m/%Y")
            except Exception:
                data_display = run_id[:8]  # Fallback para data do run_id
                data_obj = datetime.strptime(run_id[:8], "%Y%m%d")
            
            titulo_display = f"{titulo} ({data_display})"
        
        else:
            # Sem metadata - usar run_id e extrair data
            try:
                # run_id formato: 20260203_154057_891a6226
                data_str = run_id[:8]  # 20260203
                data_obj = datetime.strptime(data_str, "%Y%m%d")
                data_display = data_obj.strftime("%d/%m/%Y")
                titulo_display = f"[Sem título] {run_id[:15]}... ({data_display})"
            except Exception:
                titulo_display = run_id
                data_obj = datetime.now()
        
        analises.append((run_id, titulo_display, data_obj))
    
    # Ordenar por data (mais recente primeiro)
    analises.sort(key=lambda x: x[2], reverse=True)
    
    # Retornar só run_id e titulo_display
    return [(run_id, titulo_display, data_obj.strftime("%Y-%m-%d")) for run_id, titulo_display, data_obj in analises]


def gerar_titulo_automatico(documento_filename: str, area_direito: str = "") -> str:
    """
    Gera título automático baseado no nome do ficheiro.
    
    Args:
        documento_filename: Nome do ficheiro
        area_direito: Área de direito (opcional)
    
    Returns:
        Título sugerido
    """
    # Remover extensão
    nome = Path(documento_filename).stem
    
    # Limpar underscores e hífens
    nome = nome.replace('_', ' ').replace('-', ' ')
    
    # Capitalizar palavras
    nome = ' '.join(word.capitalize() for word in nome.split())
    
    # Adicionar área se fornecida
    if area_direito and area_direito != "Geral":
        return f"{nome} - {area_direito}"
    
    return nome


def tem_metadata(run_id: str, output_dir: Path) -> bool:
    """
    Verifica se análise tem metadata.

    Args:
        run_id: ID da análise
        output_dir: Pasta outputs

    Returns:
        True se tem metadata.json
    """
    run_id = sanitize_run_id(run_id)
    analise_dir = output_dir / run_id
    metadata_path = analise_dir / "metadata.json"
    return metadata_path.exists()


def contar_analises_sem_titulo(output_dir: Path) -> int:
    """
    Conta quantas análises não têm metadata.
    
    Args:
        output_dir: Pasta outputs
    
    Returns:
        Número de análises sem título
    """
    if not output_dir.exists():
        return 0
    
    count = 0
    
    for item in output_dir.iterdir():
        if not item.is_dir() or item.name.startswith('.') or item.name.startswith('temp'):
            continue
        
        if not tem_metadata(item.name, output_dir):
            count += 1
    
    return count

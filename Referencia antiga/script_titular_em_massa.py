# -*- coding: utf-8 -*-
"""
SCRIPT: TITULAÇÃO EM MASSA DE ANÁLISES
═══════════════════════════════════════════════════════════════════════════

Gera títulos automáticos para TODAS as análises que não têm metadata.

FUNCIONAMENTO:
1. Varre pasta outputs/
2. Identifica análises sem metadata.json
3. Lê resultado.json de cada uma
4. Extrai: documento, área, veredicto, data
5. Gera título inteligente
6. Guarda metadata.json

EXEMPLO TÍTULO GERADO:
"Administrativo - Parcial (01/02 15:32)"
"Arrendamento - Improcedente (03/02 17:40)"
"Contrato Silva - Aprovado (29/01 21:06)"
"""

import json
from pathlib import Path
from datetime import datetime
import sys

# Adicionar src ao path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.config import OUTPUT_DIR
from src.utils.metadata_manager import guardar_metadata, tem_metadata


def extrair_info_analise(run_id: str) -> dict:
    """
    Extrai informação relevante de uma análise.
    
    Args:
        run_id: ID da análise
    
    Returns:
        Dict com: documento, area, veredicto, data
    """
    analise_dir = OUTPUT_DIR / run_id
    resultado_json = analise_dir / "resultado.json"
    
    if not resultado_json.exists():
        return None
    
    try:
        with open(resultado_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Extrair informações
        documento_nome = data.get('documento', {})
        if isinstance(documento_nome, dict):
            documento_nome = documento_nome.get('filename', 'Desconhecido')
        
        area_direito = data.get('area_direito', 'Geral')
        veredicto = data.get('simbolo_final', '')
        status = data.get('status_final', '')
        
        # Extrair data do timestamp
        timestamp_str = data.get('timestamp_inicio', '')
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
            data_formatada = timestamp.strftime("%d/%m %H:%M")
        except:
            # Fallback: usar run_id
            try:
                data_formatada = datetime.strptime(run_id[:13], "%Y%m%d_%H%M").strftime("%d/%m %H:%M")
            except:
                data_formatada = run_id[:13]
        
        return {
            'documento': documento_nome,
            'area': area_direito,
            'veredicto': veredicto,
            'status': status,
            'data': data_formatada
        }
    
    except Exception as e:
        print(f"❌ Erro ao ler {run_id}: {e}")
        return None


def gerar_titulo_inteligente(info: dict, run_id: str) -> str:
    """
    Gera título inteligente baseado nas informações.
    
    Estratégia:
    1. Se documento tem nome significativo → usa nome
    2. Senão, usa área de direito
    3. Adiciona status/veredicto se relevante
    4. Adiciona data curta
    
    Args:
        info: Dict com informações da análise
        run_id: ID da análise (fallback)
    
    Returns:
        Título gerado
    """
    # Extrair nome documento (sem extensão e limpo)
    doc_nome = info['documento']
    
    # Limpar nome documento
    if doc_nome:
        # Remover extensão
        doc_nome = doc_nome.rsplit('.', 1)[0]
        
        # Casos especiais (nomes genéricos/temporários)
        nomes_genericos = [
            'pdftcmsource',
            'combinado_',
            'temp_',
            'upload_',
            'documento',
            'file',
            'untitled'
        ]
        
        doc_nome_lower = doc_nome.lower()
        eh_generico = any(gen in doc_nome_lower for gen in nomes_genericos)
        
        if not eh_generico and len(doc_nome) > 3:
            # Nome documento é significativo
            # Limpar underscores e capitalizar
            titulo_base = doc_nome.replace('_', ' ').replace('-', ' ')
            titulo_base = ' '.join(word.capitalize() for word in titulo_base.split())
            
            # Limitar tamanho
            if len(titulo_base) > 40:
                titulo_base = titulo_base[:37] + "..."
        else:
            # Nome genérico, usar área
            titulo_base = info['area']
    else:
        titulo_base = info['area']
    
    # Adicionar status se relevante
    status_map = {
        'PROCEDENTE': 'Aprovado',
        'IMPROCEDENTE': 'Rejeitado',
        'PARCIALMENTE PROCEDENTE': 'Parcial',
        'INCONCLUSIVO': 'Inconclusivo',
        'ATENÇÃO': 'Atenção'
    }
    
    status_nome = status_map.get(info['status'], '')
    
    # Montar título
    if status_nome and status_nome != 'Aprovado':  # Não adicionar "Aprovado" (assume-se positivo)
        titulo = f"{titulo_base} - {status_nome}"
    else:
        titulo = titulo_base
    
    # Adicionar data curta (opcional, só se título muito curto)
    if len(titulo) < 25:
        titulo = f"{titulo} ({info['data']})"
    
    return titulo


def titular_em_massa(dry_run: bool = False):
    """
    Titula todas as análises que não têm metadata.
    
    Args:
        dry_run: Se True, apenas mostra o que faria sem guardar
    """
    print("\n" + "="*70)
    print("🎯 TITULAÇÃO EM MASSA DE ANÁLISES")
    print("="*70 + "\n")
    
    if not OUTPUT_DIR.exists():
        print(f"❌ Pasta outputs não encontrada: {OUTPUT_DIR}")
        return
    
    # Encontrar análises sem metadata
    analises_sem_titulo = []
    
    for item in OUTPUT_DIR.iterdir():
        if not item.is_dir() or item.name.startswith('.') or item.name.startswith('temp'):
            continue
        
        run_id = item.name
        
        if not tem_metadata(run_id, OUTPUT_DIR):
            analises_sem_titulo.append(run_id)
    
    if not analises_sem_titulo:
        print("✅ Todas as análises já têm título!")
        print("   Não há nada para fazer.\n")
        return
    
    print(f"📊 Encontradas {len(analises_sem_titulo)} análise(s) sem título\n")
    
    if dry_run:
        print("🔍 MODO DRY-RUN (simulação, não vai guardar)\n")
    
    # Processar cada análise
    tituladas = 0
    erros = 0
    
    for i, run_id in enumerate(analises_sem_titulo, 1):
        print(f"[{i}/{len(analises_sem_titulo)}] Processando: {run_id}")
        
        # Extrair informações
        info = extrair_info_analise(run_id)
        
        if not info:
            print(f"   ❌ Erro ao extrair informações\n")
            erros += 1
            continue
        
        # Gerar título
        titulo = gerar_titulo_inteligente(info, run_id)
        
        print(f"   📄 Documento: {info['documento']}")
        print(f"   📁 Área: {info['area']}")
        print(f"   {info['veredicto']} Status: {info['status']}")
        print(f"   ➡️ Título gerado: {titulo}")
        
        if not dry_run:
            # Guardar metadata
            try:
                guardar_metadata(
                    run_id=run_id,
                    output_dir=OUTPUT_DIR,
                    titulo=titulo,
                    descricao="",
                    area_direito=info['area'],
                    num_documentos=1
                )
                print(f"   ✅ Metadata guardada!\n")
                tituladas += 1
            except Exception as e:
                print(f"   ❌ Erro ao guardar: {e}\n")
                erros += 1
        else:
            print(f"   ⚠️ (não guardado - dry run)\n")
            tituladas += 1
    
    # Resumo final
    print("="*70)
    if dry_run:
        print(f"✅ SIMULAÇÃO COMPLETA!")
        print(f"   {tituladas} análise(s) seriam tituladas")
        print(f"   {erros} erro(s) encontrado(s)")
        print(f"\n💡 Execute sem --dry-run para aplicar as alterações")
    else:
        print(f"✅ TITULAÇÃO COMPLETA!")
        print(f"   {tituladas} análise(s) tituladas com sucesso")
        print(f"   {erros} erro(s) encontrado(s)")
        print(f"\n💡 Pode editar títulos manualmente em: ✏️ Gerir Títulos")
    print("="*70 + "\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Titular análises em massa")
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Simular sem guardar alterações"
    )
    
    args = parser.parse_args()
    
    titular_em_massa(dry_run=args.dry_run)

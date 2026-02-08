# -*- coding: utf-8 -*-
"""
Script de teste E2E mínimo para verificar geração de JSON.
"""

import os
import sys
from pathlib import Path

# Load .env
from dotenv import load_dotenv
load_dotenv()

# Verify API keys
for key in ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OPENROUTER_API_KEY']:
    val = os.environ.get(key, '')
    if val:
        print(f"{key}: SET ({len(val)} chars)")
    else:
        print(f"{key}: NOT SET")

print("\n" + "="*60)
print("TESTE E2E - Geração de JSON")
print("="*60)

# Import after loading env
from src.document_loader import DocumentLoader
from src.pipeline.processor import TribunalProcessor

# Load test PDF
fixtures_dir = Path("tests/fixtures")
pdf_path = fixtures_dir / "pdf_texto_normal.pdf"

if not pdf_path.exists():
    print(f"ERRO: PDF não encontrado: {pdf_path}")
    print("Execute: python tests/fixtures/create_test_pdfs.py")
    sys.exit(1)

print(f"\nCarregando: {pdf_path}")
loader = DocumentLoader()
doc = loader.load(pdf_path)

if not doc.success:
    print(f"ERRO ao carregar PDF: {doc.error}")
    sys.exit(1)

print(f"Documento: {doc.num_chars} chars, {doc.num_pages} páginas")

# Run pipeline
print("\nExecutando pipeline...")
processor = TribunalProcessor()

perguntas = [
    "Quem são as partes do contrato?",
    "Qual o valor da renda?"
]

try:
    result = processor.processar(
        documento=doc,
        perguntas_raw="---\n".join(perguntas),
        area_direito="Civil"
    )

    print(f"\nRun ID: {result.run_id}")
    print(f"Output Dir: {processor._output_dir}")

    # Check JSON files
    output_dir = processor._output_dir
    print(f"\nFicheiros em {output_dir}:")

    for f in sorted(output_dir.glob("*")):
        if f.is_file():
            print(f"  - {f.name}: {f.stat().st_size:,} bytes")

    # Verify critical JSON files
    print("\n" + "="*60)
    print("VERIFICAÇÃO DE FICHEIROS JSON")
    print("="*60)

    critical_files = [
        "fase1_agregado_consolidado.json",
        "fase2_chefe_consolidado.json",
    ]

    all_ok = True
    for fname in critical_files:
        fpath = output_dir / fname
        if fpath.exists():
            size = fpath.stat().st_size
            print(f"✓ {fname}: {size:,} bytes")
        else:
            print(f"✗ {fname}: NÃO EXISTE")
            all_ok = False

    if all_ok:
        print("\n✓ TODOS OS JSON CRÍTICOS GERADOS COM SUCESSO!")
    else:
        print("\n✗ ALGUNS JSON CRÍTICOS NÃO FORAM GERADOS")

except Exception as e:
    print(f"\nERRO no pipeline: {e}")
    import traceback
    traceback.print_exc()

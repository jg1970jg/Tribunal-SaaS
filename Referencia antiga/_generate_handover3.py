# -*- coding: utf-8 -*-
"""Script to generate HANDOVER_PACK_PARTE3.md"""
import re
from pathlib import Path

base = Path(r'C:\Users\Guilherme\Desktop\TRIBUNAL_GOLDENMASTER_GUI')
output_path = base / 'HANDOVER_PACK_PARTE3.md'

# Source files > 500 lines
source_files = [
    'src/app.py',
    'src/llm_client.py',
    'src/legal_verifier.py',
    'src/pipeline/processor.py',
    'src/pipeline/pdf_safe.py',
    'src/pipeline/schema_audit.py',
    'src/pipeline/integrity.py',
    'src/pipeline/extractor_unified.py',
    'src/pipeline/meta_integrity.py',
    'src/pipeline/schema_unified.py',
    'src/pipeline/confidence_policy.py',
    'src/perguntas/pipeline_perguntas.py',
    'src/perguntas/tab_perguntas.py',
]

# Test files
test_files = [
    'tests/__init__.py',
    'tests/conftest.py',
    'tests/fixtures/create_test_pdfs.py',
    'tests/test_document_loader.py',
    'tests/test_e2e_json_pipeline.py',
    'tests/test_e2e_verification.py',
    'tests/test_integrity.py',
    'tests/test_json_output.py',
    'tests/test_legal_verifier_offline.py',
    'tests/test_meta_integrity.py',
    'tests/test_new_features.py',
    'tests/test_pipeline_txt.py',
    'tests/test_unified_provenance.py',
]

def mask_api_keys(content):
    """Mask API keys, tokens, passwords in the content."""
    # sk-... pattern (OpenAI keys)
    content = re.sub(r'sk-[A-Za-z0-9]{20,}', '<API_KEY_OPENAI>', content)
    # Patterns like api_key = "...long string..."
    content = re.sub(r'(api_key\s*=\s*["\'])([A-Za-z0-9_-]{30,})(["\'])', r'\1<API_KEY_X>\3', content)
    return content

# Build the markdown file
parts = []

# Header
parts.append('# HANDOVER PACK --- TRIBUNAL GOLDENMASTER GUI / PARTE 3/3 --- Codigo Fonte (ficheiros > 500 linhas + testes)')
parts.append('')
parts.append('> Gerado automaticamente. Contem o codigo fonte COMPLETO de todos os ficheiros Python com mais de 500 linhas, mais todos os ficheiros de teste.')
parts.append('>')
parts.append('> REGRAS: Chaves de API mascaradas com placeholders. Codigo NAO truncado.')
parts.append('')
parts.append('---')
parts.append('')

# Section 14
parts.append('## 14. EXPORTACAO DO CODIGO')
parts.append('')

# Subsection 14.1 - Source files
parts.append('### 14.1 Ficheiros Fonte (> 500 linhas)')
parts.append('')

for i, fpath in enumerate(source_files, 1):
    full_path = base / fpath
    if full_path.exists():
        content = full_path.read_text(encoding='utf-8')
        content = mask_api_keys(content)
        line_count = len(content.splitlines())
        parts.append(f'#### 14.1.{i} `{fpath}` ({line_count} linhas)')
        parts.append('')
        parts.append('```python')
        parts.append(content)
        parts.append('```')
        parts.append('')
    else:
        parts.append(f'#### 14.1.{i} `{fpath}` --- FICHEIRO NAO ENCONTRADO')
        parts.append('')

# Subsection 14.2 - Test files
parts.append('### 14.2 Ficheiros de Teste')
parts.append('')

for i, fpath in enumerate(test_files, 1):
    full_path = base / fpath
    if full_path.exists():
        content = full_path.read_text(encoding='utf-8')
        content = mask_api_keys(content)
        line_count = len(content.splitlines())
        if line_count == 0:
            parts.append(f'#### 14.2.{i} `{fpath}` (ficheiro vazio)')
            parts.append('')
            parts.append('```python')
            parts.append('# ficheiro vazio')
            parts.append('```')
            parts.append('')
        else:
            parts.append(f'#### 14.2.{i} `{fpath}` ({line_count} linhas)')
            parts.append('')
            parts.append('```python')
            parts.append(content)
            parts.append('```')
            parts.append('')
    else:
        parts.append(f'#### 14.2.{i} `{fpath}` --- FICHEIRO NAO ENCONTRADO')
        parts.append('')

# Footer
parts.append('---')
parts.append('')
parts.append('**FIM DO HANDOVER PACK PARTE 3/3**')
parts.append('')
parts.append(f'Total de ficheiros incluidos: {len(source_files)} fonte + {len(test_files)} testes = {len(source_files) + len(test_files)}')
parts.append('')

# Write the file
final_content = '\n'.join(parts)
output_path.write_text(final_content, encoding='utf-8')

# Stats
total_lines = len(final_content.splitlines())
total_bytes = len(final_content.encode('utf-8'))
print(f'File written: {output_path}')
print(f'Total lines: {total_lines}')
print(f'Total bytes: {total_bytes:,}')
print(f'Total KB: {total_bytes/1024:.1f}')
print(f'Total MB: {total_bytes/1024/1024:.2f}')

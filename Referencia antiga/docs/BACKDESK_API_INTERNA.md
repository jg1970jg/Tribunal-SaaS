# TRIBUNAL GOLDENMASTER - API INTERNA (BACKDESK)

**Versão:** 2.0
**Objetivo:** Definir contratos internos para migração do frontend

---

## 1. VISÃO GERAL

Este documento define os contratos internos entre o **frontend** (Streamlit UI) e o **backdesk** (lógica de negócio). Permite migrar o frontend para outra tecnologia sem modificar o backdesk.

---

## 2. FUNÇÃO DE ENTRADA PRINCIPAL

### `TribunalProcessor.processar()`

```python
def processar(
    self,
    documento: DocumentContent,
    area_direito: str,
    perguntas_raw: str = "",
    titulo: str = "",
) -> PipelineResult:
    """
    Executa o pipeline completo de análise.

    Args:
        documento: DocumentContent com texto extraído
        area_direito: Área do direito (Civil, Penal, etc.)
        perguntas_raw: Perguntas do utilizador separadas por ---
        titulo: Título do projeto (opcional, gera automático)

    Returns:
        PipelineResult com todos os resultados

    Raises:
        ValueError: Perguntas inválidas
        BudgetExceededError: Custo excedeu limite
        TokenLimitExceededError: Tokens excederam limite
    """
```

### Exemplo de uso

```python
from src.pipeline.processor import TribunalProcessor
from src.document_loader import DocumentLoader

# Carregar documento
loader = DocumentLoader()
doc = loader.load("contrato.pdf")

# Processar
processor = TribunalProcessor()
resultado = processor.processar(
    documento=doc,
    area_direito="Civil",
    perguntas_raw="Qual o prazo de recurso?",
    titulo="Contrato Arrendamento Silva"
)

# Usar resultado
print(resultado.veredicto_final)  # "PROCEDENTE"
print(resultado.simbolo_final)    # "✓"
```

---

## 3. TIPOS/DICIONÁRIOS

### `DocumentContent` (Input)

```python
@dataclass
class DocumentContent:
    filename: str           # Nome do ficheiro
    extension: str          # Extensão (.pdf, .docx, etc.)
    text: str               # Texto extraído
    num_pages: int = 0      # Número de páginas
    num_chars: int = 0      # Número de caracteres
    num_words: int = 0      # Número de palavras
    metadata: Dict = {}     # Metadata adicional
    success: bool = True    # Se extração foi bem sucedida
    error: str = None       # Mensagem de erro (se falhou)

    # PDF Safe (opcional)
    pdf_safe_result: Any = None
    pdf_safe_enabled: bool = False
    pages_problematic: int = 0
```

### `PipelineResult` (Output)

```python
@dataclass
class PipelineResult:
    # Identificação
    run_id: str                          # ID único (formato: YYYYMMDD_HHMMSS_hash)
    documento: DocumentContent           # Documento processado
    area_direito: str                    # Área do direito

    # Fase 1: Extração
    fase1_extracoes: List[FaseResult]    # 5 extrações individuais
    fase1_agregado_bruto: str            # Concatenação simples
    fase1_agregado_consolidado: str      # Agregador LOSSLESS

    # Fase 2: Auditoria
    fase2_auditorias: List[FaseResult]   # 3 auditorias
    fase2_auditorias_brutas: str         # Concatenação simples
    fase2_chefe_consolidado: str         # Chefe LOSSLESS

    # Fase 3: Julgamento
    fase3_pareceres: List[FaseResult]    # 3 pareceres
    fase3_presidente: str                # Decisão do presidente

    # Verificação Legal
    verificacoes_legais: List[VerificacaoLegal]

    # Veredicto
    veredicto_final: str                 # "PROCEDENTE", "IMPROCEDENTE", etc.
    simbolo_final: str                   # "✓", "✗", "⚠"
    status_final: str                    # "aprovado", "rejeitado", "atencao"

    # Q&A
    perguntas_utilizador: List[str]
    respostas_juizes_qa: List[Dict]
    respostas_finais_qa: str

    # Estatísticas
    total_tokens: int
    total_latencia_ms: float
    timestamp_inicio: datetime
    timestamp_fim: datetime
    sucesso: bool
    erro: str = None

    def to_dict(self) -> Dict:
        """Serializa para dicionário."""
```

### `FaseResult`

```python
@dataclass
class FaseResult:
    fase: str              # "extrator", "auditor", "juiz"
    modelo: str            # Nome do modelo usado
    role: str              # "extrator_1", "auditor_2", etc.
    conteudo: str          # Texto gerado
    tokens_usados: int     # Tokens consumidos
    latencia_ms: float     # Latência em ms
    sucesso: bool = True
    erro: str = None
```

### `VerificacaoLegal`

```python
@dataclass
class VerificacaoLegal:
    citacao: CitacaoLegal          # Citação normalizada
    existe: bool                   # Se foi encontrada
    texto_encontrado: str = None   # Texto do artigo (se encontrado)
    fonte: str = ""                # "cache_local", "dre_online"
    status: str = ""               # "aprovado", "rejeitado", "atencao"
    simbolo: str = ""              # "✓", "✗", "⚠"
    aplicabilidade: str = "⚠"     # Sempre ⚠ (requer análise humana)
    mensagem: str = ""
```

### `CitacaoLegal`

```python
@dataclass
class CitacaoLegal:
    diploma: str            # "Código Civil"
    artigo: str             # "483º"
    numero: str = None      # "1"
    alinea: str = None      # "a)"
    texto_original: str     # Texto como aparece no documento
    texto_normalizado: str  # Texto normalizado
```

---

## 4. INVARIANTES (FICHEIROS GERADOS)

### Estrutura de outputs/<run_id>/

```
outputs/<run_id>/
├── resultado.json              # OBRIGATÓRIO: Dados completos
├── metadata.json               # OBRIGATÓRIO: Título, área, etc.
├── RESUMO.md                   # OBRIGATÓRIO: Resumo legível
│
├── fase1_extrator_E1.md        # Extração E1
├── fase1_extrator_E2.md        # Extração E2
├── fase1_extrator_E3.md        # Extração E3
├── fase1_extrator_E4.md        # Extração E4
├── fase1_extrator_E5.md        # Extração E5
├── fase1_agregado_bruto.md     # Concatenação
├── fase1_agregado_consolidado.md  # LOSSLESS
│
├── fase2_auditor_1.md          # Auditoria 1
├── fase2_auditor_2.md          # Auditoria 2
├── fase2_auditor_3.md          # Auditoria 3
├── fase2_auditorias_brutas.md  # Concatenação
├── fase2_chefe_consolidado.md  # LOSSLESS
│
├── fase3_juiz_1.md             # Parecer 1
├── fase3_juiz_2.md             # Parecer 2
├── fase3_juiz_3.md             # Parecer 3
├── fase3_qa_respostas.md       # Q&A dos juízes (se perguntas)
│
├── fase4_presidente.md         # Decisão final
├── fase4_qa_final.md           # Q&A consolidado (se perguntas)
│
├── verificacao_legal.md        # Relatório de verificação
└── signals_coverage_report.json # Relatório de sinais (PDF safe)
```

### Histórico

```
historico/<run_id>.json         # Cópia de resultado.json
```

---

## 5. ERROS ESPERADOS

### Códigos/Classes de erro

| Erro | Causa | Código |
|------|-------|--------|
| `BudgetExceededError` | Custo > MAX_BUDGET_USD | 429 |
| `TokenLimitExceededError` | Tokens > MAX_TOKENS_TOTAL | 429 |
| `ValueError` | Perguntas inválidas | 400 |
| `httpx.TimeoutException` | API timeout | 504 |
| `httpx.HTTPStatusError` | Erro HTTP da API | Varia |

### Tratamento recomendado

```python
try:
    resultado = processor.processar(doc, area, perguntas)
except BudgetExceededError as e:
    # Mostrar: "Budget excedido: $X.XX > $Y.YY"
    # Sugerir: Reduzir documento ou aumentar limite
except TokenLimitExceededError as e:
    # Mostrar: "Tokens excedidos: X > Y"
    # Sugerir: Reduzir documento ou aumentar limite
except ValueError as e:
    # Mostrar: Mensagem do erro (validação de perguntas)
except Exception as e:
    # Log do erro
    # Mostrar: "Erro inesperado. Tente novamente."
```

---

## 6. PONTOS DE EXTENSÃO

### Adicionar novo extrator

```python
# Em config.py, adicionar a LLM_CONFIGS:
LLM_CONFIGS = [
    ...
    {
        "id": "E6",
        "role": "Extrator Novo",
        "model": "novo/modelo",
        "temperature": 0.0,
        "instructions": PROMPT_NOVO
    },
]
```

### Adicionar novo auditor/juiz

```python
# Em config.py:
AUDITOR_MODELS.append("novo/modelo")
JUIZ_MODELS.append("novo/modelo")
```

### Customizar prompts

```python
# Em prompts_maximos.py:
PROMPT_PERSONALIZADO = """
Instruções personalizadas...
"""
```

### Adicionar novo formato de documento

```python
# Em document_loader.py, adicionar método:
def _extract_novo_formato(self, file_bytes: bytes) -> tuple:
    # Implementar extração
    return text, pages, metadata

# Em config.py:
SUPPORTED_EXTENSIONS[".novo"] = "Novo Formato"
```

---

## 7. EXEMPLOS DE INTEGRAÇÃO

### Chamar pipeline via Python

```python
from src.pipeline.processor import TribunalProcessor
from src.document_loader import DocumentLoader

# Setup
loader = DocumentLoader()
processor = TribunalProcessor()

# Carregar e processar
doc = loader.load("documento.pdf")
resultado = processor.processar(doc, "Civil")

# Acessar resultados
print(f"Veredicto: {resultado.veredicto_final}")
print(f"Tokens usados: {resultado.total_tokens}")
print(f"Custo estimado: ver resultado.json")

# Ficheiros gerados em outputs/<resultado.run_id>/
```

### Verificar legislação isoladamente

```python
from src.legal_verifier import get_legal_verifier

verifier = get_legal_verifier()

# Verificar uma citação
citacao = verifier.normalizar_citacao("art. 483º do Código Civil")
verificacao = verifier.verificar_citacao(citacao)

print(f"Existe: {verificacao.existe}")
print(f"Status: {verificacao.status}")
print(f"Símbolo: {verificacao.simbolo}")
```

### Usar controlo de custos

```python
from src.cost_controller import CostController, BudgetExceededError

controller = CostController(
    run_id="test",
    budget_limit_usd=1.0,
    token_limit=10000
)

try:
    controller.register_usage(
        phase="fase1_E1",
        model="openai/gpt-4o",
        prompt_tokens=5000,
        completion_tokens=2000
    )

    if controller.can_continue():
        # Continuar processamento
        pass

except BudgetExceededError:
    # Parar processamento
    pass

# Ver resumo
print(controller.get_summary())
```

---

## 8. MIGRAÇÃO PARA OUTRO FRONTEND

### Passos recomendados

1. **Manter backdesk inalterado**: `src/pipeline/`, `src/llm_client.py`, etc.

2. **Criar nova camada de API**:
   ```python
   # api.py (exemplo FastAPI)
   from fastapi import FastAPI, UploadFile
   from src.pipeline.processor import TribunalProcessor
   from src.document_loader import DocumentLoader

   app = FastAPI()

   @app.post("/analisar")
   async def analisar(file: UploadFile, area: str, perguntas: str = ""):
       loader = DocumentLoader()
       doc = loader.load(file.file, file.filename)

       processor = TribunalProcessor()
       resultado = processor.processar(doc, area, perguntas)

       return resultado.to_dict()
   ```

3. **Implementar novo frontend**: React, Vue, etc.

4. **Testar integrações**: Usar os mesmos testes

### Contratos a manter

- Input: `DocumentContent`
- Output: `PipelineResult.to_dict()`
- Erros: Classes definidas neste documento
- Ficheiros: Estrutura de `outputs/<run_id>/`

---

*Documento de API interna para Tribunal GoldenMaster v2.0*

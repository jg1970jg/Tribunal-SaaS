# HANDOVER PACK — TRIBUNAL GOLDENMASTER GUI
## PARTE 1/3 — Arquitectura, Configuração e Fluxos

> **Gerado:** 2026-02-08
> **Total linhas código Python:** ~25.266
> **Ficheiros fonte:** ~60

---

# 1. VISÃO GERAL DO PRODUTO

## O que faz
O **Tribunal GoldenMaster** é uma aplicação web (Streamlit) de **análise jurídica automatizada** focada em **Direito Português**. Recebe documentos legais (PDF, DOCX, XLSX, TXT) ou texto livre e produz um **parecer jurídico estruturado** usando múltiplos modelos de IA (LLMs) organizados num pipeline de 4 fases inspirado num tribunal.

## Para quem
Advogados, juristas e cidadãos portugueses que precisam de análise preliminar de documentos jurídicos (contratos, recursos, petições, etc.).

## Principais features
- **Pipeline multi-LLM de 4 fases**: 5 Extratores → 4 Auditores → 3 Juízes → 1 Presidente
- **Dual API System**: Usa OpenAI directa para modelos GPT + OpenRouter para outros (Claude, Gemini, etc.)
- **Fallback automático**: Se OpenAI falhar, usa OpenRouter como backup
- **Extração LOSSLESS**: Agregadores consolidam sem perder dados únicos de cada extrator
- **PDF Seguro**: Extração página-a-página com detecção de páginas problemáticas
- **Verificação legal**: Valida citações contra o DRE (Diário da República Electrónico)
- **Perguntas Q&A**: Perguntas específicas respondidas por 3 juízes + Presidente
- **Perguntas adicionais**: Pós-análise, permite novas perguntas sobre resultados anteriores
- **Controlo de custos**: Budget USD e limite de tokens por execução
- **Histórico auditável**: Cada análise gera pasta com ficheiros .md e .json por fase
- **Gestão de API keys via interface**: Editar/mascarar/apagar keys no browser
- **Escolha de modelos premium**: GPT-5.2 (económico) vs GPT-5.2-pro (premium) via UI
- **Proveniência unificada**: Source spans com offsets absolutos para rastrear origem
- **Meta-integridade**: Validação de coerência entre ficheiros gerados
- **Política de confiança determinística**: Penalidades automáticas baseadas em flags de qualidade

---

# 2. COMO CORRER (LOCAL)

## Requisitos
- **Python**: 3.10+
- **OS**: Windows (principal), Linux/Mac (suportado)
- **RAM**: ~4GB mínimo (sem modelos locais, tudo via API)

## Comandos Windows
```cmd
cd Desktop\TRIBUNAL_GOLDENMASTER_GUI
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env              # Preencher OPENAI_API_KEY e OPENROUTER_API_KEY
python data/create_db.py  # Inicializar BD legislação
streamlit run src/app.py  # Acesso: http://localhost:8501
```

## Comandos Linux/Mac
```bash
cd ~/TRIBUNAL_GOLDENMASTER_GUI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python data/create_db.py
streamlit run src/app.py
```

## Docker
```bash
cd docker
docker compose up --build   # http://localhost:8501
```

---

# 3. DEPENDÊNCIAS

```
# requirements.txt

# Interface Web
streamlit>=1.28.0          # Framework UI (toda a interface)

# Dados e Validação
pandas>=2.0.0              # Manipulação tabular (Excel, dados)
pydantic>=2.0.0            # Validação schemas JSON (EvidenceItem, AuditReport, etc.)

# HTTP e API
httpx>=0.25.0              # Cliente HTTP assíncrono/síncrono (chamadas API)
tenacity>=8.2.0            # Retry com backoff exponencial

# Leitura de Documentos
pypdf>=3.17.0              # Extração texto PDF (fallback)
python-docx>=1.0.0         # Leitura Word .docx
openpyxl>=3.1.0            # Leitura Excel .xlsx
pdfplumber>=0.10.0         # Extração texto PDF avançada

# PDF Seguro (extração página-a-página)
pymupdf>=1.23.0            # fitz - rendering PDF → imagem, extração texto por página
pillow>=10.0.0             # Processamento imagem (thumbnails páginas)

# Web Scraping (DRE)
beautifulsoup4>=4.12.0     # Parsing HTML do DRE
lxml>=4.9.0                # Parser HTML rápido

# Export PDF
reportlab>=4.0.0           # Geração relatórios PDF

# Ambiente
python-dotenv>=1.0.0       # Carregamento .env

# Testes
pytest>=7.4.0
pytest-cov>=4.1.0

# Opcional: OCR (requer Tesseract)
# pytesseract>=0.3.10
```

### Bibliotecas críticas
| Biblioteca | Porquê |
|---|---|
| `httpx` | Todas as chamadas LLM (OpenAI + OpenRouter) passam por httpx |
| `tenacity` | Retry automático com backoff em caso de rate limit (429) ou erro servidor (5xx) |
| `pymupdf` (fitz) | Core do PDF Seguro: rendering, extração por página, detecção problemas |
| `streamlit` | Toda a UI. Sem alternativa drop-in. Migração exigiria reescrever frontend |
| `python-dotenv` | Carregamento de API keys de ficheiros .env |
| `pydantic` | Schemas estruturados (EvidenceItem, AuditReport, JudgeOpinion, etc.) |

---

# 4. CONFIGURAÇÃO

## Variáveis de ambiente (.env)

| Variável | Obrigatória | Formato | Descrição |
|---|---|---|---|
| `OPENAI_API_KEY` | Sim* | `sk-proj-...` | Key directa OpenAI (modelos GPT) |
| `OPENROUTER_API_KEY` | Sim* | `sk-or-v1-...` | Key OpenRouter (Claude, Gemini, etc. + fallback) |
| `OPENROUTER_BASE_URL` | Não | URL | Default: `https://openrouter.ai/api/v1` |
| `API_TIMEOUT` | Não | int (segundos) | Default: 180 |
| `API_MAX_RETRIES` | Não | int | Default: 5 |
| `MAX_BUDGET_USD` | Não | float | Default: 5.00 — budget máximo por execução |
| `MAX_TOKENS_TOTAL` | Não | int | Default: 500000 — limite tokens por execução |
| `LOG_LEVEL` | Não | string | Default: INFO |

*Pelo menos uma das API keys deve estar configurada. Idealmente ambas.

## Ficheiros de configuração

| Ficheiro | Caminho | Descrição |
|---|---|---|
| `.env` | Raiz | API keys e configurações runtime |
| `.env.example` | Raiz | Template com placeholders |
| `src/config.py` | src/ | Configuração central Python (modelos, prompts, thresholds) |
| `.streamlit/config.toml` | .streamlit/ | Config Streamlit (tema, porta) |

## Modelos configurados (src/config.py)

### Fase 1 — Extratores (5)
| ID | Modelo | Provider | Temperatura |
|----|--------|----------|-------------|
| E1 | `anthropic/claude-opus-4.5` | OpenRouter | 0.0 |
| E2 | `google/gemini-3-flash-preview` | OpenRouter | 0.0 |
| E3 | `openai/gpt-4o` | **OpenAI directa** | 0.0 |
| E4 | `anthropic/claude-3-5-sonnet` | OpenRouter | 0.0 |
| E5 | `deepseek/deepseek-chat` | OpenRouter | 0.0 |

### Fase 2 — Auditores (4)
| ID | Modelo | Provider | Temperatura |
|----|--------|----------|-------------|
| A1 | `openai/gpt-5.2` | **OpenAI directa** (Responses API) | 0.1 |
| A2 | `anthropic/claude-opus-4.5` | OpenRouter | 0.0 |
| A3 | `google/gemini-3-pro-preview` | OpenRouter | 0.0 |
| A4 | `x-ai/grok-4.1-fast` | OpenRouter | 0.1 |

### Fase 3 — Juízes (3)
| ID | Modelo | Provider | Temperatura |
|----|--------|----------|-------------|
| J1 | `openai/gpt-5.2` | **OpenAI directa** (Responses API) | 0.2 |
| J2 | `anthropic/claude-opus-4.5` | OpenRouter | 0.1 |
| J3 | `google/gemini-3-pro-preview` | OpenRouter | 0.0 |

### Consolidadores
| Papel | Modelo | API |
|-------|--------|-----|
| **Agregador** (Fase 1) | `openai/gpt-5.2` | OpenAI Responses API |
| **Chefe** (Fase 2) | `openai/gpt-5.2` ou `gpt-5.2-pro` (escolha UI) | OpenAI Responses API |
| **Presidente** (Fase 4) | `openai/gpt-5.2` ou `gpt-5.2-pro` (escolha UI) | OpenAI Responses API |

---

# 5. ARQUITECTURA

## Camadas

```
┌─────────────────────────────────────────────────────────┐
│                    STREAMLIT UI (app.py)                │
│  Páginas: Analisar Docs | Texto | Histórico | Q&A |    │
│           API Keys | Configurações | Ajuda             │
├─────────────────────────────────────────────────────────┤
│               COMPONENTES UI (src/components/)          │
│  components_api_config.py | components_model_selector   │
│  src/ui/page_repair.py | src/perguntas/tab_perguntas   │
├─────────────────────────────────────────────────────────┤
│                SERVIÇOS / PIPELINE                      │
│  pipeline/processor.py (3147 linhas) — ORQUESTRADOR     │
│  ├── Fase 1: Extração + Agregação                      │
│  ├── Fase 2: Auditoria + Chefe                         │
│  ├── Fase 3: Julgamento (juízes + Q&A)                 │
│  └── Fase 4: Presidente (decisão final)                │
│                                                         │
│  pipeline/pdf_safe.py — Extração PDF página-a-página   │
│  pipeline/extractor_unified.py — Parser output LLM      │
│  pipeline/schema_unified.py — Schemas Pydantic          │
│  pipeline/schema_audit.py — Schemas auditoria           │
│  pipeline/integrity.py — Validação integridade          │
│  pipeline/meta_integrity.py — Meta-integridade          │
│  pipeline/confidence_policy.py — Política confiança     │
│  pipeline/page_mapper.py — Mapeamento char→página       │
│  pipeline/text_normalize.py — Normalização texto        │
│  pipeline/extractor_json.py — Parser JSON output        │
│  pipeline/constants.py — Constantes (flags, estados)    │
├─────────────────────────────────────────────────────────┤
│                  CLIENTES EXTERNOS                      │
│  llm_client.py — Dual API (OpenAI + OpenRouter)        │
│  legal_verifier.py — Scraping DRE                       │
│  document_loader.py — Leitura PDF/DOCX/XLSX/TXT        │
│  cost_controller.py — Controlo budget/tokens            │
├─────────────────────────────────────────────────────────┤
│                    DADOS                                │
│  data/legislacao_pt.db — SQLite (legislação cached)     │
│  outputs/<run_id>/ — Outputs por análise                │
│  historico/<run_id>.json — Histórico compacto           │
│  .env — API keys                                        │
└─────────────────────────────────────────────────────────┘
```

## Fluxo de dados principal

```
UTILIZADOR                    SISTEMA
    │
    ├── Upload PDF(s) ────────► document_loader.py
    │                          ├── pypdf / pdfplumber (texto)
    │                          ├── pdf_safe.py (página-a-página)
    │                          └── → DocumentContent
    │
    ├── Seleciona área ────────► config.py (AREAS_DIREITO)
    ├── Escreve perguntas ─────► utils/perguntas.py (parse)
    ├── Escolhe modelo ────────► components_model_selector.py
    │
    ├── Clica ANALISAR ────────► processor.py.processar()
    │                           │
    │   ┌── FASE 1 ────────────┤
    │   │   5 extratores (LLM) │ llm_client.py → OpenAI/OpenRouter
    │   │   + Agregador LOSSLESS│
    │   │                       │
    │   ├── FASE 2 ────────────┤
    │   │   4 auditores (LLM)  │
    │   │   + Chefe LOSSLESS   │
    │   │                       │
    │   ├── FASE 3 ────────────┤
    │   │   3 juízes (LLM)     │
    │   │   + Q&A por juiz     │
    │   │                       │
    │   └── FASE 4 ────────────┤
    │       Presidente (LLM)   │
    │       + Q&A consolidado  │
    │       + Veredicto final  │
    │                           │
    │   legal_verifier.py ─────┤ Scraping DRE
    │   integrity.py ──────────┤ Validação integridade
    │   meta_integrity.py ─────┤ Meta-integridade
    │   confidence_policy.py ──┤ Penalidades confiança
    │                           │
    │                           └──► PipelineResult
    │                                ├── outputs/<run_id>/
    │                                ├── historico/<run_id>.json
    │                                └── resultado.json
    │
    ├── Visualiza resultado ───► app.py (renderizar_resultado)
    ├── Exporta JSON/MD ───────► download via Streamlit
    └── Perguntas adicionais ──► perguntas/pipeline_perguntas.py
```

## Routing de APIs (llm_client.py)

```
Modelo solicitado
    │
    ├── is_openai_model()? ──── SIM ──┐
    │                                  │
    │                          uses_responses_api()?
    │                          ├── SIM: POST /v1/responses (GPT-5.2, GPT-5.2-pro)
    │                          │        ├── max_output_tokens (não max_tokens!)
    │                          │        ├── instructions (não system no input)
    │                          │        └── sem temperature para modelos -pro
    │                          │
    │                          └── NÃO: POST /v1/chat/completions (GPT-4o, etc.)
    │                                   └── formato standard chat
    │
    │                          Se FALHAR → fallback OpenRouter
    │
    └── NÃO (Claude, Gemini, etc.) → OpenRouter
         POST openrouter.ai/api/v1/chat/completions
```

---

# 6. ÁRVORE COMPLETA DO PROJECTO

```
TRIBUNAL_GOLDENMASTER_GUI/
├── .env                          # API keys (NÃO versionar)
├── .env.example                  # Template .env
├── .gitignore
├── .streamlit/
│   └── config.toml               # Config Streamlit (tema, porta)
├── README.md                     # Documentação principal
├── VERIFICACAO_FINAL_E2E.md      # Relatório verificação E2E
├── requirements.txt              # Dependências Python
├── prompts_maximos.py            # 300 linhas — Prompts dos extratores e agregador
├── run_e2e_test.py               # 104 linhas — Script teste E2E
├── script_titular_em_massa.py    # 273 linhas — Script batch títulos
├── teste_llm_client.py           # 77 linhas — Teste manual LLM client
├── tree.txt                      # Árvore do projecto (gerada)
│
├── data/
│   ├── create_db.py              # 101 linhas — Cria BD SQLite legislação
│   └── legislacao_pt.db          # BD SQLite (cache legislação DRE)
│
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── entrypoint.sh
│   └── README_DOCKER_SECTION.md
│
├── docs/
│   ├── BACKDESK_API_INTERNA.md   # Contratos API interna
│   └── HANDOVER.md               # Handover anterior (desactualizado)
│
├── fixtures/
│   └── sample_input.txt          # Input de teste
│
├── src/
│   ├── __init__.py               # 8 linhas
│   ├── app.py                    # 1879 linhas — ENTRYPOINT Streamlit (UI principal)
│   ├── config.py                 # 336 linhas — Configuração central
│   ├── llm_client.py             # 1062 linhas — Dual API client (OpenAI + OpenRouter)
│   ├── document_loader.py        # 475 linhas — Carregamento documentos
│   ├── legal_verifier.py         # 528 linhas — Verificação DRE
│   ├── cost_controller.py        # 376 linhas — Controlo de custos
│   │
│   ├── components/
│   │   ├── __init__.py           # 0 linhas
│   │   ├── components_api_config.py    # 285 linhas — UI gestão API keys
│   │   └── components_model_selector.py # 214 linhas — UI escolha modelo premium
│   │
│   ├── ui/
│   │   ├── __init__.py           # 4 linhas
│   │   └── page_repair.py        # 430 linhas — UI reparação páginas PDF
│   │
│   ├── perguntas/
│   │   ├── __init__.py           # 9 linhas
│   │   ├── pipeline_perguntas.py # 915 linhas — Pipeline perguntas adicionais
│   │   └── tab_perguntas.py      # 805 linhas — UI tab perguntas
│   │
│   ├── pipeline/
│   │   ├── __init__.py           # 8 linhas
│   │   ├── processor.py          # 3147 linhas — ORQUESTRADOR PRINCIPAL
│   │   ├── pdf_safe.py           # 1234 linhas — Extração PDF segura
│   │   ├── schema_audit.py       # 1116 linhas — Schemas auditoria (Pydantic)
│   │   ├── integrity.py          # 1008 linhas — Validador integridade
│   │   ├── extractor_unified.py  # 820 linhas — Parser output LLM → EvidenceItem
│   │   ├── meta_integrity.py     # 819 linhas — Meta-integridade
│   │   ├── schema_unified.py     # 689 linhas — Schemas unificados (Pydantic)
│   │   ├── confidence_policy.py  # 568 linhas — Política confiança
│   │   ├── page_mapper.py        # 483 linhas — Mapeamento char→página
│   │   ├── text_normalize.py     # 450 linhas — Normalização texto
│   │   ├── extractor_json.py     # 345 linhas — Parser JSON output
│   │   └── constants.py          # 95 linhas — Constantes (flags, estados)
│   │
│   └── utils/
│       ├── __init__.py           # 6 linhas
│       ├── cleanup.py            # 280 linhas — Limpeza outputs temporários
│       ├── metadata_manager.py   # 279 linhas — Gestão metadata análises
│       └── perguntas.py          # 102 linhas — Parse/validação perguntas
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # 100 linhas — Fixtures pytest
│   ├── fixtures/
│   │   └── create_test_pdfs.py   # 221 linhas
│   ├── test_document_loader.py   # 95 linhas
│   ├── test_e2e_json_pipeline.py # 716 linhas
│   ├── test_e2e_verification.py  # 706 linhas
│   ├── test_integrity.py         # 990 linhas
│   ├── test_json_output.py       # 303 linhas
│   ├── test_legal_verifier_offline.py # 200 linhas
│   ├── test_meta_integrity.py    # 1063 linhas
│   ├── test_new_features.py      # 576 linhas
│   ├── test_pipeline_txt.py      # 274 linhas
│   └── test_unified_provenance.py # 392 linhas
│
├── outputs/                      # Outputs de análises (pasta por run_id)
│   └── <run_id>/
│       ├── resultado.json
│       ├── RESUMO.md
│       ├── metadata.json
│       ├── fase1_extrator_E1.md ... E5.md
│       ├── fase1_agregado_consolidado.md
│       ├── fase2_auditor_1.md ... 4.md
│       ├── fase2_chefe_consolidado.md
│       ├── fase3_juiz_1.md ... 3.md
│       ├── fase4_presidente.md
│       ├── verificacao_legal.md
│       ├── integrity_report.json
│       ├── meta_integrity_report.json
│       ├── confidence_penalty.json
│       └── perguntas/
│           ├── pergunta_1.json
│           ├── pergunta_1_completa.md
│           └── documentos_anexados/
│
└── historico/                    # Histórico compacto (um JSON por análise)
    └── <run_id>.json
```

---

# 7. FLUXOS DE UI

## Páginas / Rotas (app.py — session_state.pagina)

| Valor `pagina` | Função | Descrição |
|---|---|---|
| `"analisar"` | `pagina_analisar_documento()` | Upload + análise documentos |
| `"texto"` | `pagina_analisar_texto()` | Análise texto colado |
| `"historico"` | `pagina_historico()` | Lista análises anteriores |
| `"perguntas"` | `pagina_perguntas()` | Perguntas sobre análises existentes |
| `"titulos"` | `pagina_gerir_titulos()` | Editar títulos de análises |
| `"api_keys"` | `pagina_api_keys()` | Gestão API keys |
| `"config"` | `pagina_configuracoes()` | Configurações e testes |
| `"ajuda"` | `pagina_ajuda()` | Como funciona |

## session_state principal

| Chave | Tipo | Descrição |
|---|---|---|
| `processor` | `TribunalProcessor` | Instância do processador |
| `resultado` | `PipelineResult` | Resultado da análise actual |
| `resultados_multiplos` | `list` | Resultados múltiplos |
| `pagina` | `str` | Página actual |
| `documentos_carregados` | `list[DocumentContent]` | Docs processados |
| `ficheiros_acumulados` | `dict{nome: bytes}` | Ficheiros acumulados pelo uploader |
| `model_choices` | `dict{chefe, presidente}` | Modelos premium escolhidos |
| `pdf_bytes_cache` | `dict` | Cache bytes PDF |
| `pdf_out_dirs` | `dict` | Dirs output PDF Seguro |
| `perguntas_raw_docs` | `str` | Perguntas brutas (modo docs) |
| `perguntas_raw_texto` | `str` | Perguntas brutas (modo texto) |

## Componentes UI reutilizáveis

- **`selecao_modelos_premium()`** (components_model_selector.py): Widget para escolher GPT-5.2 vs GPT-5.2-pro para Chefe e Presidente
- **`pagina_api_keys()`** (components_api_config.py): Página completa gestão API keys
- **`tab_perguntas_adicionais()`** (perguntas/tab_perguntas.py): Tab isolada para perguntas sobre análises existentes
- **`renderizar_ui_perguntas()`** (app.py): Widget para input de perguntas Q&A

---

# 8. BACKDESK: SERVIÇOS E FUNÇÕES PRINCIPAIS

## processor.py — TribunalProcessor (3147 linhas)

**Classe principal:** `TribunalProcessor`

### Construtor
```python
TribunalProcessor(
    extrator_models=None,     # Override modelos fase 1
    auditor_models=None,      # Override modelos fase 2
    juiz_models=None,         # Override modelos fase 3
    presidente_model=None,    # Override presidente
    agregador_model=None,     # Override agregador
    chefe_model=None,         # Override chefe
    callback_progresso=None,  # Callback(fase, progresso%, mensagem)
)
```

### Métodos públicos
| Método | Input | Output | Descrição |
|---|---|---|---|
| `processar(doc, area, perguntas, titulo)` | DocumentContent, str, str, str | PipelineResult | Pipeline completo para documento |
| `processar_texto(texto, area, perguntas)` | str, str, str | PipelineResult | Pipeline para texto livre |
| `carregar_run(run_id)` | str | dict | Carrega resultado anterior |

### Fluxo interno do processar()
1. `_setup_run()` → cria run_id e directório output
2. `_fase1_extracao(texto)` → 5 extratores + agregação LOSSLESS
3. `_fase2_auditoria(agregado)` → 4 auditores + Chefe LOSSLESS
4. `_fase3_julgamento(auditoria, perguntas)` → 3 juízes + Q&A
5. `_fase4_presidente(pareceres, qa)` → Decisão final
6. `_verificar_legislacao(texto_presidente)` → Scraping DRE
7. `_guardar_outputs()` → Ficheiros por fase
8. `_guardar_historico()` → JSON compacto

### Dataclasses
- **`FaseResult`**: resultado de uma chamada LLM (fase, modelo, conteudo, tokens, latencia, sucesso, erro)
- **`PipelineResult`**: resultado completo (run_id, documento, fases 1-4, verificações legais, stats)

## llm_client.py — Dual API System (1062 linhas)

### Classes
- **`OpenAIClient`**: Cliente directo para `api.openai.com`
  - Chat API: `/v1/chat/completions` (GPT-4o, etc.)
  - Responses API: `/v1/responses` (GPT-5.2, GPT-5.2-pro)
- **`OpenRouterClient`**: Cliente para `openrouter.ai/api/v1`
- **`UnifiedLLMClient`**: Orquestrador que detecta modelo e escolhe API + fallback

### Funções helper
- `is_openai_model(name)` → bool
- `uses_responses_api(name)` → bool
- `supports_temperature(name)` → bool (modelos reasoning não suportam)
- `should_use_openai_direct(name)` → bool
- `normalize_model_name(name, for_api)` → str
- `get_llm_client()` → UnifiedLLMClient (singleton global)
- `call_llm(model, prompt, ...)` → LLMResponse (conveniência)

### Retry Policy
- Retry em: 429 (rate limit), 5xx (servidor), timeouts
- NÃO retry em: 400, 401, 403, 404 (erros cliente)
- Max 5 tentativas, backoff exponencial 2s→30s

## legal_verifier.py (528 linhas)

- **`LegalVerifier`**: Extrai citações legais do texto e verifica no DRE
- Endpoint: `https://diariodarepublica.pt/dr/pesquisa`
- Output: lista de `VerificacaoLegal` com status (verificada/não encontrada/atenção)

## document_loader.py (475 linhas)

- **`DocumentLoader`**: Carrega PDF, DOCX, XLSX, TXT
- **`DocumentContent`**: dataclass com texto, metadata, info PDF Seguro
- Método especial: `load_pdf_safe()` → usa `pdf_safe.py` para extracção por página

## cost_controller.py (376 linhas)

- **`CostController`**: Rastreia tokens e custos por modelo
- Bloqueia execução se exceder `MAX_BUDGET_USD` ou `MAX_TOKENS_TOTAL`
- Mapa de custos por modelo (input/output por 1M tokens)

---

# 9. DADOS

## BD SQLite: `data/legislacao_pt.db`

Criada por `data/create_db.py`. Schema:

```sql
CREATE TABLE legislacao (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diploma TEXT NOT NULL,      -- Ex: "Código Civil"
    artigo TEXT NOT NULL,       -- Ex: "1022"
    numero TEXT,                -- Ex: "1"
    alinea TEXT,                -- Ex: "a)"
    texto TEXT,                 -- Texto do artigo
    fonte TEXT,                 -- URL DRE
    data_verificacao TEXT,      -- Timestamp
    UNIQUE(diploma, artigo, numero, alinea)
);
```

Serve como **cache** para evitar scraping repetido do DRE.

## Directório outputs/<run_id>/

Cada análise gera:

| Ficheiro | Formato | Conteúdo |
|---|---|---|
| `resultado.json` | JSON | Dados completos (PipelineResult serializado) |
| `metadata.json` | JSON | Título, área, data, stats |
| `RESUMO.md` | Markdown | Resumo legível |
| `fase1_extrator_E1.md`…`E5.md` | Markdown | Output bruto de cada extrator |
| `fase1_agregado_consolidado.md` | Markdown | Agregação LOSSLESS |
| `fase1_agregado_consolidado.json` | JSON | EvidenceItems estruturados |
| `fase1_unified_result.json` | JSON | UnifiedExtractionResult |
| `fase1_coverage_report.json` | JSON | Relatório cobertura |
| `fase2_auditor_1.md`…`4.md` | Markdown | Output bruto de cada auditor |
| `fase2_auditor_1.json`…`4.json` | JSON | AuditReport estruturado |
| `fase2_chefe_consolidado.md` | Markdown | Consolidação Chefe |
| `fase3_juiz_1.md`…`3.md` | Markdown | Output bruto de cada juiz |
| `fase3_juiz_1.json`…`3.json` | JSON | JudgeOpinion estruturado |
| `fase4_presidente.md` | Markdown | Decisão final |
| `fase4_decisao_final.json` | JSON | FinalDecision estruturado |
| `verificacao_legal.md` | Markdown | Resultado verificações DRE |
| `integrity_report.json` | JSON | Validação integridade |
| `meta_integrity_report.json` | JSON | Meta-integridade |
| `confidence_penalty.json` | JSON | Penalidades confiança |
| `perguntas/pergunta_N.json` | JSON | Perguntas adicionais |

## Directório historico/

Ficheiros `<run_id>.json` — cópia compacta do resultado para listagem rápida.

---

# 10. INTEGRAÇÕES EXTERNAS

## 1. OpenAI API (directa)

| Parâmetro | Valor |
|---|---|
| Base URL | `https://api.openai.com/v1` |
| Endpoints | `/chat/completions` (GPT-4o, etc.) e `/responses` (GPT-5.2) |
| Auth | `Bearer <OPENAI_API_KEY>` |
| Timeout | 180s |
| Retries | 5 (apenas 429/5xx) |
| Rate limit | Gerido pela OpenAI (429 → retry com backoff) |

### Responses API (/v1/responses) — Particularidades
- Parâmetro: `max_output_tokens` (NÃO `max_tokens`)
- Parâmetro: `instructions` para system prompt (NÃO mensagem system no input)
- Modelos `-pro`: NÃO suportam `temperature`
- Response: `output_text` (NÃO `choices[0].message.content`)

## 2. OpenRouter API

| Parâmetro | Valor |
|---|---|
| Base URL | `https://openrouter.ai/api/v1` |
| Endpoint | `/chat/completions` |
| Auth | `Bearer <OPENROUTER_API_KEY>` |
| Headers extras | `HTTP-Referer`, `X-Title` |
| Timeout | 180s |
| Retries | 5 (apenas 429/5xx) |

## 3. DRE (Diário da República Electrónico)

| Parâmetro | Valor |
|---|---|
| Base URL | `https://diariodarepublica.pt` |
| Endpoint | `/dr/pesquisa` |
| Método | HTTP scraping (BeautifulSoup) |
| Sem autenticação | Acesso público |
| Cache | SQLite local (`legislacao_pt.db`) |

---

# 11. LOGGING E ERROS

## Logging
- `logging.basicConfig(level=logging.INFO)` em `llm_client.py`
- `LOG_LEVEL` configurável via `.env`
- Formato: prefixos emoji para identificar API:
  - `🔵` = OpenAI API
  - `🟠` = OpenRouter API
  - `🎯` = Detecção modelo
  - `🔄` = Fallback
  - `✅` = Sucesso
  - `❌` = Erro
  - `⚠️` = Warning

## Mensagens típicas
```
INFO:llm_client:🎯 Modelo OpenAI detectado: openai/gpt-5.2 (via Responses API)
INFO:llm_client:🔵 Chamando OpenAI Responses API: openai/gpt-5.2
INFO:httpx:HTTP Request: POST https://api.openai.com/v1/responses "HTTP/1.1 200 OK"
INFO:llm_client:✅ OpenAI Responses resposta: 1234 tokens, 2500ms
```

## Erros comuns
| Erro | Causa | Solução |
|---|---|---|
| `OPENAI_API_KEY não configurada!` | .env sem key | Preencher .env |
| `400 Unknown parameter: 'max_tokens'` | Responses API recebe param errado | Usar `max_output_tokens` |
| `400 temperature not supported` | Modelo reasoning (pro) | Não enviar temperature |
| `401 Unauthorized` | Key inválida/expirada | Renovar key |
| `429 Rate Limit` | Muitas chamadas | Retry automático |
| `Budget excedido` | Custo > MAX_BUDGET_USD | Aumentar .env ou documento menor |

---

# 12. TESTES

## O que existe
- **13 ficheiros de teste** na pasta `tests/`
- **~5.315 linhas** de testes
- Framework: **pytest**

| Ficheiro | Linhas | Foco |
|---|---|---|
| `test_meta_integrity.py` | 1063 | Meta-integridade |
| `test_integrity.py` | 990 | Validação integridade |
| `test_e2e_json_pipeline.py` | 716 | Pipeline E2E JSON |
| `test_e2e_verification.py` | 706 | Verificação E2E |
| `test_new_features.py` | 576 | Features novas |
| `test_unified_provenance.py` | 392 | Proveniência |
| `test_json_output.py` | 303 | Output JSON |
| `test_pipeline_txt.py` | 274 | Pipeline texto |
| `test_legal_verifier_offline.py` | 200 | Verificador legal offline |
| `test_document_loader.py` | 95 | Loader documentos |
| `conftest.py` | 100 | Fixtures |

## Como correr
```bash
# Todos
pytest -q

# Com cobertura
pytest --cov=src --cov-report=html

# Ficheiro específico
pytest tests/test_integrity.py -v

# E2E (requer API keys)
python run_e2e_test.py
```

## Gaps conhecidos
- **Sem testes unitários** para `llm_client.py` (Dual API, Responses API, fallback)
- **Sem testes** para componentes UI (components_api_config, components_model_selector)
- **Sem testes** para `perguntas/pipeline_perguntas.py`
- **Testes E2E** requerem API keys reais (não mocados)
- **Sem CI/CD** configurado

---

# 13. PONTOS FRÁGEIS / DÍVIDA TÉCNICA / TODOs

### Crítico
1. **Dois ficheiros .env** (`/.env` e `/src/.env`) com keys diferentes — já corrigido para usar apenas raiz
2. **processor.py tem 3147 linhas** — monólito difícil de manter, devia ser dividido por fase
3. **Sem testes para Dual API** — o bug `max_tokens` vs `max_output_tokens` passou despercebido

### Importante
4. **`app.py` tem 1879 linhas** — devia ser dividido (render, callbacks, pipeline)
5. **Fallback silencioso** — utilizador não vê na UI quando OpenAI falha e OpenRouter assume
6. **Modelos hardcoded** em `config.py` — devia ser configurável via UI/ficheiro
7. **Sem rate limiting client-side** — depende apenas do retry em 429
8. **Legal verifier faz scraping** — frágil, DRE pode mudar HTML
9. **Sem gestão de sessão persistente** — sessão Streamlit perde-se ao fechar browser

### Nice-to-have
10. **Sem internacionalização** — apenas português
11. **Sem autenticação/roles** — qualquer pessoa com acesso ao URL pode usar
12. **Sem WebSocket/SSE** — progress bar via polling Streamlit
13. **Código misto PT/EN** — nomes de variáveis em português, docstrings em português
14. **Outputs temporários não são limpos automaticamente** — pasta outputs/ cresce

---

# 15. CHECKLIST — O QUE FOI INCLUÍDO

- [x] 1. Visão geral do produto
- [x] 2. Como correr (local)
- [x] 3. Dependências
- [x] 4. Configuração (env vars, ficheiros config)
- [x] 5. Arquitectura (diagrama textual)
- [x] 6. Árvore completa do projecto
- [x] 7. Fluxos de UI (páginas, session_state)
- [x] 8. Backdesk (serviços/funções)
- [x] 9. Dados (BD, schemas, outputs)
- [x] 10. Integrações externas (OpenAI, OpenRouter, DRE)
- [x] 11. Logging e erros
- [x] 12. Testes
- [x] 13. Pontos frágeis / dívida técnica
- [ ] 14. Exportação código — **VER PARTES 2 E 3**
- [x] 15. Checklist

---

# SEGREDOS MASCARADOS

| Localização | Variável | Formato Esperado |
|---|---|---|
| `/.env` linha 16 | `OPENAI_API_KEY` | `sk-proj-<164 chars>` |
| `/.env` linha 19 | `OPENROUTER_API_KEY` | `sk-or-v1-<64 hex chars>` |
| `/src/.env` linha 1 | `OPENAI_API_KEY` | `sk-proj-<164 chars>` (aspas simples) |
| `/src/.env` linha 2 | `OPENROUTER_API_KEY` | `sk-or-v1-<64 hex chars>` (aspas simples) |

> **NOTA:** O projecto tem DOIS ficheiros .env com keys diferentes. O `config.py` foi corrigido para usar explicitamente o da raiz (`/.env`).

---

*Continua em PARTE 2/3 (código fonte principal) e PARTE 3/3 (pipeline, tests, docs)*

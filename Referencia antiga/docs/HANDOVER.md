# TRIBUNAL GOLDENMASTER - DOCUMENTO DE HANDOVER

**Versão:** 2.0
**Data:** 2026-02-05
**Objetivo:** Permitir a outra IA (ex.: GPT) compreender e modificar o sistema a 100%

---

## 1. VISÃO GERAL DO PRODUTO

### O que é
O **Tribunal GoldenMaster** é um sistema de análise jurídica baseado em IA para **Direito Português**. Processa documentos legais através de um pipeline de 4 fases usando múltiplos modelos LLM para extrair, auditar, avaliar e emitir veredictos fundamentados.

### Problema que resolve
- Análise manual de documentos legais é demorada e propensa a omissões
- Profissionais do direito precisam de apoio para identificar legislação aplicável
- Verificação de citações legais requer acesso a bases de dados especializadas

### Funcionalidades principais
1. **Extração multi-modelo**: 5 LLMs extraem informação em paralelo (LOSSLESS)
2. **Auditoria cruzada**: 3 LLMs auditam a extração
3. **Julgamento colegial**: 3 LLMs emitem parecer + Presidente decide
4. **Verificação legal**: Verifica citações contra DRE (Diário da República)
5. **Perguntas Q&A**: Utilizador pode fazer perguntas específicas
6. **Controlo de custos**: Budget e tokens limitados por execução

### Público-alvo
- Advogados
- Juízes
- Solicitadores
- Funcionários públicos com funções jurídicas
- Estudantes de Direito

---

## 2. COMO CORRER LOCALMENTE

### Requisitos
- Python 3.10+
- Pelo menos uma API key (OpenAI ou OpenRouter)
- 2GB RAM mínimo
- Conexão internet (para APIs e verificação DRE)

### Passo a passo (Windows)

```cmd
# 1. Clonar/extrair projeto
cd Desktop\TRIBUNAL_GOLDENMASTER_GUI

# 2. Criar ambiente virtual
python -m venv venv

# 3. Ativar ambiente
venv\Scripts\activate

# 4. Instalar dependências
pip install -r requirements.txt

# 5. Configurar API keys
copy .env.example .env
notepad .env
# Preencher OPENAI_API_KEY e/ou OPENROUTER_API_KEY

# 6. Inicializar base de dados
python data/create_db.py

# 7. Executar
streamlit run src/app.py
```

### Passo a passo (Linux/Mac)

```bash
# 1. Clonar/extrair projeto
cd ~/TRIBUNAL_GOLDENMASTER_GUI

# 2. Criar ambiente virtual
python3 -m venv venv

# 3. Ativar ambiente
source venv/bin/activate

# 4. Instalar dependências
pip install -r requirements.txt

# 5. Configurar API keys
cp .env.example .env
nano .env
# Preencher OPENAI_API_KEY e/ou OPENROUTER_API_KEY

# 6. Inicializar base de dados
python data/create_db.py

# 7. Executar
streamlit run src/app.py
```

### URL de acesso
http://localhost:8501

---

## 3. DEPENDÊNCIAS E VERSÕES

### Python
- **Versão mínima**: 3.10
- **Versão recomendada**: 3.11

### Bibliotecas principais

| Biblioteca | Versão | Propósito |
|------------|--------|-----------|
| streamlit | >=1.28.0 | Interface web |
| httpx | >=0.25.0 | Chamadas HTTP assíncronas |
| tenacity | >=8.2.0 | Retry logic |
| pypdf | >=3.17.0 | Extração PDF |
| pdfplumber | >=0.10.0 | Extração PDF (fallback) |
| python-docx | >=1.0.0 | Extração Word |
| openpyxl | >=3.1.0 | Extração Excel |
| beautifulsoup4 | >=4.12.0 | Web scraping DRE |
| pydantic | >=2.0.0 | Validação de dados |
| python-dotenv | >=1.0.0 | Variáveis de ambiente |
| pytest | >=7.4.0 | Testes |

### Ficheiro completo
Ver `requirements.txt` na raiz.

---

## 4. CONFIGURAÇÃO (ENV VARS)

### Variáveis obrigatórias

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `OPENAI_API_KEY` | Chave API OpenAI | `sk-proj-xxx` |
| `OPENROUTER_API_KEY` | Chave API OpenRouter | `sk-or-v1-xxx` |

**Nota**: Pelo menos uma das duas deve estar configurada.

### Variáveis de controlo de custos

| Variável | Descrição | Default |
|----------|-----------|---------|
| `MAX_BUDGET_USD` | Limite de custo por run | `5.00` |
| `MAX_TOKENS_TOTAL` | Limite de tokens por run | `500000` |

### Variáveis de configuração

| Variável | Descrição | Default |
|----------|-----------|---------|
| `API_TIMEOUT` | Timeout em segundos | `180` |
| `API_MAX_RETRIES` | Número de retries | `5` |
| `LOG_LEVEL` | Nível de log | `INFO` |
| `OUTPUT_DIR` | Diretório de outputs | `outputs` |
| `HISTORICO_DIR` | Diretório de histórico | `historico` |
| `DATA_DIR` | Diretório de dados | `data` |

### Source of truth
O ficheiro `.env` na **raiz** do projeto é o único local de configuração.
Nunca comitar `.env` para git (está no `.gitignore`).

---

## 5. ARQUITETURA

### Diagrama de componentes

```
┌─────────────────────────────────────────────────────────────────┐
│                    STREAMLIT UI (app.py)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────────┐ │
│  │ Analisar │  │ Histórico│  │ Perguntas│  │ Configurações   │ │
│  │ Documento│  │          │  │ Adicionais│  │                 │ │
│  └────┬─────┘  └────┬─────┘  └─────┬────┘  └────────┬────────┘ │
└───────┼─────────────┼──────────────┼────────────────┼──────────┘
        │             │              │                │
        ▼             │              ▼                │
┌───────────────────────────────────────────────────────────────┐
│                    BACKDESK LAYER                              │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │              TribunalProcessor (processor.py)            │  │
│  │  ┌────────────────────────────────────────────────────┐ │  │
│  │  │ FASE 1: EXTRAÇÃO                                   │ │  │
│  │  │ E1(Claude) + E2(Gemini) + E3(GPT) + E4 + E5       │ │  │
│  │  │              ↓ Agregador (LOSSLESS)                │ │  │
│  │  └────────────────────────────────────────────────────┘ │  │
│  │  ┌────────────────────────────────────────────────────┐ │  │
│  │  │ FASE 2: AUDITORIA                                  │ │  │
│  │  │ A1(GPT) + A2(Claude) + A3(Gemini)                 │ │  │
│  │  │              ↓ Chefe (LOSSLESS)                    │ │  │
│  │  └────────────────────────────────────────────────────┘ │  │
│  │  ┌────────────────────────────────────────────────────┐ │  │
│  │  │ FASE 3: JULGAMENTO                                 │ │  │
│  │  │ J1(GPT) + J2(Claude) + J3(Gemini) + Q&A           │ │  │
│  │  └────────────────────────────────────────────────────┘ │  │
│  │  ┌────────────────────────────────────────────────────┐ │  │
│  │  │ FASE 4: PRESIDENTE                                 │ │  │
│  │  │ Veredicto Final + Q&A Consolidado                  │ │  │
│  │  └────────────────────────────────────────────────────┘ │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ LLM Client   │  │ Document     │  │ Legal Verifier        │ │
│  │ (OpenAI +    │  │ Loader       │  │ (DRE + SQLite cache)  │ │
│  │  OpenRouter) │  │              │  │                       │ │
│  └──────────────┘  └──────────────┘  └───────────────────────┘ │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                  Cost Controller                          │  │
│  │  Budget tracking + Token limits + Blocking                │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         DADOS                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ outputs/     │  │ historico/   │  │ data/                 │  │
│  │ <run_id>/    │  │ <run_id>.json│  │ legislacao_pt.db      │  │
│  └──────────────┘  └──────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Fluxo de dados

1. **Input**: Documento (PDF/DOCX/XLSX/TXT) + Área do Direito + Perguntas (opcional)
2. **Fase 1**: 5 extratores processam → Agregador consolida (LOSSLESS)
3. **Fase 2**: 3 auditores analisam → Chefe consolida (LOSSLESS)
4. **Fase 3**: 3 juízes emitem parecer → respondem Q&A
5. **Fase 4**: Presidente emite veredicto → consolida Q&A
6. **Verificação**: Citações legais verificadas contra DRE
7. **Output**: Ficheiros em `outputs/<run_id>/` + histórico

---

## 6. ÁRVORE COMPLETA DO REPO

```
TRIBUNAL_GOLDENMASTER_GUI/
├── .env.example                # Template de configuração
├── .gitignore                  # Ficheiros ignorados por git
├── requirements.txt            # Dependências Python
├── README.md                   # Documentação principal
│
├── src/                        # Código fonte principal
│   ├── __init__.py
│   ├── app.py                  # Entrypoint Streamlit (UI)
│   ├── config.py               # Configuração e modelos
│   ├── llm_client.py           # Cliente LLM unificado
│   ├── document_loader.py      # Carregador de documentos
│   ├── legal_verifier.py       # Verificador de legislação
│   ├── cost_controller.py      # Controlo de custos
│   │
│   ├── pipeline/               # Pipeline de processamento
│   │   ├── __init__.py
│   │   ├── processor.py        # Processador principal (4 fases)
│   │   ├── constants.py        # Constantes do pipeline
│   │   ├── pdf_safe.py         # Extração PDF segura
│   │   └── extractor_json.py   # Extração estruturada
│   │
│   ├── components/             # Componentes UI reutilizáveis
│   │   ├── __init__.py
│   │   ├── components_api_config.py    # Gestão API keys
│   │   └── components_model_selector.py # Seleção de modelos
│   │
│   ├── perguntas/              # Subsistema Q&A
│   │   ├── __init__.py
│   │   ├── pipeline_perguntas.py       # Pipeline Q&A
│   │   └── tab_perguntas.py            # UI Q&A
│   │
│   ├── ui/                     # Componentes UI adicionais
│   │   ├── __init__.py
│   │   └── page_repair.py      # Reparação de páginas PDF
│   │
│   └── utils/                  # Utilitários
│       ├── __init__.py
│       ├── perguntas.py        # Parsing de perguntas
│       ├── metadata_manager.py # Gestão de metadata
│       └── cleanup.py          # Limpeza de temporários
│
├── data/                       # Dados e BD
│   ├── schema.sql              # Schema da BD
│   ├── create_db.py            # Script criação BD
│   └── legislacao_pt.db        # BD SQLite (gerada)
│
├── tests/                      # Testes pytest
│   ├── __init__.py
│   ├── conftest.py             # Fixtures
│   ├── test_pipeline_txt.py    # Testes do pipeline
│   ├── test_document_loader.py # Testes do loader
│   └── test_legal_verifier_offline.py  # Testes do verifier
│
├── fixtures/                   # Inputs de teste
│   └── sample_input.txt        # Documento exemplo
│
├── docker/                     # Docker setup
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── entrypoint.sh
│   └── README_DOCKER_SECTION.md
│
├── docs/                       # Documentação
│   ├── HANDOVER.md             # Este documento
│   └── BACKDESK_API_INTERNA.md # Contratos internos
│
├── outputs/                    # Outputs (gerados, ignorados por git)
│   └── <run_id>/
│       ├── resultado.json
│       ├── RESUMO.md
│       ├── fase1_*.md
│       ├── fase2_*.md
│       ├── fase3_*.md
│       ├── fase4_presidente.md
│       └── verificacao_legal.md
│
└── historico/                  # Histórico (gerado, ignorado por git)
    └── <run_id>.json
```

---

## 7. FLUXOS UI (PÁGINAS, ESTADOS, PERMISSÕES)

### Páginas disponíveis

| Página | Descrição | Acesso |
|--------|-----------|--------|
| Analisar Documento | Upload e análise de documentos | Principal |
| Analisar Texto | Análise de texto direto | Principal |
| Histórico | Lista de análises anteriores | Consulta |
| Perguntas Adicionais | Q&A sobre análises existentes | Consulta |
| Gerir Títulos | Editar títulos das análises | Gestão |
| API Keys | Configurar chaves API | Configuração |
| Configurações | Ver modelos e testar conexão | Configuração |
| Como Funciona | Documentação/ajuda | Informação |

### Session State

```python
st.session_state = {
    "processor": TribunalProcessor,      # Instância do processador
    "resultado": PipelineResult | None,  # Resultado atual
    "resultados_multiplos": List,        # Para batch
    "pagina": str,                        # Página atual
    "documentos_carregados": List,       # Docs carregados
    "model_choices": Dict,                # Modelos selecionados
    "pdf_bytes_cache": Dict,              # Cache de PDFs
    "pdf_out_dirs": Dict,                 # Diretórios PDF safe
}
```

### Fluxo de análise

1. Utilizador faz upload de documento(s)
2. Sistema extrai texto (loader)
3. Se PDF, opção de usar "PDF Seguro" (página a página)
4. Utilizador seleciona área do direito
5. Utilizador (opcional) escreve perguntas
6. Utilizador seleciona modelos premium
7. Clica "ANALISAR"
8. Pipeline executa 4 fases
9. Resultados apresentados com tabs
10. Opção de download JSON/MD

---

## 8. BACKDESK (SERVIÇOS, ENTRADAS/SAÍDAS)

Ver documento separado: `docs/BACKDESK_API_INTERNA.md`

### Resumo dos serviços

| Serviço | Módulo | Função principal |
|---------|--------|------------------|
| Pipeline | `processor.py` | `TribunalProcessor.processar()` |
| LLM | `llm_client.py` | `UnifiedLLMClient.chat()` |
| Documentos | `document_loader.py` | `DocumentLoader.load()` |
| Legislação | `legal_verifier.py` | `LegalVerifier.verificar_texto()` |
| Custos | `cost_controller.py` | `CostController.register_usage()` |

---

## 9. DADOS (FORMATOS, SCHEMAS)

### Formato de output principal: `resultado.json`

```json
{
  "run_id": "20260205_143022_abc12345",
  "documento": {
    "filename": "contrato.pdf",
    "extension": ".pdf",
    "text": "...",
    "num_pages": 5,
    "num_chars": 15000,
    "num_words": 2500
  },
  "area_direito": "Civil",
  "fase1_extracoes": [...],
  "fase1_agregado_consolidado": "...",
  "fase2_auditorias": [...],
  "fase2_chefe_consolidado": "...",
  "fase3_pareceres": [...],
  "fase3_presidente": "...",
  "verificacoes_legais": [...],
  "veredicto_final": "PROCEDENTE",
  "simbolo_final": "✓",
  "status_final": "aprovado",
  "perguntas_utilizador": ["Pergunta 1?"],
  "respostas_juizes_qa": [...],
  "total_tokens": 65000,
  "total_latencia_ms": 180000,
  "timestamp_inicio": "2026-02-05T14:30:22",
  "timestamp_fim": "2026-02-05T14:33:22"
}
```

### Schema SQLite (`legislacao_cache`)

```sql
CREATE TABLE legislacao_cache (
    id TEXT PRIMARY KEY,
    diploma TEXT NOT NULL,
    artigo TEXT NOT NULL,
    numero TEXT,
    alinea TEXT,
    texto TEXT,
    fonte TEXT,
    timestamp TEXT,
    hash TEXT,
    verificado INTEGER DEFAULT 1
);
```

---

## 10. INTEGRAÇÕES EXTERNAS

### OpenAI API

| Parâmetro | Valor |
|-----------|-------|
| Base URL | `https://api.openai.com/v1` |
| Endpoints | `/chat/completions`, `/responses` |
| Modelos | gpt-4o, gpt-4o-mini, gpt-5.2, gpt-5.2-pro |
| Timeout | 180s |
| Max retries | 5 |

### OpenRouter API

| Parâmetro | Valor |
|-----------|-------|
| Base URL | `https://openrouter.ai/api/v1` |
| Endpoint | `/chat/completions` |
| Modelos | anthropic/claude-*, google/gemini-*, deepseek/*, etc. |
| Headers | Authorization, HTTP-Referer, X-Title |
| Timeout | 180s |
| Max retries | 5 |

### DRE (Diário da República)

| Parâmetro | Valor |
|-----------|-------|
| Base URL | `https://diariodarepublica.pt` |
| Search URL | `https://diariodarepublica.pt/dr/pesquisa` |
| Método | GET com query params |
| Rate limit | Respeitar robots.txt |
| Fallback | Cache local SQLite |

---

## 11. LOGGING E ERROS

### Níveis de log

```python
DEBUG   - Detalhes de debugging
INFO    - Fluxo normal de execução
WARNING - Situações não ideais mas recuperáveis
ERROR   - Erros que interrompem operação
```

### Onde são escritos

- **Console**: stdout/stderr
- **Streamlit**: st.info(), st.warning(), st.error()
- **Ficheiros**: Nenhum por defeito (configurável)

### Erros comuns e tratamento

| Erro | Causa | Tratamento |
|------|-------|------------|
| `BudgetExceededError` | Custo > limite | Bloqueia pipeline |
| `TokenLimitExceededError` | Tokens > limite | Bloqueia pipeline |
| `httpx.TimeoutException` | API lenta | Retry automático |
| `httpx.HTTPStatusError` | Erro HTTP | Retry ou fallback |

---

## 12. TESTES

### Correr testes

```bash
# Todos os testes
pytest -q

# Com cobertura
pytest --cov=src --cov-report=html

# Testes específicos
pytest tests/test_document_loader.py -v
```

### Estrutura dos testes

| Ficheiro | Testa |
|----------|-------|
| `test_pipeline_txt.py` | Pipeline, resultados, custos |
| `test_document_loader.py` | Carregamento de documentos |
| `test_legal_verifier_offline.py` | Verificação legal (offline) |

### Fixtures importantes

- `sample_txt_path`: Caminho para fixture de texto
- `temp_output_dir`: Diretório temporário
- `sample_documento_content`: Documento de exemplo
- `mock_llm_response`: Resposta LLM mock

---

## 13. CUSTOS (CONTROLO ATIVO)

### Limites por defeito

| Limite | Valor | Env var |
|--------|-------|---------|
| Budget | $5.00 | `MAX_BUDGET_USD` |
| Tokens | 500,000 | `MAX_TOKENS_TOTAL` |

### Preços por modelo (por 1M tokens)

| Modelo | Input | Output |
|--------|-------|--------|
| gpt-4o | $2.50 | $10.00 |
| gpt-4o-mini | $0.15 | $0.60 |
| claude-opus-4.5 | $15.00 | $75.00 |
| claude-3.5-sonnet | $3.00 | $15.00 |
| gemini-3-flash | $0.075 | $0.30 |

### O que é registado

- Tokens por fase (prompt + completion)
- Custo estimado por fase
- Total acumulado
- Percentagem de uso vs limite

### Onde é guardado

- `resultado.json`: totais
- `metadata.json`: resumo
- UI: indicador em tempo real

---

## 14. PONTOS FRÁGEIS / DÍVIDA TÉCNICA / TODOs

### Dívida técnica conhecida

1. **Chunking**: Documentos muito grandes podem ter perda de contexto
2. **OCR**: PDFs escaneados não são suportados (sem pytesseract ativo)
3. **Concorrência**: Não há suporte para múltiplos utilizadores simultâneos
4. **Testes**: Cobertura de testes pode ser melhorada
5. **Autenticação**: Não há sistema de login (single-user)

### TODOs futuros

- [ ] Adicionar OCR para PDFs escaneados
- [ ] Implementar cache de respostas LLM
- [ ] Adicionar streaming de respostas
- [ ] Suportar mais idiomas além de PT
- [ ] Melhorar rate limiting para DRE

### Riscos conhecidos

- **APIs externas**: Dependência de disponibilidade e preços
- **DRE**: Estrutura do site pode mudar
- **Modelos**: Versões de modelos podem ser descontinuadas

---

## 15. CHECKLIST FINAL

### Incluído neste pacote

- [x] Código fonte completo (`src/`)
- [x] Configuração (`.env.example`, `.gitignore`)
- [x] Dependências (`requirements.txt`)
- [x] Base de dados (`data/schema.sql`, `create_db.py`)
- [x] Testes (`tests/`)
- [x] Docker (`docker/`)
- [x] Documentação (`docs/`)
- [x] Fixtures (`fixtures/`)
- [x] Controlo de custos (`cost_controller.py`)
- [x] Utilitário cleanup (`cleanup.py`)

### NÃO incluído (por segurança)

- [ ] `.env` (segredos)
- [ ] `outputs/` (dados de runtime)
- [ ] `historico/` (dados de runtime)
- [ ] `*.db` (base de dados populada)
- [ ] `venv/` (ambiente virtual)

### Validação realizada

- [ ] App local executa: `streamlit run src/app.py`
- [ ] Testes passam: `pytest -q`
- [ ] Docker build: `docker compose up --build`

---

## CONTACTO / SUPORTE

Para questões sobre este documento ou o sistema, reportar issues no repositório.

---

*Documento gerado automaticamente para handover do Tribunal GoldenMaster v2.0*

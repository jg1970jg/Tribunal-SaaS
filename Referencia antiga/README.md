# TRIBUNAL GOLDENMASTER

Sistema de análise jurídica com **Inteligência Artificial** para **Direito Português**.

Pipeline de 4 fases com múltiplos modelos LLM (OpenAI + OpenRouter).

---

## Funcionalidades

- **Extração multi-modelo**: 5 LLMs extraem informação em paralelo (LOSSLESS)
- **Auditoria cruzada**: 3 LLMs auditam e validam a extração
- **Julgamento colegial**: 3 LLMs emitem parecer + Presidente decide
- **Verificação legal**: Verifica citações contra DRE (Diário da República)
- **Perguntas Q&A**: Permite perguntas específicas sobre documentos
- **Controlo de custos**: Budget e tokens limitados por execução

---

## Instalação Rápida

### Windows

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

# 6. Inicializar base de dados
python data/create_db.py

# 7. Executar
streamlit run src/app.py
```

### Linux/Mac

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

# 6. Inicializar base de dados
python data/create_db.py

# 7. Executar
streamlit run src/app.py
```

Acesso: **http://localhost:8501**

---

## Pipeline de 4 Fases

### FASE 1: Extração (5 modelos + Agregador)

```
Documento → E1(Claude) + E2(Gemini) + E3(GPT) + E4(DeepSeek) + E5(Qwen)
                                    ↓
                         Agregador (LOSSLESS)
```

### FASE 2: Auditoria (3 modelos + Chefe)

```
Extração → A1(GPT) + A2(Claude) + A3(Gemini)
                        ↓
                   Chefe (LOSSLESS)
```

### FASE 3: Julgamento (3 modelos)

```
Auditoria → J1(GPT) + J2(Claude) + J3(Gemini) + Q&A
```

### FASE 4: Presidente

```
Pareceres → Veredicto Final + Q&A Consolidado
```

---

## Símbolos de Verificação

| Símbolo | Significado |
|---------|-------------|
| ✓ | Procedente/Aprovado |
| ✗ | Improcedente/Rejeitado |
| ⚠ | Parcialmente Procedente/Atenção |

---

## Formatos Suportados

| Formato | Extensão |
|---------|----------|
| PDF | `.pdf` |
| Word | `.docx` |
| Excel | `.xlsx` |
| Texto | `.txt` |

---

## Configuração (.env)

```env
# API Keys (pelo menos uma obrigatória)
OPENAI_API_KEY=sk-proj-...
OPENROUTER_API_KEY=sk-or-v1-...

# Controlo de custos
MAX_BUDGET_USD=5.00
MAX_TOKENS_TOTAL=500000

# Configurações API
API_TIMEOUT=180
API_MAX_RETRIES=5
LOG_LEVEL=INFO
```

---

## Testes

```bash
# Correr todos os testes
pytest -q

# Com cobertura
pytest --cov=src --cov-report=html
```

---

## Docker

```bash
# Build e iniciar
cd docker
docker compose up --build

# Ou em background
docker compose up -d

# Parar
docker compose down
```

Ver `docker/README_DOCKER_SECTION.md` para mais detalhes.

---

## Estrutura do Projeto

```
TRIBUNAL_GOLDENMASTER_GUI/
├── src/                # Código fonte
│   ├── app.py          # Entrypoint Streamlit
│   ├── config.py       # Configuração
│   ├── llm_client.py   # Cliente LLM
│   ├── document_loader.py
│   ├── legal_verifier.py
│   ├── cost_controller.py
│   └── pipeline/       # Pipeline de processamento
├── data/               # BD e schema
├── tests/              # Testes pytest
├── docker/             # Docker setup
├── docs/               # Documentação
│   ├── HANDOVER.md
│   └── BACKDESK_API_INTERNA.md
└── fixtures/           # Inputs de teste
```

---

## Documentação

- **HANDOVER.md**: Documento completo para migração/handover
- **BACKDESK_API_INTERNA.md**: Contratos internos para integração

Ver pasta `docs/` para documentação completa.

---

## Custos Estimados

| Configuração | Custo por Análise |
|--------------|-------------------|
| Modelos económicos | ~$0.05 - $0.15 |
| Mista | ~$0.20 - $0.50 |
| Modelos premium | ~$0.50 - $1.50 |

*Valores aproximados, dependem do tamanho do documento.*

---

## Troubleshooting

### "API Key não configurada"
```cmd
copy .env.example .env
notepad .env  # Preencher chaves
```

### "ModuleNotFoundError"
```cmd
venv\Scripts\activate
pip install -r requirements.txt
```

### "Streamlit não abre"
Abrir manualmente: http://localhost:8501

### "Budget excedido"
Aumentar `MAX_BUDGET_USD` no `.env` ou usar documento menor.

---

## Aviso Legal

Este sistema é uma **ferramenta de apoio** para análise jurídica.
**NÃO substitui aconselhamento jurídico profissional.**

---

## Licença

MIT License - Uso livre para fins educacionais e profissionais.

---

*Tribunal GoldenMaster v2.0 - Apenas Direito Português*

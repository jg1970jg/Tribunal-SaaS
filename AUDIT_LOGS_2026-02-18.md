# AUDITORIA DE LOGS - LexForum Pipeline
**Data:** 2026-02-18
**Sessoes analisadas:** 2 (pesquisa 19:10-19:25 + pesquisa 22:07-em curso)

---

## 1. RESUMO EXECUTIVO

| Metrica | Pesquisa 1 (19:10) | Pesquisa 2 (22:07) |
|---------|--------------------|--------------------|
| Duracao total | ~75 min (19:10→20:25 estimado) | Em curso |
| Erros criticos | 5 | 2+ (em curso) |
| Fases com falhas | Extracao, Relatoria, Legal, Curador | Extracao |
| Modelo mais problematico | amazon/nova-pro-v1 (100% falha) | amazon/nova-pro-v1 (100% falha) |
| Modelo mais fiavel | anthropic/claude-sonnet-4.6 (suplente 100% sucesso) | Em curso |

---

## 2. ERROS E BUGS POR FASE

### FASE 1: EXTRACAO (7 IAs paralelas)

#### BUG CRITICO: E6 (Nova Pro v1) — Taxa de falha: 100%

**Causa raiz:** `MODEL_MAX_OUTPUT["amazon/nova-pro-v1"] = 5_120` tokens. Este limite e absurdamente baixo para chunks de 50,000 caracteres. O modelo tenta produzir ~18,000-18,800 chars mas e cortado aos ~5,120 tokens (~18K chars), resultando em `finish_reason=length`.

**Evidencia dos logs:**
```
Pesquisa 1:
  19:10:41 — E6 chunk 13: truncado (18670 chars) → fallback sonnet-4.6
  19:14:17 — E6 chunk 16: truncado (18670 chars) → fallback sonnet-4.6
  19:11:33 — TIMEOUT: E6 nao acabou em 1200s → continuou com 6/7 extractores

Pesquisa 2 (chunks consecutivos, 100% falha):
  22:09:31 — E6 chunk 2: truncado (18,045 chars) → fallback sonnet-4.6
  22:10:58 — E6 chunk 3: truncado (18,885 chars) → fallback sonnet-4.6
  22:12:52 — E6 chunk 4: truncado (17,822 chars) → fallback sonnet-4.6
  22:14:27 — E6 chunk 5: truncado (17,814 chars) → fallback sonnet-4.6
  (em curso — mais chunks a caminho)
```

**Padrao de truncagem:** Output sempre entre 17,800-18,900 chars (~4,500-4,700 tokens).
Isto confirma que o modelo atinge o hard limit de 5,120 tokens em TODOS os chunks.

**Cadencia:** ~2 min por chunk (falha + fallback Sonnet). Com N chunks, E6 demora ~2N minutos.

**Impacto:**
- E6 falha em TODOS os chunks → Sonnet 4.6 faz o trabalho como suplente
- Na pesquisa 1, E6 deu timeout (1200s) antes de todos os chunks falharem/serem processados
- Custo desperdicado: cada chamada ao Nova Pro que trunca gasta tokens de input sem output util
- Latencia adicionada: ~90s por chunk (tempo de falha + tempo de fallback)

**Recomendacao:**
- Opcao A: Aumentar `MODEL_MAX_OUTPUT["amazon/nova-pro-v1"]` de 5,120 para pelo menos 16,384
- Opcao B: Reduzir chunk size para E6 especificamente (25K em vez de 50K)
- Opcao C: Substituir Nova Pro por outro modelo como extractor E6 (ex: Gemini Flash, GPT-4o-mini)
- Opcao D: Remover Nova Pro e promover Sonnet 4.6 a titular do E6 (ja faz o trabalho todo)

#### TIMEOUT: E6 — 1200s deadline excedido

**Config:** `EXTRACTOR_TIMEOUT_MIN = 1200` (20 min)

Na pesquisa 1, E6 excedeu o deadline porque estava a processar chunks sequencialmente com fallbacks. Cada chunk falhado demora ~90s (chamada Nova Pro + timeout + chamada Sonnet 4.6), e com 16+ chunks o tempo explode.

**Calculo:**
- 16 chunks × ~90s por chunk (falha + fallback) = ~1440s > 1200s deadline

---

### FASE 2: AUDITORIA (4 IAs + Consensus)

#### WARNING: Consensus — 3 omissoes suspeitas

```
19:21:43 — [CONSENSUS] 3 omissao(oes) suspeita(s)
```

**Significado:** O consensus engine (Fase B3) detectou que pelo menos 1 auditor encontrou algo que os outros nao mencionaram. Isto pode ser:
- Falso positivo: um auditor sobre-reportou
- Falha real: 3 auditores falharam em detectar algo

**Impacto:** Moderado — o sistema regista mas nao corrige automaticamente (Phase C com re-query so activa apos 30 runs historicos).

---

### FASE 3: RELATORIA / JULGAMENTO (3 Juizes)

#### BUG CRITICO: NO_CITATIONS — 3/3 relatores falharam

```
19:22:21 — relator_2_json: NO_CITATIONS (retry 1/1) — 0 citations
19:22:27 — relator_1_json: NO_CITATIONS (retry 1/2) — 0 citations
19:22:48 — relator_3_json: NO_CITATIONS (retry 1/2) — 0 citations
```

**Causa raiz provavel:** Efeito cascata da extracao incompleta (E6 timeout). Se a fase de extracao produziu dados incompletos, os relatores nao tem excerpts/offsets suficientes para gerar citations validas.

**Detalhes do quality gate (de `performance_tracker.py:468`):**
```python
# Findings existem mas total_citations == 0
has_items = len(parsed.get("findings", [])) > 0 or len(parsed.get("decision_points", [])) > 0
if has_items and total_citations == 0:
    return {"code": "NO_CITATIONS", "critical": True, ...}
```

**Retries:**
- relator_2: apenas 1 retry (MODEL_MAX_RETRIES pode estar a 1 para este modelo)
- relator_1: 1/2 retries (pode ter passado no retry 2, ou nao — log nao mostra)
- relator_3: 1/2 retries (idem)

**Impacto:** CRITICO — relatorio final sem fundamentacao documental. O utilizador recebe conclusoes sem provas.

**Recomendacao:**
- Investigar se a consolidacao (agregador) preserva os offsets/excerpts corretamente
- Verificar se o prompt dos relatores pede explicitamente citations com start_char/end_char
- Considerar adicionar um fallback que gera citations sinteticas a partir dos evidence_items

---

### FASE 4: VERIFICACAO LEGAL

#### WARNING: nid=109 — Legislacao sem versao historica

```
19:24:42 — [LEGAL] Nenhuma versao de nid=109 anterior a 21/06/2024
(repetido 6 vezes)
```

**Causa raiz (de `legal_verifier.py:868`):**
```python
# Data dos factos anterior a todas as versoes conhecidas
# — a lei pode nao ter existido nessa data
logger.warning(f"[LEGAL] Nenhuma versao de nid={nid} anterior a ...")
return versions[0][0]  # Usa primeira versao como aproximacao
```

**Significado:** O documento refere legislacao (nid=109) com data anterior a todas as versoes na base de dados PGDL. O sistema usa a versao mais antiga como aproximacao, mas esta pode nao ser a versao correcta vigente a data dos factos.

**Impacto:** Moderado — a versao legislativa citada pode ter redacao diferente da vigente. Em contexto juridico, isto pode invalidar argumentos baseados nessa legislacao.

**Repetido 6 vezes:** Sugere que 6 citations diferentes referenciam o mesmo diploma (nid=109), possivelmente o mesmo artigo em diferentes findings.

**Recomendacao:**
- Identificar que diploma e nid=109 (consultar tabela `nid_cache` ou PGDL)
- Se for legislacao recente, adicionar versoes historicas a base de dados
- Se for legislacao antiga (pre-digital), marcar como "versao nao verificada" no relatorio

---

### FASE 5: CURADOR SENIOR

#### BUG: Q7 — Leak de metadados internos do pipeline

```
19:25:45 — [CURADOR] Quality gate falhou (iteracao 1/2):
  ['Q7: Referencias a fases/papeis do pipeline encontradas']
```

**Causa raiz (de `processor.py:3805`):**
```python
# Q7: Zero referencias a fases do pipeline
if re.search(r'[Ff]ase\s+\d|[Aa]uditor|[Rr]elator|\bpipeline\b|
              \bagente\b|\bextrat(?:or|ores)\b', texto, re.IGNORECASE):
    falhas.append("Q7: Referencias a fases/papeis do pipeline encontradas")
```

**Significado:** O relatorio final continha termos como "Fase 1", "Auditor", "Relator", "pipeline", "extrator", etc. — que sao termos INTERNOS do sistema e nao devem ser visiveis ao utilizador final.

**Impacto:** O curador teve 2 iteracoes para corrigir. Se corrigiu na iteracao 2, o texto final esta limpo. Se falhou em ambas, o relatorio final tem `[REVISAO MANUAL RECOMENDADA]` prepended.

**Recomendacao:**
- Reforcar no prompt do Conselheiro-Mor/Presidente que termos internos sao proibidos
- Adicionar um filtro pos-processamento que substitui termos internos automaticamente
- Verificar se os relatores estao a usar terminologia interna nos seus outputs

---

## 3. EFICIENCIA POR MODELO DE IA

### Ranking de Fiabilidade (baseado nos logs)

| Modelo | Funcao | Taxa Sucesso | Problemas | Notas |
|--------|--------|-------------|-----------|-------|
| `anthropic/claude-sonnet-4.6` | E4 + Suplente E6 | **100%** | Nenhum | Faz todo o trabalho de E6 como suplente |
| `anthropic/claude-haiku-4.5` | E1 | **~100%** | Nenhum nos logs | Champion extractor (955 items/doc) |
| `openai/gpt-5.2` | E3 + A1 + J1 + Agregador | **~100%** | Nenhum nos logs | Workhouse do pipeline |
| `google/gemini-3-pro-preview` | E2 + A2 | **~100%** | Nenhum nos logs | Visual extractor |
| `deepseek/deepseek-r1` | J2 | **Sem dados** | Context limitado a 64K | Pode ter problemas com docs grandes |
| `anthropic/claude-opus-4.6` | J3 + A5(Gold) | **Sem dados** | Nenhum nos logs | Modelo premium |
| `alibaba/qwen3-max-thinking` | A4 | **Sem dados** | Nenhum nos logs | "Advogado do Diabo" |
| `amazon/nova-pro-v1` | E6 | **0%** | 100% truncagem | max_output=5120 insuficiente |
| `nvidia/llama-3.1-nemotron-70b` | E7 | **Sem dados** | Nenhum nos logs | Baseline extractor |
| `meta-llama/llama-3.3-70b` | E5 | **Sem dados** | Nenhum nos logs | Baseline extractor |

### Modelos que geraram erros de quality gate:

| Modelo | Role | Erro | Retries |
|--------|------|------|---------|
| `amazon/nova-pro-v1` | E6 | OUTPUT_TRUNCATED (finish_reason=length) | N/A (fallback) |
| J1/J2/J3 (nao identificados) | relator_1/2/3_json | NO_CITATIONS | 1-2 retries |
| Presidente/Curador | curador | Q7: Leak de pipeline | 1/2 iteracoes |

---

## 4. EFICIENCIA POR FASE

| Fase | Duracao Estimada | Status | Bottleneck |
|------|-----------------|--------|------------|
| **Fase 0: Triagem** | <30s | OK | — |
| **Fase 1: Extracao** | ~10 min (normal), 20 min (com E6 timeout) | PROBLEMATICA | E6 Nova Pro (100% falha + timeout) |
| **Fase 1b: Agregacao** | ~2-3 min | Sem dados | — |
| **Fase 2: Auditoria** | ~2 min | WARNING | 3 omissoes suspeitas |
| **Fase 2b: Consensus** | <1 min | OK | — |
| **Fase 3: Relatoria** | ~2-3 min | CRITICO | 3/3 relatores sem citations |
| **Fase 4: Legal** | ~2 min | WARNING | nid=109 sem versao historica |
| **Fase 5: Curador** | ~2-5 min | FALHOU (iter 1) | Q7 leak de pipeline |

### Timeline da Pesquisa 1 (completa):

```
19:10:41 ─── FASE 1: Extracao ───────────────────────
  │  E6 chunk 13 falhou (Nova Pro truncou)
  │  ...mais chunks a falhar...
19:11:33 ─── E6 TIMEOUT (1200s) ─────────────────────
  │  Pipeline continua com 6/7 extractores
  │  ...
19:14:17 ─── E6 chunk 16 falhou (ultimo registado)
  │
19:21:43 ─── FASE 2: Consensus ──────────────────────
  │  3 omissoes suspeitas
  │
19:22:21 ─── FASE 3: Relatoria ──────────────────────
  │  relator_2: NO_CITATIONS
  │  relator_1: NO_CITATIONS
  │  relator_3: NO_CITATIONS
  │
19:24:42 ─── FASE 4: Legal Verifier ─────────────────
  │  nid=109 sem versao (×6)
  │
19:25:45 ─── FASE 5: Curador ───────────────────────
  │  Q7 falhou (iter 1/2)
  │  (resultado final nao visivel nos logs)
```

---

## 5. PROBLEMAS DE INFRAESTRUTURA

### Render Deploy + Restart inesperado

```
21:49:13 — Deploying...
21:49:44 — Application startup complete
21:49:54 — Service is live
21:50:53 — Shutting down (59s apos live!)
21:54:54 — Service detected running again
```

**Problema:** O servico fez shutdown 59 segundos apos ficar live. Possiveis causas:
- Rolling restart do Render (deploy anterior a terminar)
- Health check falhou (HEAD / retorna 404)
- Memoria insuficiente no plano free/starter

**Nota:** `HEAD / → 404 Not Found` — o Render faz health check na raiz, mas o FastAPI nao tem rota `/`. Isto pode causar restarts inesperados.

**Recomendacao:** Adicionar rota GET `/` que retorna 200 (ou redirect para `/health`).

### Area nao-standard: "Multi-area"

```
22:07:55 — Area de direito nao standard: Multi-area
```

**Config (main.py:54):**
```python
VALID_AREAS = {"Civil", "Penal", "Trabalho", "Administrativo",
               "Fiscal", "Comercial", "Familia", "Outro"}
```

"Multi-area" nao esta em VALID_AREAS. O sistema aceita mas loga warning. Se a area afecta os prompts ou a seleccao de modelos, isto pode degradar a qualidade.

---

## 6. ACOES RECOMENDADAS (por prioridade)

### P0 — CRITICO (corrigir ja)

1. **E6 Nova Pro: Aumentar max_output ou substituir modelo**
   - Ficheiro: `src/config.py:447`
   - Valor actual: `"amazon/nova-pro-v1": 5_120`
   - Valor recomendado: `16_384` ou substituir por outro modelo
   - Impacto: Elimina 100% das falhas E6 + elimina timeout de 1200s

2. **Investigar NO_CITATIONS nos 3 relatores**
   - Ficheiro: `src/performance_tracker.py:468`
   - Verificar se o agregador preserva offsets e excerpts
   - Verificar se os prompts dos relatores pedem citations correctamente
   - Pode ser efeito cascata do E6 timeout (menos dados → menos citations)

### P1 — ALTO

3. **Curador Q7: Reforcar filtragem de termos internos**
   - Ficheiro: `src/pipeline/processor.py:3805`
   - Adicionar pos-processamento que remove/substitui termos automaticamente
   - Reforcar no prompt que termos como "Fase", "Auditor", "Relator" sao proibidos

4. **Health check do Render: Adicionar rota /**
   - Ficheiro: `main.py`
   - Adicionar `@app.get("/")` que retorna 200
   - Evita restarts inesperados por health check falhado

### P2 — MEDIO

5. **Legal Verifier: nid=109 sem versao historica**
   - Ficheiro: `src/legal_verifier.py:868`
   - Identificar que diploma e nid=109
   - Adicionar versoes historicas ou marcar como "nao verificado"

6. **"Multi-area" como area valida**
   - Ficheiro: `main.py:54`
   - Considerar adicionar "Multi-area" a VALID_AREAS
   - Ou mapear para "Outro" automaticamente

### P3 — BAIXO (optimizacao)

7. **Eliminar desperdicio de tokens no Nova Pro**
   - Cada chamada falhada gasta tokens de input (~50K chars) sem retorno
   - Se o fix P0 nao for aplicado, skip Nova Pro para docs >10 chunks

8. **Consensus Phase C: Reduzir threshold de activacao**
   - Actual: 30 runs historicos para activar re-query
   - Considerar reduzir para 10-15 runs para feedback mais rapido

---

## 7. METRICAS DE CUSTO (estimativas)

### Custo desperdicado por Nova Pro:

| Item | Pesquisa 1 | Pesquisa 2 |
|------|-----------|-----------|
| Chunks falhados | >=3 (13, 16, +timeout) | >=2 (2, 3, +em curso) |
| Input tokens desperdicados | ~150K tokens (~$0.12) | ~100K+ tokens |
| Sonnet 4.6 suplente (custo extra) | ~$0.08 por chunk × 3 = $0.24 | Em curso |
| **Total desperdicado** | **~$0.36** | **Em curso** |

### Custo de retries por quality gates:

| Fase | Retries | Custo extra estimado |
|------|---------|---------------------|
| Relatores (NO_CITATIONS) | 3-6 retries total | ~$0.30-0.60 |
| Curador (Q7) | 1 retry | ~$0.15-0.30 |
| **Total retries** | **4-7** | **~$0.45-0.90** |

---

---

## 8. CORRECOES IMPLEMENTADAS (v5.1)

### Alteracao 1: E6 Nova Pro → GPT-5 Nano
- **Ficheiro:** `src/config.py` (LLM_CONFIGS, MODEL_MAX_OUTPUT, VISION_CAPABLE_MODELS)
- **Antes:** `amazon/nova-pro-v1` (max_output=5,120, $3.20/M, 100% falha)
- **Depois:** `openai/gpt-5-nano` (max_output=128,000, $0.40/M, visao)

### Alteracao 2: Suplentes universais de extracao
- **Ficheiro:** `src/config.py` (EXTRACTOR_SUBSTITUTES)
- **Suplente 1:** `openai/gpt-5-mini` ($2.00/M, visao, 128K output)
- **Suplente 2:** `google/gemini-2.5-flash` ($2.50/M, visao, 65K output)
- **Logica:** Se titular falha num chunk, suplente assume TODOS os chunks restantes

### Alteracao 3: Bronze — E4 Sonnet removido + J3 downgrade
- **Ficheiro:** `src/engine.py` (ja existia), `src/pipeline/processor.py` (filtro _llm_configs)
- **Bronze extractores:** 6 IAs (sem E4 Sonnet 4.6)
- **Bronze J3:** `claude-opus-4.6` → `claude-sonnet-4.6`

### Alteracao 4: Logging Fase 0
- **Ficheiro:** `src/pipeline/processor.py`
- Logs explicitos: dominio, confianca, consenso, votos, fotos, duracao

### Alteracao 5: Custos Bronze actualizados
- **Ficheiro:** `src/tier_config.py`
- **Antes:** $10.10 real, $20.20 cliente
- **Depois:** $6.01 real, $12.02 cliente (-40%)

### Alteracao 6: Micro-chunking removido
- **Ficheiro:** `src/pipeline/processor.py`
- Logica de micro-chunking (workaround Nova Pro) removida — desnecessaria com gpt-5-nano

*Relatório gerado automaticamente a partir da análise de logs do Render em 2026-02-18.*

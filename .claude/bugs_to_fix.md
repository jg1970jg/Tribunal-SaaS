# Bugs a Corrigir — Diamante Analysis (15 Fev 2026)

## BUG 1: Consensus Engine crash (CRITICO)
- **Ficheiro:** `src/pipeline/consensus_engine.py`, linha 73 e 96
- **Erro:** `CitationValidationResult.__init__() missing 1 required positional argument: 'status'`
- **Fix:** Linha 73: mudar `status: str` para `status: str = "PENDING"`
- **Impacto:** Todo o consensus engine silenciosamente ignorado. Zero validação de citações, zero detecção de contradições, zero normalização de doc_id. Causa directa dos 80 erros MetaIntegrity.

## BUG 2: Wallet sub-bloqueio (2 sub-bugs)
- **Ficheiros:** `src/tier_config.py` + `src/wallet_manager.py`
- **Problema:** Bloqueou $9.78, gastou $10.87 → deficit de $1.09
- **Causa raiz A:** `tier_config.py` — estimated_costs para Silver somam $3.40, mas custo real é ~$5.44. Estimativas desactualizadas.
- **Causa raiz B:** `wallet_manager.py` — settle_credits() não verifica se custo_cliente > blocked_amount. Extra é debitado silenciosamente do saldo disponível.
- **Fix A:** Actualizar estimated_costs em tier_config.py (extraction: 1.00, audit: 1.80, judgment: 1.80, president: 0.40 = $5.00)
- **Fix B:** Adicionar warning de overrun + considerar aumentar SAFETY_MARGIN de 1.25 para 1.50
- **Nota:** Admin (jgsena1970@gmail.com) tem no-balance-blocking, portanto não é afectado. Mas utilizadores normais seriam.

## BUG 3: Agregador truncado — Quality Gate errada (ALTO)
- **Ficheiro:** `src/performance_tracker.py`, linha 408
- **Causa raiz:** O agregador produz MARKDOWN (conforme SYSTEM_AGREGADOR), mas o quality gate classifica-o como role JSON por ter "agregador" no nome. Resultado: tentativa 1 com 4,404 tokens de Markdown CORRECTO é rejeitada por `JSON_NO_OPEN_BRACE`. Tentativa 2 com instrução contraditória ("responde só JSON") gera stub de 165 tokens.
- **Impacto:** A deduplicação semântica dos 2,985 items foi PERDIDA. O programmatic union (simples) existe, mas a consolidação inteligente por LLM foi destruída.
- **Fix:** Em `performance_tracker.py` linha 408, remover `"agregador"` da lista de JSON roles: `for tag in ("_json", "consolidador", "chefe")` (sem "agregador")

## BUG 4: GPT-5.2 output_text vazio — Ruído nos logs (BAIXO)
- **Ficheiro:** `src/llm_client.py`, linha 713
- **Causa raiz:** OpenAI Responses API não popula o campo top-level `output_text`. Dados vivem em `output[].content[].text`. O fallback (linhas 715-741) extrai correctamente.
- **Impacto:** ZERO perda de dados. Apenas 17 warnings desnecessários nos logs.
- **Fix:** Mudar `logger.warning` na linha 713 para `logger.debug`, ou reestruturar para tentar formato nested primeiro (canonical) e `output_text` como fallback.

## BUG 5: Token budget excedido 4.8x sem bloqueio (MEDIO)
- **Ficheiro:** `src/pipeline/processor.py` (NÃO o cost_controller)
- **Causa raiz:** O cost_controller TEM código de bloqueio funcional (`_check_limits()` com `TokenLimitExceededError` e `BudgetExceededError`). MAS os 5 call sites no processor.py passam TODOS `raise_on_exceed=False`, E envolvem a chamada em `try/except Exception: pass`. O bloqueio é impossível.
- **Linhas afectadas no processor.py:** 878, 1064, 1115, 1566, 2178
- **Fix:** Mudar pelo menos os call sites principais para `raise_on_exceed=True` e remover o `except Exception: pass`. Considerar bloqueio graceful que guarda resultados parciais antes de parar.

## BUG 6: Page mapper sem marcadores DOCX (MEDIO)
- **Ficheiro:** `src/document_loader.py` (extractor DOCX, linhas 209-250)
- **Causa raiz:** O extractor PDF insere `[Página X]` por página. O extractor DOCX NÃO insere marcadores porque `python-docx` não tem conceito de páginas (é flow-format). Resultado: 184,715 chars tratados como "página 1".
- **Impacto:** Todas as citações reportam page_num=1, coverage por página inútil, detecção de páginas ilegíveis desactivada, chunks sem localização no documento.
- **Fix:** Opção A: Inserir marcadores sintéticos no DOCX (cada ~3000 chars). Opção B: Detectar `<w:br w:type="page"/>` no XML do DOCX. Opção C: Aceitar limitação e ajustar componentes downstream.
- **Relação com MetaIntegrity:** Indirecta — degrada validação por página mas os 80 erros são do Bug 1 (consensus engine).

## BUG 7: Legal verifier sem versões temporais (BAIXO)
- **Ficheiro:** `src/legal_verifier.py`, método `_version_at_date()` (linhas 807-838) + `_parse_version_date()` (linhas 632-677)
- **Causa raiz:** O parser de datas do PGDL não consegue extrair datas de publicação das versões do Código Penal (nid=109) e Código de Processo Penal (nid=199) anteriores a 21/06/2024 (data dos factos do documento). Fallback: usa primeira versão conhecida como aproximação.
- **Impacto:** A verificação temporal não confirma exactamente a versão em vigor na data dos factos. Verifica artigo na versão mais antiga + versão actual. Impacto moderado — os artigos provavelmente existiam, mas o texto pode diferir.
- **Fix:** Melhorar `_parse_version_date()` para lidar com mais formatos de data do PGDL.

## BUG 8: Temperatura LLM demasiado alta (DESIGN)
- **Ficheiro:** `src/pipeline/processor.py`, método `_call_llm()` (linha 949)
- **Causa raiz:** Temperature default = 0.7 para TODAS as chamadas LLM do pipeline. Com ~12+ chamadas em cadeia (5 extractores + 4 auditores + 3 juízes + 1 presidente), a aleatoriedade compõe-se exponencialmente.
- **Impacto:** Confiança varia entre 17% e 64% no mesmo documento. Run 3 obteve 17% provavelmente por combinação de LLM raw confidence baixa + penalties de integridade a atingir ceiling.
- **Fix:** Reduzir temperature para 0.1-0.2 para chamadas do pipeline (manter 0.7 apenas para Q&A criativas).
- **Nota:** NÃO é bug — é decisão de design. Mas temperatura mais baixa daria resultados mais consistentes.

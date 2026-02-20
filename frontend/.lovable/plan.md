

## Bug: Coluna "Custo" mostra $0.00 no Historico de Analises

### Problema
A coluna "Custo" na pagina de Diagnostico Tecnico (AdminDiagnostics) le o valor de `analysis_result.custos.wallet.custo_real` dentro do JSON. Quando esse campo nao existe no JSON, mostra $0.00 (fallback `?? 0`).

O backend agora guarda os custos em colunas dedicadas na tabela `documents`, mas essas colunas ainda nao existem na base de dados.

### Plano

**Passo 1 - Adicionar colunas a tabela `documents`**

Criar migracao SQL para adicionar:
- `custo_real_usd` (numeric, nullable, default null) — custo real das APIs
- `custo_cobrado_usd` (numeric, nullable, default null) — custo cobrado ao cliente

**Passo 2 - Actualizar AdminDiagnostics.tsx**

Na tabela "Historico de Analises" (linha ~411), mudar:

```
// DE:
const cost = a?.custos?.wallet?.custo_real ?? 0;

// PARA:
const cost = (doc as any).custo_cobrado_usd ?? a?.custos?.wallet?.custo_real ?? 0;
```

Isto usa a coluna dedicada como fonte primaria, com fallback para o JSON (retrocompatibilidade com analises antigas).

Tambem no grafico de tendencia de custos (linha ~336), aplicar a mesma logica de fallback.

**Passo 3 - Actualizar DocumentDetails.tsx**

Na barra de stats (linha ~378-379), mudar para ler `custo_cobrado_usd` da coluna do documento com fallback para o JSON:

```
const custoCobrado = (document as any).custo_cobrado_usd 
  ?? (analysis as any)?.custos?.custo_total_usd;
```

**Passo 4 - Garantir que o SELECT inclui as novas colunas**

O Dashboard e DocumentDetails usam `select("*")`, portanto as novas colunas serao incluidas automaticamente. Nao e necessario alterar os queries.

### Detalhes Tecnicos

- As colunas sao nullable porque analises antigas nao terao estes valores
- O fallback para o JSON garante retrocompatibilidade
- O tipo `numeric` e mais adequado que `real` para valores monetarios
- O ficheiro `types.ts` sera regenerado automaticamente apos a migracao


#!/bin/bash
# ============================================================
# TRIBUNAL GOLDENMASTER - Entrypoint Script
# ============================================================
# Inicializa a base de dados se necessário e inicia a aplicação.
# ============================================================

set -e

echo "============================================================"
echo "TRIBUNAL GOLDENMASTER - Inicialização"
echo "============================================================"

# Verificar se a base de dados existe
DB_PATH="/app/data/legislacao_pt.db"
SCHEMA_PATH="/app/data/schema.sql"

if [ ! -f "$DB_PATH" ]; then
    echo "Base de dados não encontrada. Criando..."

    if [ -f "$SCHEMA_PATH" ]; then
        python /app/data/create_db.py
        echo "Base de dados criada com sucesso!"
    else
        echo "AVISO: Schema não encontrado em $SCHEMA_PATH"
        echo "A base de dados será criada automaticamente pelo LegalVerifier."
    fi
else
    echo "Base de dados encontrada: $DB_PATH"
fi

# Criar diretórios necessários (caso não existam)
mkdir -p /app/outputs /app/historico /app/data

# Verificar variáveis de ambiente críticas
if [ -z "$OPENAI_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ]; then
    echo "============================================================"
    echo "AVISO: Nenhuma API Key configurada!"
    echo "Configure OPENAI_API_KEY ou OPENROUTER_API_KEY no .env"
    echo "============================================================"
fi

# Mostrar configuração
echo ""
echo "Configuração:"
echo "  - LOG_LEVEL: ${LOG_LEVEL:-INFO}"
echo "  - MAX_BUDGET_USD: ${MAX_BUDGET_USD:-5.00}"
echo "  - MAX_TOKENS_TOTAL: ${MAX_TOKENS_TOTAL:-500000}"
echo "  - API Timeout: ${API_TIMEOUT:-180}s"
echo ""

echo "Iniciando Streamlit..."
echo "============================================================"

# Executar comando passado (ou default)
exec "$@"

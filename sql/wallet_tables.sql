-- ============================================================
-- WALLET SYSTEM - Tribunal SaaS V2
-- ============================================================
-- Executar no Supabase SQL Editor (com permissões de admin).
--
-- Tabelas:
--   1. wallet_balances  — saldo actual por utilizador
--   2. wallet_transactions — histórico de débitos e créditos
--   3. wallet_config — configuração dinâmica (markup, etc.)
--
-- NOTA: Estas tabelas usam service_role key no backend,
-- mas têm RLS para proteger acesso directo pelo frontend.
-- ============================================================


-- ============================================================
-- 1. WALLET_BALANCES
-- ============================================================

CREATE TABLE IF NOT EXISTS wallet_balances (
    user_id    UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    balance_usd NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trigger para auto-update de updated_at
CREATE OR REPLACE FUNCTION update_wallet_balances_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_wallet_balances_updated_at
    BEFORE UPDATE ON wallet_balances
    FOR EACH ROW
    EXECUTE FUNCTION update_wallet_balances_updated_at();

-- RLS: utilizador só vê o seu próprio saldo
ALTER TABLE wallet_balances ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own balance"
    ON wallet_balances FOR SELECT
    USING (auth.uid() = user_id);

-- Service role pode fazer tudo (backend)
CREATE POLICY "Service role full access on wallet_balances"
    ON wallet_balances FOR ALL
    USING (auth.role() = 'service_role');

COMMENT ON TABLE wallet_balances IS 'Saldo pré-pago de cada utilizador em USD';


-- ============================================================
-- 2. WALLET_TRANSACTIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS wallet_transactions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    type           TEXT NOT NULL CHECK (type IN ('debit', 'credit')),
    amount_usd     NUMERIC(12, 4) NOT NULL,
    cost_real_usd  NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
    markup_applied NUMERIC(6, 4) NOT NULL DEFAULT 1.0000,
    run_id         TEXT,
    description    TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índices para queries frequentes
CREATE INDEX IF NOT EXISTS idx_wallet_tx_user_id
    ON wallet_transactions(user_id);

CREATE INDEX IF NOT EXISTS idx_wallet_tx_created_at
    ON wallet_transactions(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_wallet_tx_type
    ON wallet_transactions(type);

CREATE INDEX IF NOT EXISTS idx_wallet_tx_user_type
    ON wallet_transactions(user_id, type);

-- RLS: utilizador só vê as suas próprias transações
ALTER TABLE wallet_transactions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own transactions"
    ON wallet_transactions FOR SELECT
    USING (auth.uid() = user_id);

-- Service role pode fazer tudo (backend)
CREATE POLICY "Service role full access on wallet_transactions"
    ON wallet_transactions FOR ALL
    USING (auth.role() = 'service_role');

COMMENT ON TABLE wallet_transactions IS 'Histórico de débitos e créditos da wallet';
COMMENT ON COLUMN wallet_transactions.type IS 'debit = análise executada, credit = carregamento de saldo';
COMMENT ON COLUMN wallet_transactions.amount_usd IS 'Valor debitado/creditado ao utilizador (com markup se debit)';
COMMENT ON COLUMN wallet_transactions.cost_real_usd IS 'Custo real das APIs (só para debits)';
COMMENT ON COLUMN wallet_transactions.markup_applied IS 'Multiplicador aplicado (ex: 1.47 = 40% lucro + 7% segurança)';
COMMENT ON COLUMN wallet_transactions.run_id IS 'ID do pipeline run (só para debits)';


-- ============================================================
-- 3. WALLET_CONFIG
-- ============================================================

CREATE TABLE IF NOT EXISTS wallet_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trigger para auto-update de updated_at
CREATE OR REPLACE FUNCTION update_wallet_config_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_wallet_config_updated_at
    BEFORE UPDATE ON wallet_config
    FOR EACH ROW
    EXECUTE FUNCTION update_wallet_config_updated_at();

-- RLS: só service_role (backend) acede
ALTER TABLE wallet_config ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on wallet_config"
    ON wallet_config FOR ALL
    USING (auth.role() = 'service_role');

COMMENT ON TABLE wallet_config IS 'Configuração dinâmica do sistema de wallet';

-- Inserir valores default
INSERT INTO wallet_config (key, value) VALUES
    ('markup_multiplier', '1.47'),
    ('min_balance_usd', '0.50')
ON CONFLICT (key) DO NOTHING;


-- ============================================================
-- MIGRAÇÃO: Se existir tabela user_wallets antiga, migrar dados
-- ============================================================
-- Descomente se precisar migrar da tabela antiga:
--
-- INSERT INTO wallet_balances (user_id, balance_usd)
-- SELECT user_id, balance
-- FROM user_wallets
-- ON CONFLICT (user_id) DO UPDATE
--     SET balance_usd = EXCLUDED.balance_usd;

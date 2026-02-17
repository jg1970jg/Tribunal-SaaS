-- ============================================================
-- MIGRAÇÃO: Backend → Lovable Supabase (drpuexbgdfdnhabctfhi)
-- ============================================================
-- Data: 2026-02-17
--
-- O Lovable Supabase já tem: documents, user_wallets, user_roles
-- Este script adiciona as tabelas que o backend precisa:
--   1. profiles (saldo USD, bloqueios)
--   2. blocked_credits (bloqueios activos durante análise)
--   3. wallet_transactions (histórico de débitos/créditos)
--   4. wallet_balances (fallback, opcional)
--   5. RPCs atómicas (block/settle/cancel)
--   6. Actualiza handle_new_user() para criar profiles
--
-- INSTRUÇÕES: Colar TODO este SQL no Supabase SQL Editor e executar.
-- ============================================================


-- ============================================================
-- 1. PROFILES — extensão do auth.users para o backend
-- ============================================================
CREATE TABLE IF NOT EXISTS public.profiles (
    id               UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email            TEXT NOT NULL DEFAULT '',
    full_name        TEXT NOT NULL DEFAULT '',
    credits_balance  NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
    credits_blocked  NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RLS
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "profiles_select_own"
    ON public.profiles FOR SELECT
    USING (auth.uid() = id);

CREATE POLICY "profiles_update_own"
    ON public.profiles FOR UPDATE
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);

-- Service role (backend) pode fazer tudo
CREATE POLICY "profiles_service_role_all"
    ON public.profiles FOR ALL
    USING (auth.role() = 'service_role');


-- ============================================================
-- 2. BLOCKED_CREDITS — bloqueios durante análise
-- ============================================================
CREATE TABLE IF NOT EXISTS public.blocked_credits (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    analysis_id   UUID NOT NULL,
    amount        NUMERIC(12, 4) NOT NULL,
    status        TEXT NOT NULL DEFAULT 'blocked'
                  CHECK (status IN ('blocked', 'settled', 'cancelled')),
    settled_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_blocked_credits_user_id
    ON public.blocked_credits(user_id);
CREATE INDEX IF NOT EXISTS idx_blocked_credits_analysis_id
    ON public.blocked_credits(analysis_id);
CREATE INDEX IF NOT EXISTS idx_blocked_credits_status
    ON public.blocked_credits(status);

-- RLS
ALTER TABLE public.blocked_credits ENABLE ROW LEVEL SECURITY;

CREATE POLICY "blocked_credits_select_own"
    ON public.blocked_credits FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "blocked_credits_service_role_all"
    ON public.blocked_credits FOR ALL
    USING (auth.role() = 'service_role');


-- ============================================================
-- 3. WALLET_TRANSACTIONS — histórico de débitos e créditos
-- ============================================================
CREATE TABLE IF NOT EXISTS public.wallet_transactions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    type             TEXT NOT NULL CHECK (type IN ('debit', 'credit')),
    amount_usd       NUMERIC(12, 4) NOT NULL,
    balance_after_usd NUMERIC(12, 4),
    cost_real_usd    NUMERIC(12, 4) DEFAULT 0.0000,
    markup_applied   NUMERIC(6, 4) DEFAULT 1.0000,
    run_id           TEXT,
    description      TEXT NOT NULL DEFAULT '',
    admin_id         UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wallet_tx_user_id
    ON public.wallet_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_wallet_tx_created_at
    ON public.wallet_transactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wallet_tx_type
    ON public.wallet_transactions(type);

-- RLS
ALTER TABLE public.wallet_transactions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "wallet_tx_select_own"
    ON public.wallet_transactions FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "wallet_tx_service_role_all"
    ON public.wallet_transactions FOR ALL
    USING (auth.role() = 'service_role');


-- ============================================================
-- 4. WALLET_BALANCES — tabela auxiliar (fallback do backend)
-- ============================================================
CREATE TABLE IF NOT EXISTS public.wallet_balances (
    user_id     UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    balance_usd NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.wallet_balances ENABLE ROW LEVEL SECURITY;

CREATE POLICY "wallet_balances_select_own"
    ON public.wallet_balances FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "wallet_balances_service_role_all"
    ON public.wallet_balances FOR ALL
    USING (auth.role() = 'service_role');


-- ============================================================
-- 5. ACTUALIZAR handle_new_user() — agora também cria profiles
-- ============================================================
-- O trigger original (do Lovable) só criava user_wallets.
-- Agora também cria um perfil na tabela profiles.

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    -- Criar wallet (Lovable original — 3 créditos iniciais)
    INSERT INTO public.user_wallets (user_id, balance)
    VALUES (NEW.id, 3)
    ON CONFLICT (user_id) DO NOTHING;

    -- Criar perfil para o backend (saldo $0 — admin credita manualmente)
    INSERT INTO public.profiles (id, email, credits_balance, credits_blocked)
    VALUES (
        NEW.id,
        COALESCE(NEW.email, ''),
        0.0000,
        0.0000
    )
    ON CONFLICT (id) DO NOTHING;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ============================================================
-- 6. RPCs ATÓMICAS — eliminar race conditions no wallet
-- ============================================================

-- 6.1 Bloquear créditos atomicamente
CREATE OR REPLACE FUNCTION public.block_credits_atomic(
    p_user_id UUID,
    p_analysis_id TEXT,
    p_amount NUMERIC
) RETURNS JSONB AS $$
DECLARE
    v_balance NUMERIC;
    v_blocked NUMERIC;
    v_available NUMERIC;
BEGIN
    SELECT credits_balance, credits_blocked
    INTO v_balance, v_blocked
    FROM profiles
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'user_not_found'
        );
    END IF;

    v_available := COALESCE(v_balance, 0) - COALESCE(v_blocked, 0);

    IF v_available < p_amount THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'insufficient_credits',
            'available', v_available,
            'required', p_amount
        );
    END IF;

    UPDATE profiles
    SET credits_blocked = COALESCE(credits_blocked, 0) + p_amount
    WHERE id = p_user_id;

    INSERT INTO blocked_credits (user_id, analysis_id, amount, status)
    VALUES (p_user_id, p_analysis_id::uuid, p_amount, 'blocked');

    RETURN jsonb_build_object(
        'success', true,
        'blocked_amount', p_amount,
        'balance_after', v_available - p_amount
    );
END;
$$ LANGUAGE plpgsql;

-- 6.2 Liquidar créditos atomicamente
CREATE OR REPLACE FUNCTION public.settle_credits_atomic(
    p_analysis_id TEXT,
    p_real_cost_usd NUMERIC,
    p_markup NUMERIC DEFAULT 2.0
) RETURNS JSONB AS $$
DECLARE
    v_block RECORD;
    v_custo_cliente NUMERIC;
    v_refunded NUMERIC;
    v_balance NUMERIC;
    v_blocked NUMERIC;
    v_new_balance NUMERIC;
BEGIN
    SELECT id, user_id, amount
    INTO v_block
    FROM blocked_credits
    WHERE analysis_id = p_analysis_id::uuid AND status = 'blocked'
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'no_block_found'
        );
    END IF;

    v_custo_cliente := p_real_cost_usd * p_markup;
    v_refunded := GREATEST(0, v_block.amount - v_custo_cliente);

    SELECT credits_balance, credits_blocked
    INTO v_balance, v_blocked
    FROM profiles
    WHERE id = v_block.user_id
    FOR UPDATE;

    v_new_balance := GREATEST(0, COALESCE(v_balance, 0) - v_custo_cliente);

    UPDATE profiles
    SET credits_balance = v_new_balance,
        credits_blocked = GREATEST(0, COALESCE(v_blocked, 0) - v_block.amount)
    WHERE id = v_block.user_id;

    UPDATE blocked_credits
    SET status = 'settled', settled_at = NOW()
    WHERE id = v_block.id;

    INSERT INTO wallet_transactions (user_id, type, amount_usd, balance_after_usd, cost_real_usd, markup_applied, run_id, description)
    VALUES (v_block.user_id, 'debit', v_custo_cliente, v_new_balance, p_real_cost_usd, p_markup, p_analysis_id, 'Liquidação relatoria ' || p_analysis_id);

    RETURN jsonb_build_object(
        'success', true,
        'blocked', v_block.amount,
        'real_cost', p_real_cost_usd,
        'custo_cliente', v_custo_cliente,
        'refunded', v_refunded,
        'new_balance', v_new_balance
    );
END;
$$ LANGUAGE plpgsql;

-- 6.3 Cancelar bloqueio atomicamente
CREATE OR REPLACE FUNCTION public.cancel_block_atomic(
    p_analysis_id TEXT
) RETURNS JSONB AS $$
DECLARE
    v_block RECORD;
BEGIN
    SELECT id, user_id, amount
    INTO v_block
    FROM blocked_credits
    WHERE analysis_id = p_analysis_id::uuid AND status = 'blocked'
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'error', 'no_block_found');
    END IF;

    UPDATE profiles
    SET credits_blocked = GREATEST(0, COALESCE(credits_blocked, 0) - v_block.amount)
    WHERE id = v_block.user_id;

    UPDATE blocked_credits
    SET status = 'cancelled', settled_at = NOW()
    WHERE id = v_block.id;

    RETURN jsonb_build_object(
        'success', true,
        'released', v_block.amount
    );
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- 7. CRIAR PERFIL PARA UTILIZADORES EXISTENTES
-- ============================================================
-- Os utilizadores que já se registaram no Lovable Supabase
-- têm user_wallets mas NÃO têm profiles.
-- Este INSERT cria profiles para todos os users existentes.

INSERT INTO public.profiles (id, email, credits_balance, credits_blocked)
SELECT
    u.id,
    COALESCE(u.email, ''),
    0.0000,
    0.0000
FROM auth.users u
WHERE NOT EXISTS (
    SELECT 1 FROM public.profiles p WHERE p.id = u.id
)
ON CONFLICT (id) DO NOTHING;


-- ============================================================
-- RESULTADO ESPERADO:
-- ============================================================
-- Novas tabelas: profiles, blocked_credits, wallet_transactions, wallet_balances
-- Funções actualizadas: handle_new_user (agora cria profiles + user_wallets)
-- Novas funções: block_credits_atomic, settle_credits_atomic, cancel_block_atomic
-- Perfis criados para utilizadores existentes
-- ============================================================

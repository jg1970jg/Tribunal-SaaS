-- ============================================================
-- WALLET ATOMIC RPCs
-- Executar no Supabase SQL Editor para eliminar race conditions
-- ============================================================

-- 1. Bloquear créditos atomicamente
CREATE OR REPLACE FUNCTION block_credits_atomic(
    p_user_id UUID,
    p_analysis_id TEXT,
    p_amount NUMERIC
) RETURNS JSONB AS $$
DECLARE
    v_balance NUMERIC;
    v_blocked NUMERIC;
    v_available NUMERIC;
BEGIN
    -- Lock the row (SELECT ... FOR UPDATE)
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

    -- Atomic increment (not set)
    UPDATE profiles
    SET credits_blocked = COALESCE(credits_blocked, 0) + p_amount
    WHERE id = p_user_id;

    -- Insert block record
    INSERT INTO blocked_credits (user_id, analysis_id, amount, status)
    VALUES (p_user_id, p_analysis_id, p_amount, 'blocked');

    RETURN jsonb_build_object(
        'success', true,
        'blocked_amount', p_amount,
        'balance_after', v_available - p_amount
    );
END;
$$ LANGUAGE plpgsql;


-- 2. Liquidar créditos atomicamente
CREATE OR REPLACE FUNCTION settle_credits_atomic(
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
    -- Find and lock the block
    SELECT id, user_id, amount
    INTO v_block
    FROM blocked_credits
    WHERE analysis_id = p_analysis_id AND status = 'blocked'
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

    -- Lock and read profile
    SELECT credits_balance, credits_blocked
    INTO v_balance, v_blocked
    FROM profiles
    WHERE id = v_block.user_id
    FOR UPDATE;

    v_new_balance := GREATEST(0, COALESCE(v_balance, 0) - v_custo_cliente);

    -- Atomic update: debit balance and release block
    UPDATE profiles
    SET credits_balance = v_new_balance,
        credits_blocked = GREATEST(0, COALESCE(v_blocked, 0) - v_block.amount)
    WHERE id = v_block.user_id;

    -- Mark block as settled
    UPDATE blocked_credits
    SET status = 'settled', settled_at = NOW()
    WHERE id = v_block.id;

    -- Record transaction
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


-- 3. Cancelar bloqueio atomicamente
CREATE OR REPLACE FUNCTION cancel_block_atomic(
    p_analysis_id TEXT
) RETURNS JSONB AS $$
DECLARE
    v_block RECORD;
BEGIN
    -- Find and lock the block
    SELECT id, user_id, amount
    INTO v_block
    FROM blocked_credits
    WHERE analysis_id = p_analysis_id AND status = 'blocked'
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'error', 'no_block_found');
    END IF;

    -- Atomic decrement
    UPDATE profiles
    SET credits_blocked = GREATEST(0, COALESCE(credits_blocked, 0) - v_block.amount)
    WHERE id = v_block.user_id;

    -- Mark as cancelled
    UPDATE blocked_credits
    SET status = 'cancelled', settled_at = NOW()
    WHERE id = v_block.id;

    RETURN jsonb_build_object(
        'success', true,
        'released', v_block.amount
    );
END;
$$ LANGUAGE plpgsql;

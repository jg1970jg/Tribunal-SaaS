-- ============================================================
-- TRIBUNAL SAAS V2 - DATABASE SCHEMA (Supabase / PostgreSQL)
-- ============================================================
-- Baseado na lógica do cost_controller.py e config.py
-- da versão anterior (Referencia antiga).
--
-- Tabelas:
--   1. profiles       → Dados do utilizador (ligado a auth.users)
--   2. user_wallets   → Saldo atual do utilizador
--   3. transactions   → Histórico de carregamentos e gastos
--   4. audit_logs     → Registo de cada execução do Tribunal (tokens, custos)
-- ============================================================


-- ============================================================
-- 1. PROFILES
-- ============================================================
-- Extensão do auth.users do Supabase.
-- Criado automaticamente quando um utilizador faz registo.

CREATE TABLE public.profiles (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name   TEXT NOT NULL DEFAULT '',
    nif         TEXT NOT NULL DEFAULT '',
    morada      TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índice para pesquisa por NIF
CREATE INDEX idx_profiles_nif ON public.profiles(nif);

-- Trigger: criar perfil automaticamente quando um user se regista
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.profiles (id)
    VALUES (NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- Trigger: atualizar updated_at automaticamente
CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER on_profiles_updated
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW
    EXECUTE FUNCTION public.update_updated_at();


-- ============================================================
-- 2. USER_WALLETS
-- ============================================================
-- Saldo do utilizador em EUR.
-- Criado automaticamente junto com o perfil.

CREATE TABLE public.user_wallets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    balance     NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
    currency    TEXT NOT NULL DEFAULT 'EUR',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Saldo nunca pode ser negativo
    CONSTRAINT balance_non_negative CHECK (balance >= 0)
);

CREATE INDEX idx_wallets_user_id ON public.user_wallets(user_id);

-- Trigger: criar wallet automaticamente quando o perfil é criado
CREATE OR REPLACE FUNCTION public.handle_new_profile()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.user_wallets (user_id)
    VALUES (NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_profile_created
    AFTER INSERT ON public.profiles
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_profile();

-- Trigger: atualizar updated_at
CREATE TRIGGER on_wallets_updated
    BEFORE UPDATE ON public.user_wallets
    FOR EACH ROW
    EXECUTE FUNCTION public.update_updated_at();


-- ============================================================
-- 3. TRANSACTIONS
-- ============================================================
-- Histórico completo de movimentos financeiros.
--   CREDIT = carregamento via Stripe
--   DEBIT  = gasto com execução de IA

CREATE TABLE public.transactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    amount              NUMERIC(12, 4) NOT NULL,
    transaction_type    TEXT NOT NULL CHECK (transaction_type IN ('CREDIT', 'DEBIT')),
    description         TEXT NOT NULL DEFAULT '',
    -- Referência ao pagamento Stripe (só para CREDIT)
    stripe_payment_id   TEXT,
    -- Saldo após esta transação (para auditoria)
    balance_after       NUMERIC(12, 4),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_transactions_user_id ON public.transactions(user_id);
CREATE INDEX idx_transactions_type ON public.transactions(transaction_type);
CREATE INDEX idx_transactions_created_at ON public.transactions(created_at DESC);


-- ============================================================
-- 4. AUDIT_LOGS
-- ============================================================
-- Registo detalhado de cada execução do Tribunal.
-- Baseado nos campos do cost_controller.py:
--   prompt_tokens, completion_tokens, cost, model, fase

CREATE TABLE public.audit_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    -- Tokens consumidos (mapeado do cost_controller)
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,
    -- Custo em EUR cobrado ao utilizador
    cost_eur            NUMERIC(10, 6) NOT NULL DEFAULT 0.000000,
    -- Ficheiro processado
    file_name           TEXT NOT NULL DEFAULT '',
    -- Status da execução
    status              TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'BUDGET_EXCEEDED')),
    -- Modelo de IA utilizado (ex: "openai/gpt-5.2")
    model_used          TEXT NOT NULL DEFAULT '',
    -- Duração em milissegundos
    duration_ms         INTEGER,
    -- Referência à transação de débito associada
    transaction_id      UUID REFERENCES public.transactions(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_user_id ON public.audit_logs(user_id);
CREATE INDEX idx_audit_logs_status ON public.audit_logs(status);
CREATE INDEX idx_audit_logs_created_at ON public.audit_logs(created_at DESC);


-- ============================================================
-- 5. ROW LEVEL SECURITY (RLS)
-- ============================================================
-- Cada utilizador só consegue ver/editar os seus próprios dados.

-- Ativar RLS em todas as tabelas
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

-- ── PROFILES ──
-- Utilizador pode ler e atualizar o seu próprio perfil
CREATE POLICY "profiles_select_own"
    ON public.profiles FOR SELECT
    USING (auth.uid() = id);

CREATE POLICY "profiles_update_own"
    ON public.profiles FOR UPDATE
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);

-- ── USER_WALLETS ──
-- Utilizador pode apenas ler o seu saldo (updates só via backend/service_role)
CREATE POLICY "wallets_select_own"
    ON public.user_wallets FOR SELECT
    USING (auth.uid() = user_id);

-- ── TRANSACTIONS ──
-- Utilizador pode ler o seu próprio histórico (inserts só via backend/service_role)
CREATE POLICY "transactions_select_own"
    ON public.transactions FOR SELECT
    USING (auth.uid() = user_id);

-- ── AUDIT_LOGS ──
-- Utilizador pode ler os seus próprios logs (inserts só via backend/service_role)
CREATE POLICY "audit_logs_select_own"
    ON public.audit_logs FOR SELECT
    USING (auth.uid() = user_id);

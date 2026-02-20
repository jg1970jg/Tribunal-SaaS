-- ============================================================
-- ADICIONAR TABELAS/COLUNAS PARA O FRONTEND LOVABLE
-- Projeto: vtwskjvabruebaxilxli
-- Data: 2026-02-17
-- ============================================================
-- Colar TODO este SQL no Supabase SQL Editor e clicar "Run"
-- ============================================================


-- ============================================================
-- 1. COLUNAS EM FALTA NA TABELA documents
-- ============================================================
-- O frontend Lovable usa estas colunas que ainda não existem

ALTER TABLE public.documents
  ADD COLUMN IF NOT EXISTS file_name TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS file_url TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS summary TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS risk_level TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS key_points JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS analysis_date TIMESTAMPTZ DEFAULT now();


-- ============================================================
-- 2. RLS NA TABELA documents (se ainda não tiver)
-- ============================================================
ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

-- Drop existing policies first (ignore errors if don't exist)
DROP POLICY IF EXISTS "documents_select_own" ON public.documents;
DROP POLICY IF EXISTS "documents_insert_own" ON public.documents;
DROP POLICY IF EXISTS "documents_update_own" ON public.documents;
DROP POLICY IF EXISTS "documents_delete_own" ON public.documents;
DROP POLICY IF EXISTS "documents_service_role_all" ON public.documents;

-- Users can CRUD their own documents
CREATE POLICY "documents_select_own"
    ON public.documents FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "documents_insert_own"
    ON public.documents FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "documents_update_own"
    ON public.documents FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "documents_delete_own"
    ON public.documents FOR DELETE
    USING (auth.uid() = user_id);

-- Service role (backend) can do everything
CREATE POLICY "documents_service_role_all"
    ON public.documents FOR ALL
    USING ((auth.jwt()->>'role') = 'service_role');


-- ============================================================
-- 3. TABELA user_wallets (para o frontend Lovable)
-- ============================================================
CREATE TABLE IF NOT EXISTS public.user_wallets (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    balance    INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.user_wallets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_wallets_select_own" ON public.user_wallets;
DROP POLICY IF EXISTS "user_wallets_update_own" ON public.user_wallets;
DROP POLICY IF EXISTS "user_wallets_service_role_all" ON public.user_wallets;

CREATE POLICY "user_wallets_select_own"
    ON public.user_wallets FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "user_wallets_update_own"
    ON public.user_wallets FOR UPDATE
    USING (auth.uid() = user_id);

-- Service role (backend) pode fazer tudo
CREATE POLICY "user_wallets_service_role_all"
    ON public.user_wallets FOR ALL
    USING ((auth.jwt()->>'role') = 'service_role');


-- ============================================================
-- 4. TABELA user_roles (para admin panel do Lovable)
-- ============================================================

-- Criar o enum se não existir
DO $$ BEGIN
    CREATE TYPE public.app_role AS ENUM ('admin', 'moderator', 'user');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS public.user_roles (
    id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role     public.app_role NOT NULL,
    UNIQUE(user_id, role)
);

ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_roles_select_own" ON public.user_roles;
DROP POLICY IF EXISTS "user_roles_service_role_all" ON public.user_roles;

CREATE POLICY "user_roles_select_own"
    ON public.user_roles FOR SELECT
    USING (auth.uid() = user_id);

-- Service role (backend) pode fazer tudo
CREATE POLICY "user_roles_service_role_all"
    ON public.user_roles FOR ALL
    USING ((auth.jwt()->>'role') = 'service_role');


-- ============================================================
-- 5. FUNÇÃO has_role() (usada pelo admin panel)
-- ============================================================
CREATE OR REPLACE FUNCTION public.has_role(_user_id UUID, _role public.app_role)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.user_roles
        WHERE user_id = _user_id AND role = _role
    );
$$;


-- ============================================================
-- 6. TRIGGER: criar wallet + profile quando novo user se regista
-- ============================================================
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    -- Criar wallet para o frontend (3 créditos iniciais)
    INSERT INTO public.user_wallets (user_id, balance)
    VALUES (NEW.id, 3)
    ON CONFLICT (user_id) DO NOTHING;

    -- Criar perfil para o backend (saldo $0 — admin credita)
    INSERT INTO public.profiles (id, email, credits_balance, credits_blocked)
    VALUES (NEW.id, COALESCE(NEW.email, ''), 0.0000, 0.0000)
    ON CONFLICT (id) DO NOTHING;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Garantir que o trigger existe
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();


-- ============================================================
-- 7. TRIGGER: updated_at nos documents
-- ============================================================
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_documents_updated_at ON public.documents;
CREATE TRIGGER update_documents_updated_at
    BEFORE UPDATE ON public.documents
    FOR EACH ROW
    EXECUTE FUNCTION public.update_updated_at_column();


-- ============================================================
-- 8. CRIAR WALLET + ROLE ADMIN PARA USERS EXISTENTES
-- ============================================================

-- Wallets para todos os users que ainda não têm
INSERT INTO public.user_wallets (user_id, balance)
SELECT u.id, 3
FROM auth.users u
WHERE NOT EXISTS (
    SELECT 1 FROM public.user_wallets w WHERE w.user_id = u.id
)
ON CONFLICT (user_id) DO NOTHING;

-- Dar role admin ao jgsena1970@gmail.com
INSERT INTO public.user_roles (user_id, role)
SELECT id, 'admin'::public.app_role
FROM auth.users
WHERE email = 'jgsena1970@gmail.com'
ON CONFLICT (user_id, role) DO NOTHING;


-- ============================================================
-- RESULTADO:
-- ============================================================
-- ✓ documents: colunas file_name, file_url, summary, risk_level, key_points, analysis_date
-- ✓ user_wallets: nova tabela para o frontend
-- ✓ user_roles: nova tabela para admin panel
-- ✓ has_role(): função para verificar roles
-- ✓ handle_new_user(): trigger actualizado (cria wallet + profile)
-- ✓ RLS em todas as tabelas
-- ✓ Admin role para jgsena1970@gmail.com
-- ============================================================

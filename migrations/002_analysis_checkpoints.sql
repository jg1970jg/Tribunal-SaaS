-- ============================================================
-- Migration 002: Analysis Checkpoints for Resume Support
-- ============================================================
-- Allows interrupted analyses to be resumed from the last completed phase.
-- Phase data is stored in Supabase (not local disk) to survive Render deploys.
-- ============================================================

-- 1. Nova tabela para guardar dados reais de cada fase
CREATE TABLE IF NOT EXISTS public.analysis_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    fase_num INTEGER NOT NULL,
    fase_nome TEXT NOT NULL,
    phase_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    pipeline_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(analysis_id, fase_num)
);

-- Indices para queries frequentes
CREATE INDEX IF NOT EXISTS idx_checkpoints_analysis_id ON public.analysis_checkpoints(analysis_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_user_id ON public.analysis_checkpoints(user_id);

-- RLS: utilizadores so veem os seus proprios checkpoints
ALTER TABLE public.analysis_checkpoints ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own checkpoints"
    ON public.analysis_checkpoints FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Service role full access to checkpoints"
    ON public.analysis_checkpoints FOR ALL
    USING (true)
    WITH CHECK (true);

-- 2. Novas colunas em documents para rastrear interrupcoes
ALTER TABLE public.documents
    ADD COLUMN IF NOT EXISTS analysis_id TEXT,
    ADD COLUMN IF NOT EXISTS last_completed_phase INTEGER DEFAULT -1,
    ADD COLUMN IF NOT EXISTS interrupted_at TIMESTAMPTZ;

-- Index para encontrar documentos interrompidos rapidamente
CREATE INDEX IF NOT EXISTS idx_documents_interrupted
    ON public.documents(status, interrupted_at)
    WHERE status = 'interrupted';

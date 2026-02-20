
-- Adicionar coluna 'filename' (o backend espera este nome)
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS filename TEXT;

-- Adicionar coluna 'analysis_date'
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS analysis_date TIMESTAMP WITH TIME ZONE DEFAULT NOW();

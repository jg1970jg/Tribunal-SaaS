
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS analysis_result jsonb DEFAULT NULL;

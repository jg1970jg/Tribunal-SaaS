-- ============================================================================
-- Pipeline v4.2: Per-page OCR checkpoints and chunk tracking
-- ============================================================================
-- Novas tabelas para suportar o pipeline de extracção modular:
--   - document_pages: checkpoint por página (OCR, limpeza, entidades)
--   - chunks: chunks semânticos com resultado de análise jurídica
-- ============================================================================

-- Tabela: document_pages
-- Guarda o estado de processamento de cada página do documento.
-- Permite retomar processamento interrompido (checkpoint/resume).
CREATE TABLE IF NOT EXISTS public.document_pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    page_num INTEGER NOT NULL,

    -- M2: Pre-processing
    image_hash TEXT,               -- SHA-256 da imagem pré-processada
    skew_angle FLOAT DEFAULT 0,    -- ângulo de deskew aplicado

    -- M3: OCR
    ocr_status TEXT DEFAULT 'pending',  -- pending, processing, completed, failed
    ocr_text TEXT,                      -- texto consenso final
    ocr_providers JSONB,               -- resultados por provider {google: {...}, azure: {...}}
    ocr_confidence FLOAT,              -- confiança média do consenso
    ocr_word_count INTEGER,            -- número de palavras detectadas

    -- M4: LLM Cleaning
    cleaned_text TEXT,                 -- texto após limpeza LLM
    cleaning_diff JSONB,               -- lista de alterações feitas pelo LLM
    cleaning_model TEXT,               -- modelo usado para limpeza

    -- M5: Entity Lock
    entities_locked JSONB,             -- entidades travadas nesta página

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    -- Uma entrada por página por análise
    UNIQUE(analysis_id, page_num)
);

CREATE INDEX IF NOT EXISTS idx_doc_pages_analysis ON public.document_pages(analysis_id);
CREATE INDEX IF NOT EXISTS idx_doc_pages_status ON public.document_pages(ocr_status);
CREATE INDEX IF NOT EXISTS idx_doc_pages_doc_id ON public.document_pages(doc_id);


-- Tabela: chunks
-- Guarda os chunks semânticos e o resultado da análise jurídica por chunk.
CREATE TABLE IF NOT EXISTS public.chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,

    -- Posição no documento
    start_page INTEGER,
    end_page INTEGER,
    start_char INTEGER,
    end_char INTEGER,
    token_count INTEGER,

    -- Conteúdo
    text TEXT,
    contains_table BOOLEAN DEFAULT FALSE,

    -- M5: Referências a entidades
    entity_refs JSONB,             -- lista de entity_ids que estão neste chunk

    -- M7: Análise jurídica
    analysis_status TEXT DEFAULT 'pending',  -- pending, processing, completed, failed
    analysis_result JSONB,                   -- resultado estruturado da análise
    analysis_model TEXT,                     -- modelo usado
    analysis_tokens INTEGER,                 -- tokens consumidos

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT now(),

    -- Um chunk por índice por análise
    UNIQUE(analysis_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_analysis ON public.chunks(analysis_id);
CREATE INDEX IF NOT EXISTS idx_chunks_status ON public.chunks(analysis_status);


-- ============================================================================
-- Row Level Security (RLS)
-- ============================================================================
-- Ambas as tabelas são acedidas apenas pelo backend via service role key.
-- RLS habilitado com políticas permissivas para service role.

ALTER TABLE public.document_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chunks ENABLE ROW LEVEL SECURITY;

-- Service role tem acesso total
CREATE POLICY "Service role full access to document_pages"
    ON public.document_pages
    FOR ALL
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role full access to chunks"
    ON public.chunks
    FOR ALL
    USING (true)
    WITH CHECK (true);


-- ============================================================================
-- Trigger: auto-update updated_at em document_pages
-- ============================================================================
CREATE OR REPLACE FUNCTION update_document_pages_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_document_pages_updated_at
    BEFORE UPDATE ON public.document_pages
    FOR EACH ROW
    EXECUTE FUNCTION update_document_pages_updated_at();

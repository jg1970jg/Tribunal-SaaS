-- ============================================================
-- TRIBUNAL GOLDENMASTER - Schema da Base de Dados
-- ============================================================
-- Ficheiro: data/schema.sql
-- Descrição: Schema completo para cache de legislação portuguesa
-- Uso: sqlite3 data/legislacao_pt.db < data/schema.sql
-- ============================================================

-- ---------------------------------------------------------
-- Tabela principal: Cache de legislação
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS legislacao_cache (
    id TEXT PRIMARY KEY,
    diploma TEXT NOT NULL,
    artigo TEXT NOT NULL,
    numero TEXT,
    alinea TEXT,
    texto TEXT,
    fonte TEXT,
    timestamp TEXT,
    hash TEXT,
    verificado INTEGER DEFAULT 1
);

-- Índice para pesquisa rápida por diploma e artigo
CREATE INDEX IF NOT EXISTS idx_diploma_artigo
ON legislacao_cache(diploma, artigo);

-- Índice para pesquisa por diploma
CREATE INDEX IF NOT EXISTS idx_diploma
ON legislacao_cache(diploma);

-- ---------------------------------------------------------
-- Tabela de metadados (opcional)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS db_metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Inserir versão do schema
INSERT OR REPLACE INTO db_metadata (key, value, updated_at)
VALUES ('schema_version', '1.0.0', datetime('now'));

INSERT OR REPLACE INTO db_metadata (key, value, updated_at)
VALUES ('created_at', datetime('now'), datetime('now'));

-- ---------------------------------------------------------
-- Diplomas pré-configurados (sem artigos - apenas referência)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS diplomas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sigla TEXT NOT NULL UNIQUE,
    nome_completo TEXT NOT NULL,
    tipo TEXT,  -- codigo, lei, decreto-lei, etc.
    data_publicacao TEXT,
    notas TEXT
);

-- Inserir diplomas comuns
INSERT OR IGNORE INTO diplomas (sigla, nome_completo, tipo) VALUES
('CC', 'Código Civil', 'codigo'),
('CP', 'Código Penal', 'codigo'),
('CT', 'Código do Trabalho', 'codigo'),
('CPC', 'Código de Processo Civil', 'codigo'),
('CPP', 'Código de Processo Penal', 'codigo'),
('CRP', 'Constituição da República Portuguesa', 'constituicao'),
('CCOM', 'Código Comercial', 'codigo'),
('CPA', 'Código do Procedimento Administrativo', 'codigo'),
('CPTA', 'Código de Processo nos Tribunais Administrativos', 'codigo'),
('RGIT', 'Regime Geral das Infracções Tributárias', 'lei'),
('LGT', 'Lei Geral Tributária', 'lei');

-- ---------------------------------------------------------
-- Tabela de estatísticas de verificação
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS verificacao_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT DEFAULT CURRENT_TIMESTAMP,
    total_verificacoes INTEGER DEFAULT 0,
    cache_hits INTEGER DEFAULT 0,
    dre_lookups INTEGER DEFAULT 0,
    encontrados INTEGER DEFAULT 0,
    nao_encontrados INTEGER DEFAULT 0
);

-- ---------------------------------------------------------
-- View útil: últimas verificações
-- ---------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_ultimas_verificacoes AS
SELECT
    id,
    diploma,
    artigo,
    numero,
    alinea,
    CASE WHEN verificado = 1 THEN 'Verificado' ELSE 'Não verificado' END as status,
    fonte,
    timestamp
FROM legislacao_cache
ORDER BY timestamp DESC
LIMIT 100;

-- ============================================================
-- FIM DO SCHEMA
-- ============================================================

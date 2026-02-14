# -*- coding: utf-8 -*-
"""
Create the 'documents' table in Supabase.
This table stores analysis results so they can be queried later.
"""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

url = os.environ["SUPABASE_URL"]
service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
project_ref = "vtwskjvabruebaxilxli"

headers = {
    "apikey": service_key,
    "Authorization": f"Bearer {service_key}",
    "Content-Type": "application/json",
}

SQL = """
-- Create documents table for storing analysis results
CREATE TABLE IF NOT EXISTS public.documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    analysis_result JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    tier TEXT NOT NULL DEFAULT 'bronze',
    area_direito TEXT NOT NULL DEFAULT 'Civil',
    run_id TEXT,
    filename TEXT,
    file_size_bytes BIGINT,
    total_tokens INTEGER DEFAULT 0,
    custo_real_usd NUMERIC(10,4) DEFAULT 0,
    custo_cobrado_usd NUMERIC(10,4) DEFAULT 0,
    duracao_segundos NUMERIC(10,1) DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for fast user lookups
CREATE INDEX IF NOT EXISTS idx_documents_user_id ON public.documents(user_id);

-- Index for ordering by date
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON public.documents(created_at DESC);

-- Index for run_id lookups
CREATE INDEX IF NOT EXISTS idx_documents_run_id ON public.documents(run_id);

-- Enable RLS
ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

-- Policy: Users can see their own documents
CREATE POLICY "Users can view own documents"
    ON public.documents FOR SELECT
    USING (auth.uid() = user_id);

-- Policy: Users can insert their own documents
CREATE POLICY "Users can insert own documents"
    ON public.documents FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- Policy: Users can update their own documents
CREATE POLICY "Users can update own documents"
    ON public.documents FOR UPDATE
    USING (auth.uid() = user_id);

-- Policy: Users can delete their own documents
CREATE POLICY "Users can delete own documents"
    ON public.documents FOR DELETE
    USING (auth.uid() = user_id);

-- Policy: Service role can do everything (for backend)
CREATE POLICY "Service role full access"
    ON public.documents FOR ALL
    USING (auth.role() = 'service_role');

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_documents_updated_at
    BEFORE UPDATE ON public.documents
    FOR EACH ROW
    EXECUTE FUNCTION public.update_updated_at_column();

-- Grant permissions
GRANT ALL ON public.documents TO authenticated;
GRANT ALL ON public.documents TO service_role;
GRANT SELECT ON public.documents TO anon;
"""

print("=" * 60)
print("CREATING DOCUMENTS TABLE IN SUPABASE")
print("=" * 60)

# Method 1: Try Supabase SQL API endpoint
print("\n--- Method 1: Supabase /pg endpoint ---")
for endpoint in [
    f"{url}/rest/v1/rpc/exec_sql",
    f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
]:
    try:
        r = requests.post(
            endpoint,
            headers=headers,
            json={"query": SQL} if "api.supabase" in endpoint else {"sql": SQL},
            timeout=30,
        )
        print(f"  {endpoint}: {r.status_code}")
        if r.status_code in (200, 201):
            print(f"  SUCCESS! {r.text[:200]}")
            break
        else:
            print(f"  {r.text[:200]}")
    except Exception as e:
        print(f"  Error: {e}")

# Method 2: Try creating an RPC function first, then use it
print("\n--- Method 2: Direct PostgREST table check ---")
r = requests.get(
    f"{url}/rest/v1/documents?select=id&limit=1",
    headers=headers,
    timeout=10,
)
if r.status_code == 200:
    print("  TABLE ALREADY EXISTS!")
    print(f"  Data: {r.text[:200]}")
elif r.status_code == 404:
    print("  Table does not exist yet (404)")
else:
    print(f"  Status {r.status_code}: {r.text[:200]}")

# Method 3: Try via psycopg2 if available
print("\n--- Method 3: Direct PostgreSQL connection ---")
try:
    import psycopg2
    # Try common Supabase database URL patterns
    db_password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not db_password:
        # Try extracting from service key JWT
        print("  No SUPABASE_DB_PASSWORD found, trying connection string...")

    # Supabase transaction pooler
    conn_str = f"postgresql://postgres.{project_ref}:{db_password}@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"

    if db_password:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        cur = conn.cursor()
        cur.execute(SQL)
        conn.commit()
        print("  SUCCESS via psycopg2!")
        cur.close()
        conn.close()
    else:
        print("  No database password available")
except ImportError:
    print("  psycopg2 not installed")
except Exception as e:
    print(f"  Error: {e}")

# If nothing worked, output the SQL for manual execution
print("\n" + "=" * 60)
print("SQL MIGRATION READY")
print("=" * 60)
print("\nSe nenhum metodo automatico funcionou, cola este SQL")
print("no Supabase Dashboard > SQL Editor:")
print(f"\nSupabase Dashboard: https://supabase.com/dashboard/project/{project_ref}/sql/new")
print("\n" + "-" * 60)
print(SQL)
print("-" * 60)

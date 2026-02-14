# -*- coding: utf-8 -*-
"""Query documents table via Supabase Python client."""
import os
import json
from dotenv import load_dotenv

load_dotenv()

url = os.environ["SUPABASE_URL"]
service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

try:
    from supabase import create_client
    sb = create_client(url, service_key)

    # Try documents table
    print("=== Trying documents table ===")
    try:
        resp = sb.table("documents").select("id,created_at,title").order("created_at", desc=True).limit(4).execute()
        print(f"Found {len(resp.data)} documents")
        for d in resp.data:
            print(f"  {d}")
    except Exception as e:
        print(f"  Error: {e}")

    # Try analyses table
    print("\n=== Trying analyses table ===")
    try:
        resp = sb.table("analyses").select("*").order("created_at", desc=True).limit(4).execute()
        print(f"Found {len(resp.data)} analyses")
        for d in resp.data:
            print(f"  {d}")
    except Exception as e:
        print(f"  Error: {e}")

    # List all tables using raw SQL via rpc if available
    print("\n=== All tables in all schemas ===")
    try:
        # Try to list tables using information_schema
        resp = sb.rpc("", {}).execute()
    except Exception as e:
        print(f"  RPC not available: {type(e).__name__}")

    # Try using postgrest to find schemas
    print("\n=== Trying storage.objects ===")
    try:
        resp = sb.table("objects").select("id,name,bucket_id,created_at").limit(5).execute()
        print(f"Found {len(resp.data)} objects")
        for d in resp.data:
            print(f"  {d}")
    except Exception as e:
        print(f"  Error: {e}")

except ImportError:
    print("supabase not installed, trying with requests...")
    import requests
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    # Try various potential table names
    for table in ["documents", "analyses", "analysis_results", "cases", "reports"]:
        r = requests.get(f"{url}/rest/v1/{table}?select=id&limit=1", headers=headers, timeout=10)
        status = "OK" if r.status_code == 200 else f"Error {r.status_code}"
        print(f"  {table}: {status}")

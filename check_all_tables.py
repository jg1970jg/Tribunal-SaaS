# -*- coding: utf-8 -*-
"""Check all tables in Supabase using the management API."""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

url = os.environ["SUPABASE_URL"]
service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
headers = {
    "apikey": service_key,
    "Authorization": f"Bearer {service_key}",
    "Content-Type": "application/json",
}

# Use Supabase storage API to check buckets
print("=== STORAGE BUCKETS ===")
r = requests.get(f"{url}/storage/v1/bucket", headers=headers, timeout=10)
if r.status_code == 200:
    buckets = r.json()
    for b in buckets:
        print(f"  {b.get('name','?')} (public={b.get('public','?')})")
else:
    print(f"  Error {r.status_code}: {r.text[:200]}")

# Try Supabase Edge Functions
print("\n=== EDGE FUNCTIONS ===")
r = requests.get(f"{url}/functions/v1/", headers=headers, timeout=10)
print(f"  Status: {r.status_code}")
if r.status_code == 200:
    print(f"  {r.text[:500]}")

# Try to get OpenAPI definitions from ALL schemas
print("\n=== OPENAPI SCHEMAS ===")
r = requests.get(
    f"{url}/rest/v1/",
    headers={**headers, "Accept": "application/openapi+json"},
    timeout=10,
)
schema = json.loads(r.text)
definitions = schema.get("definitions", {})
print(f"  Tables in public schema: {sorted(definitions.keys())}")

# Check all table column types
for table_name in sorted(definitions.keys()):
    props = definitions[table_name].get("properties", {})
    jsonb_cols = [k for k, v in props.items() if "json" in str(v.get("format", "")).lower() or "json" in str(v.get("type", "")).lower()]
    if jsonb_cols:
        print(f"  {table_name} has JSON columns: {jsonb_cols}")

# Try wallet_transactions metadata more carefully
print("\n=== WALLET TRANSACTIONS WITH METADATA ===")
r = requests.get(
    f"{url}/rest/v1/wallet_transactions?select=id,run_id,type,amount_usd,cost_real_usd,description,metadata,created_at&order=created_at.desc&limit=10&type=eq.debit",
    headers=headers, timeout=10,
)
txns = r.json()
for t in txns:
    meta = t.get("metadata")
    print(f"  {t['created_at'][:16]} | ${t['cost_real_usd']:>8.4f} real | run={t['run_id'][:12]}... | meta={'yes' if meta else 'no'}")
    if meta:
        print(f"    metadata: {json.dumps(meta, ensure_ascii=False)[:200]}")

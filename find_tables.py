# -*- coding: utf-8 -*-
"""Find all Supabase tables and check for analysis data."""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

url = os.environ["SUPABASE_URL"]
key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

headers = {"apikey": key, "Authorization": f"Bearer {key}"}

# Get the OpenAPI schema to find all tables
r = requests.get(f"{url}/rest/v1/", headers={**headers, "Accept": "application/openapi+json"}, timeout=10)
schema = json.loads(r.text)

# Extract table names from definitions
definitions = schema.get("definitions", {})
print("=== ALL TABLES IN SUPABASE ===")
for table_name in sorted(definitions.keys()):
    cols = definitions[table_name].get("properties", {})
    col_names = list(cols.keys())
    has_analysis = any("analysis" in c.lower() or "result" in c.lower() for c in col_names)
    marker = " *** HAS ANALYSIS DATA ***" if has_analysis else ""
    print(f"  {table_name}: {col_names}{marker}")

# Check wallet_transactions for latest analyses
print("\n=== LATEST WALLET TRANSACTIONS ===")
r2 = requests.get(
    f"{url}/rest/v1/wallet_transactions?select=*&order=created_at.desc&limit=8",
    headers=headers, timeout=10
)
txns = json.loads(r2.text)
for t in txns:
    print(f"  {t.get('created_at','?')[:16]} | {t.get('type','?'):10s} | ${t.get('amount_usd',0):>8.4f} | {t.get('description','?')[:50]}")

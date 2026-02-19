# -*- coding: utf-8 -*-
"""Insert historical analysis records from wallet_transactions data."""
import os
import sys
import json
import requests
from dotenv import load_dotenv


def main():
    load_dotenv()

    url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not service_key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    user_id = "3f20f22a-a70a-4e0f-bc5b-d3903a9cb278"

    # Historical analyses recovered from wallet_transactions
    historical = [
        {
            "user_id": user_id,
            "title": "Analise historica #1",
            "analysis_result": {
                "nota": "Resultado detalhado nao disponivel - analise anterior a criacao da tabela documents",
                "custos": {
                    "custo_total_usd": 0.5813,
                    "custo_cliente_usd": 1.1626,
                },
            },
            "status": "completed",
            "tier": "bronze",
            "area_direito": "Civil",
            "run_id": "d610b0a7-b26c-45dc-aacf-22b9bfdf774b",
            "filename": "documento_teste.pdf",
            "total_tokens": 0,
            "custo_real_usd": 0.5813,
            "custo_cobrado_usd": 1.1626,
            "duracao_segundos": 310,
            "created_at": "2026-02-13T13:43:32+00:00",
        },
        {
            "user_id": user_id,
            "title": "Analise historica #2",
            "analysis_result": {
                "nota": "Resultado detalhado nao disponivel - analise anterior a criacao da tabela documents",
                "custos": {
                    "custo_total_usd": 0.8469,
                    "custo_cliente_usd": 1.6938,
                },
            },
            "status": "completed",
            "tier": "bronze",
            "area_direito": "Civil",
            "run_id": "67e2d80e-1ba7-43ae-8e4f-c5418ee1a959",
            "filename": "documento_teste.pdf",
            "total_tokens": 0,
            "custo_real_usd": 0.8469,
            "custo_cobrado_usd": 1.6938,
            "duracao_segundos": 377,
            "created_at": "2026-02-13T13:48:56+00:00",
        },
        # 3 cancelled/failed attempts
        {
            "user_id": user_id,
            "title": "Analise falhada #1",
            "analysis_result": {"nota": "Analise cancelada - sem resultado"},
            "status": "cancelled",
            "tier": "bronze",
            "area_direito": "Civil",
            "run_id": "bf1743df-85a1-4724-b3b8-1e13bd2c9a0d",
            "total_tokens": 0,
            "custo_real_usd": 0,
            "custo_cobrado_usd": 0,
            "duracao_segundos": 0,
            "created_at": "2026-02-13T14:14:42+00:00",
        },
        {
            "user_id": user_id,
            "title": "Analise falhada #2",
            "analysis_result": {"nota": "Analise cancelada - sem resultado"},
            "status": "cancelled",
            "tier": "bronze",
            "area_direito": "Civil",
            "run_id": "218841d9-5cd5-474f-bbcc-ebb6f90342e5",
            "total_tokens": 0,
            "custo_real_usd": 0,
            "custo_cobrado_usd": 0,
            "duracao_segundos": 0,
            "created_at": "2026-02-13T14:15:05+00:00",
        },
        {
            "user_id": user_id,
            "title": "Analise falhada #3",
            "analysis_result": {"nota": "Analise cancelada - sem resultado"},
            "status": "cancelled",
            "tier": "bronze",
            "area_direito": "Civil",
            "run_id": "e930bae0-7c94-45c1-9e0e-52d5069e3212",
            "total_tokens": 0,
            "custo_real_usd": 0,
            "custo_cobrado_usd": 0,
            "duracao_segundos": 0,
            "created_at": "2026-02-13T14:15:25+00:00",
        },
    ]

    confirm = input("WARNING: This will insert records into PRODUCTION Supabase. Continue? (yes/no): ")
    if confirm.lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    print("=== INSERTING HISTORICAL RECORDS ===\n")

    for i, record in enumerate(historical):
        r = requests.post(
            f"{url}/rest/v1/documents",
            headers=headers,
            json=record,
            timeout=15,
        )
        if r.status_code in (200, 201):
            data = r.json()
            doc_id = data[0]["id"] if data else "?"
            print(f"  OK #{i+1}: {record['title']} | run={record['run_id'][:12]}... | id={doc_id}")
        else:
            print(f"  ERRO #{i+1}: {r.status_code} - {r.text[:200]}")

    # Verify
    print("\n=== VERIFICATION ===")
    r = requests.get(
        f"{url}/rest/v1/documents?select=id,title,status,custo_real_usd,created_at&order=created_at.asc",
        headers={k: v for k, v in headers.items() if k != "Prefer"},
        timeout=10,
    )
    docs = r.json()
    print(f"\nTotal: {len(docs)} documentos na tabela\n")
    for d in docs:
        print(f"  {d['created_at'][:16]} | {d['status']:10s} | ${d['custo_real_usd']:>8.4f} | {d['title']}")


if __name__ == "__main__":
    main()

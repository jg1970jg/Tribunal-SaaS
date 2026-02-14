# -*- coding: utf-8 -*-
"""
TEST E2E - Fluxo completo: Health -> Auth -> Wallet -> Analyze -> Verify
============================================================
Testa o backend LexForum end-to-end.

Uso:
    python test_e2e_flow.py                          # usa Render (live)
    python test_e2e_flow.py --local                   # usa localhost:8000
    python test_e2e_flow.py --skip-analyze            # testa tudo MENOS a análise (rápido)
    python test_e2e_flow.py --email X --password Y    # credenciais custom

Requer:
    pip install requests python-dotenv supabase
"""

import os
import sys
import json
import time
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIG
# ============================================================

RENDER_URL = "https://tribunal-saas.onrender.com"
LOCAL_URL = "http://localhost:8000"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# Ficheiro de teste (PDF pequeno)
TEST_PDF = Path(__file__).parent / "tests" / "fixtures" / "pdf_texto_normal.pdf"

# ============================================================
# HELPERS
# ============================================================

class Colors:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    INFO = "\033[94m"
    BOLD = "\033[1m"
    END = "\033[0m"


def ok(msg):
    print(f"  {Colors.OK}PASS{Colors.END} {msg}")


def fail(msg):
    print(f"  {Colors.FAIL}FAIL{Colors.END} {msg}")


def warn(msg):
    print(f"  {Colors.WARN}WARN{Colors.END} {msg}")


def info(msg):
    print(f"  {Colors.INFO}INFO{Colors.END} {msg}")


def header(msg):
    print(f"\n{Colors.BOLD}{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}{Colors.END}")


# ============================================================
# STEP 1: HEALTH CHECK
# ============================================================

def test_health(base_url: str) -> bool:
    header("STEP 1: Health Check")
    try:
        r = requests.get(f"{base_url}/health", timeout=30)
        if r.status_code == 200 and r.json().get("status") == "online":
            ok(f"GET /health -> {r.json()}")
            return True
        else:
            fail(f"GET /health -> {r.status_code}: {r.text}")
            return False
    except requests.exceptions.ConnectionError:
        fail(f"Servidor nao acessivel em {base_url}")
        info("Se Render, o servidor pode estar a fazer cold start (espera ~60s)")
        return False
    except Exception as e:
        fail(f"Erro: {e}")
        return False


# ============================================================
# STEP 2: AUTENTICACAO (Supabase JWT)
# ============================================================

def test_auth(email: str, password: str) -> str | None:
    header("STEP 2: Autenticacao Supabase")

    if not SUPABASE_URL or not SUPABASE_KEY:
        fail("SUPABASE_URL e/ou SUPABASE_KEY nao definidos no .env")
        return None

    info(f"Supabase URL: {SUPABASE_URL}")
    info(f"Email: {email}")

    try:
        # Login via Supabase REST API
        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={
                "apikey": SUPABASE_KEY,
                "Content-Type": "application/json",
            },
            json={"email": email, "password": password},
            timeout=15,
        )

        if r.status_code == 200:
            data = r.json()
            token = data.get("access_token", "")
            user_id = data.get("user", {}).get("id", "")
            ok(f"Login OK -> user_id={user_id[:8]}...")
            info(f"Token: {token[:20]}...{token[-10:]}")
            return token
        else:
            fail(f"Login falhou: {r.status_code} -> {r.text[:200]}")
            return None

    except Exception as e:
        fail(f"Erro auth: {e}")
        return None


# ============================================================
# STEP 3: VERIFICAR IDENTIDADE (/me)
# ============================================================

def test_me(base_url: str, token: str) -> bool:
    header("STEP 3: Verificar Identidade (GET /me)")
    try:
        r = requests.get(
            f"{base_url}/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            ok(f"user_id={data.get('user_id', '?')[:8]}... email={data.get('email', '?')}")
            return True
        else:
            fail(f"GET /me -> {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        fail(f"Erro: {e}")
        return False


# ============================================================
# STEP 4: VERIFICAR WALLET (/wallet/balance)
# ============================================================

def test_wallet_balance(base_url: str, token: str) -> dict | None:
    header("STEP 4: Wallet Balance (GET /wallet/balance)")
    try:
        r = requests.get(
            f"{base_url}/wallet/balance",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            ok(f"Balance: ${data.get('balance_usd', 0):.2f} | Blocked: ${data.get('blocked_usd', 0):.2f} | Total: ${data.get('total_usd', 0):.2f}")
            return data
        elif r.status_code == 404:
            warn("Endpoint /wallet/balance nao encontrado (pode estar desactivado)")
            return {}
        else:
            fail(f"GET /wallet/balance -> {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        fail(f"Erro: {e}")
        return None


# ============================================================
# STEP 5: LISTAR TIERS (/tiers)
# ============================================================

def test_tiers(base_url: str) -> bool:
    header("STEP 5: Listar Tiers (GET /tiers)")
    try:
        r = requests.get(f"{base_url}/tiers", timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                for t in data:
                    name = t.get("name") or t.get("id", "?")
                    info(f"  Tier: {name}")
                ok(f"{len(data)} tiers encontrados")
            else:
                ok(f"Tiers: {json.dumps(data, indent=2)[:300]}")
            return True
        elif r.status_code == 404:
            warn("Endpoint /tiers nao encontrado")
            return True  # nao e critico
        else:
            fail(f"GET /tiers -> {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        fail(f"Erro: {e}")
        return False


# ============================================================
# STEP 6: ANALISE COMPLETA (POST /analyze)
# ============================================================

def test_analyze(base_url: str, token: str, pdf_path: Path) -> dict | None:
    header("STEP 6: Analise Completa (POST /analyze)")

    if not pdf_path.exists():
        fail(f"Ficheiro de teste nao encontrado: {pdf_path}")
        return None

    file_size = pdf_path.stat().st_size
    info(f"Ficheiro: {pdf_path.name} ({file_size:,} bytes)")
    info(f"Isto vai demorar varios minutos (pipeline de 4 fases)...")

    try:
        with open(pdf_path, "rb") as f:
            t0 = time.time()
            r = requests.post(
                f"{base_url}/analyze",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={
                    "area_direito": "Civil",
                    "perguntas_raw": "",
                    "titulo": "Teste E2E Automatizado",
                    "tier": "bronze",
                },
                timeout=600,  # 10 min max
            )
            elapsed = time.time() - t0

        if r.status_code == 200:
            data = r.json()
            ok(f"Analise concluida em {elapsed:.1f}s")
            info(f"Run ID: {data.get('run_id', '?')}")
            info(f"Veredicto: {data.get('simbolo_final', '')} {data.get('veredicto_final', '?')}")
            info(f"Tokens: {data.get('total_tokens', 0):,}")

            custos = data.get("custos", {})
            if custos:
                info(f"Custo APIs: ${custos.get('custo_total_usd', 0):.4f}")
                wallet = custos.get("wallet", {})
                if wallet:
                    info(f"Wallet debit: real=${wallet.get('custo_real', 0):.4f} cliente=${wallet.get('custo_cliente', 0):.4f}")
                    info(f"Saldo: ${wallet.get('saldo_antes', 0):.2f} -> ${wallet.get('saldo_depois', 0):.2f}")

            # Verificar campos essenciais
            checks = [
                ("run_id", bool(data.get("run_id"))),
                ("veredicto_final", bool(data.get("veredicto_final"))),
                ("fase1_agregado", bool(data.get("fase1_agregado_consolidado") or data.get("fase1_agregado"))),
                ("fase2_chefe", bool(data.get("fase2_chefe_consolidado") or data.get("fase2_chefe"))),
                ("fase3_presidente", bool(data.get("fase3_presidente"))),
                ("total_tokens > 0", (data.get("total_tokens", 0) or 0) > 0),
            ]
            all_ok = True
            for name, passed in checks:
                if passed:
                    ok(f"  {name}")
                else:
                    fail(f"  {name} AUSENTE")
                    all_ok = False

            # Guardar resultado completo
            out_path = Path(__file__).parent / "test_e2e_result.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            info(f"Resultado guardado em: {out_path}")

            return data

        elif r.status_code == 402:
            fail(f"Saldo insuficiente: {r.json()}")
            return None
        elif r.status_code == 401:
            fail(f"Token expirado/invalido: {r.text[:200]}")
            return None
        else:
            fail(f"POST /analyze -> {r.status_code}: {r.text[:500]}")
            return None

    except requests.exceptions.Timeout:
        fail("Timeout (>10 min) - pipeline demasiado lento?")
        return None
    except Exception as e:
        fail(f"Erro: {e}")
        return None


# ============================================================
# STEP 7: VERIFICAR WALLET APOS ANALISE
# ============================================================

def test_wallet_after(base_url: str, token: str, balance_before: dict) -> bool:
    header("STEP 7: Wallet Apos Analise")
    after = test_wallet_balance(base_url, token)
    if after and balance_before:
        before_avail = balance_before.get("balance_usd", 0)
        after_avail = after.get("balance_usd", 0)
        diff = before_avail - after_avail
        if diff > 0:
            ok(f"Wallet debitada: ${diff:.4f} (${before_avail:.2f} -> ${after_avail:.2f})")
            return True
        elif diff == 0:
            warn("Saldo nao mudou (SKIP_WALLET_CHECK=true?)")
            return True
        else:
            warn(f"Saldo AUMENTOU? ${before_avail:.2f} -> ${after_avail:.2f}")
            return False
    return True


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Teste E2E do LexForum")
    parser.add_argument("--local", action="store_true", help="Usar localhost:8000")
    parser.add_argument("--skip-analyze", action="store_true", help="Nao executar analise (rapido)")
    parser.add_argument("--email", default=os.environ.get("TEST_EMAIL", ""), help="Email Supabase")
    parser.add_argument("--password", default=os.environ.get("TEST_PASSWORD", ""), help="Password Supabase")
    parser.add_argument("--pdf", default="", help="Caminho para PDF de teste")
    args = parser.parse_args()

    base_url = LOCAL_URL if args.local else RENDER_URL
    pdf_path = Path(args.pdf) if args.pdf else TEST_PDF

    print(f"\n{Colors.BOLD}LEXFORUM - TESTE E2E{Colors.END}")
    print(f"Backend: {base_url}")
    print(f"PDF: {pdf_path}")
    print(f"Skip analyze: {args.skip_analyze}")

    results = {}

    # STEP 1: Health
    results["health"] = test_health(base_url)
    if not results["health"]:
        print(f"\n{Colors.FAIL}ABORTADO: Servidor offline{Colors.END}")
        sys.exit(1)

    # STEP 2: Auth
    if not args.email or not args.password:
        warn("Email/password nao fornecidos. Usa --email e --password, ou define TEST_EMAIL/TEST_PASSWORD no .env")
        print(f"\n{Colors.WARN}ABORTADO: Sem credenciais{Colors.END}")
        sys.exit(1)

    token = test_auth(args.email, args.password)
    results["auth"] = token is not None
    if not token:
        print(f"\n{Colors.FAIL}ABORTADO: Auth falhou{Colors.END}")
        sys.exit(1)

    # STEP 3: /me
    results["me"] = test_me(base_url, token)

    # STEP 4: Wallet balance (antes)
    balance_before = test_wallet_balance(base_url, token)
    results["wallet_before"] = balance_before is not None

    # STEP 5: Tiers
    results["tiers"] = test_tiers(base_url)

    # STEP 6: Analyze
    if args.skip_analyze:
        warn("Analise SKIP (--skip-analyze)")
        results["analyze"] = None
    else:
        analyze_result = test_analyze(base_url, token, pdf_path)
        results["analyze"] = analyze_result is not None

        # STEP 7: Wallet after
        if analyze_result:
            results["wallet_after"] = test_wallet_after(base_url, token, balance_before or {})

    # RESUMO
    header("RESUMO")
    total = 0
    passed = 0
    for step, result in results.items():
        if result is None:
            warn(f"  {step}: SKIPPED")
        elif result:
            ok(f"  {step}")
            passed += 1
            total += 1
        else:
            fail(f"  {step}")
            total += 1

    print(f"\n  {passed}/{total} testes passaram")

    if passed == total:
        print(f"\n{Colors.OK}{Colors.BOLD}  ALL TESTS PASSED{Colors.END}")
    else:
        print(f"\n{Colors.FAIL}{Colors.BOLD}  SOME TESTS FAILED{Colors.END}")
        sys.exit(1)


if __name__ == "__main__":
    main()

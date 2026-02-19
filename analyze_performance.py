# -*- coding: utf-8 -*-
"""Analisa performance das ultimas 4 analises."""
import os
import sys
import json
import requests
from dotenv import load_dotenv
from collections import defaultdict


def main():
    load_dotenv()

    url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not service_key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    # Buscar ultimas 4 analises
    r = requests.get(
        f"{url}/rest/v1/documents?select=id,created_at,title,analysis_result&order=created_at.desc&limit=4&analysis_result=not.is.null",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    if r.status_code != 200:
        print(f"Erro {r.status_code}: {r.text[:300]}")
        exit(1)

    analyses = r.json()
    print(f"=== ULTIMAS {len(analyses)} ANALISES ===\n")

    # Acumular stats por modelo
    model_stats = defaultdict(lambda: {
        "chamadas": 0,
        "tokens_total": 0,
        "custo_total": 0.0,
        "erros": 0,
        "warnings": 0,
        "fases": set(),
        "inconclusivos": 0,
    })

    all_errors = []

    for i, a in enumerate(analyses):
        result = a.get("analysis_result") or {}
        titulo = a.get("title", "?")
        created = a.get("created_at", "?")[:16]
        custos = result.get("custos") or {}
        total_tokens = result.get("total_tokens", 0)
        veredicto = result.get("veredicto_final", "?")
        simbolo = result.get("simbolo_final", "")

        print(f"--- Analise {i+1}: {titulo} ({created}) ---")
        print(f"    Parecer: {simbolo} {veredicto}")
        print(f"    Tokens: {total_tokens:,} | Custo: ${custos.get('custo_total_usd', 0):.4f}")

        # Detalhes por modelo
        detalhes = custos.get("detalhes_por_modelo") or []
        for d in detalhes:
            modelo = d.get("modelo", "?")
            modelo_short = modelo.split("/")[-1]
            fase = d.get("fase", "?")
            tokens = d.get("tokens", 0)
            custo = d.get("custo_usd", 0)
            status = d.get("status", "")
            erro = d.get("erro", "")

            stats = model_stats[modelo_short]
            stats["chamadas"] += 1
            stats["tokens_total"] += tokens
            stats["custo_total"] += custo
            stats["fases"].add(fase.split("_")[0] if "_" in fase else fase)
            if erro or status == "erro":
                stats["erros"] += 1

        # Erros tecnicos
        erros = result.get("erros_tecnicos") or []
        for e in erros:
            if isinstance(e, dict):
                auditor = e.get("auditor_id", "?")
                msgs = e.get("messages", [])
                for m in msgs:
                    all_errors.append({"analise": titulo[:30], "auditor": auditor, "msg": m})
                    # Encontrar modelo do auditor
                    for d in detalhes:
                        fase = d.get("fase", "")
                        if auditor.lower() in fase.lower():
                            modelo_short = d.get("modelo", "?").split("/")[-1]
                            model_stats[modelo_short]["warnings"] += 1
                            break
            elif isinstance(e, str):
                all_errors.append({"analise": titulo[:30], "auditor": "?", "msg": e})

    print("\n")

    # ============================================================
    # RANKING DE PERFORMANCE
    # ============================================================
    print("=" * 80)
    print("RANKING DE PERFORMANCE POR MODELO (4 ultimas analises)")
    print("=" * 80)

    # Ordenar por: mais erros + warnings, menos tokens por chamada
    models_sorted = sorted(
        model_stats.items(),
        key=lambda x: (-(x[1]["erros"] + x[1]["warnings"]), x[1]["tokens_total"] / max(x[1]["chamadas"], 1)),
    )

    print(f"\n{'Modelo':<30s} {'Chamadas':>8s} {'Tokens':>10s} {'Tok/Call':>10s} {'Custo':>10s} {'$/Call':>8s} {'Erros':>6s} {'Warns':>6s}")
    print("-" * 98)

    for modelo, stats in models_sorted:
        tok_per_call = stats["tokens_total"] / max(stats["chamadas"], 1)
        cost_per_call = stats["custo_total"] / max(stats["chamadas"], 1)
        flag = "🔴" if stats["erros"] > 0 else ("🟡" if stats["warnings"] > 2 else "🟢")
        print(
            f"{flag} {modelo:<28s} {stats['chamadas']:>8d} {stats['tokens_total']:>10,} {tok_per_call:>10,.0f} "
            f"${stats['custo_total']:>9.4f} ${cost_per_call:>7.4f} {stats['erros']:>6d} {stats['warnings']:>6d}"
        )

    # ============================================================
    # PIORES PERFORMERS
    # ============================================================
    print("\n")
    print("=" * 80)
    print("MODELOS COM PIOR PERFORMANCE")
    print("=" * 80)

    # Menor tokens por chamada (menos trabalho)
    print("\n📉 MENOS TOKENS POR CHAMADA (menos trabalho):")
    by_tokens = sorted(
        [(m, s["tokens_total"] / max(s["chamadas"], 1), s["chamadas"]) for m, s in model_stats.items()],
        key=lambda x: x[1],
    )
    for m, tpc, calls in by_tokens[:5]:
        print(f"   {m:<30s} {tpc:>10,.0f} tok/chamada ({calls} chamadas)")

    # Mais erros + warnings
    print("\n🔴 MAIS ERROS E WARNINGS:")
    by_errors = sorted(
        [(m, s["erros"], s["warnings"], s["erros"] + s["warnings"]) for m, s in model_stats.items()],
        key=lambda x: -x[3],
    )
    for m, errs, warns, total in by_errors:
        if total > 0:
            print(f"   {m:<30s} {errs} erros, {warns} warnings (total: {total})")

    # Mais caro por chamada
    print("\n💰 MAIS CARO POR CHAMADA:")
    by_cost = sorted(
        [(m, s["custo_total"] / max(s["chamadas"], 1), s["chamadas"]) for m, s in model_stats.items()],
        key=lambda x: -x[1],
    )
    for m, cpc, calls in by_cost[:5]:
        print(f"   {m:<30s} ${cpc:.4f}/chamada ({calls} chamadas)")

    # ============================================================
    # DETALHE DOS WARNINGS
    # ============================================================
    if all_errors:
        print("\n")
        print("=" * 80)
        print(f"DETALHE DOS WARNINGS ({len(all_errors)} total)")
        print("=" * 80)

        # Agrupar por tipo
        warning_types = defaultdict(int)
        for e in all_errors:
            msg = e["msg"]
            if "[" in msg and "]" in msg:
                wtype = msg.split("]")[0].split("[")[-1]
            else:
                wtype = msg[:40]
            warning_types[wtype] += 1

        print("\nTipos de warning:")
        for wtype, count in sorted(warning_types.items(), key=lambda x: -x[1]):
            print(f"   {count:>3d}x  {wtype}")


if __name__ == "__main__":
    main()

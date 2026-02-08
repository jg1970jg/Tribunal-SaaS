# -*- coding: utf-8 -*-
"""
TESTE RÁPIDO - Verificar se llm_client.py está correcto
"""

print("\n" + "="*70)
print("🔍 TESTE: Verificar llm_client.py")
print("="*70 + "\n")

try:
    # Importar módulo
    print("1. Importando llm_client...")
    import sys
    sys.path.insert(0, 'src')
    import llm_client
    print("   ✅ Importado com sucesso!\n")
    
    # Verificar se tem RESPONSES_API (CORRECTO)
    print("2. Verificando OPENAI_MODELS_USE_RESPONSES_API...")
    if hasattr(llm_client, 'OPENAI_MODELS_USE_RESPONSES_API'):
        print("   ✅ ENCONTRADO! (correcto)")
        print(f"   Modelos: {llm_client.OPENAI_MODELS_USE_RESPONSES_API}\n")
        tem_responses_api = True
    else:
        print("   ❌ NÃO ENCONTRADO! (ficheiro errado!)\n")
        tem_responses_api = False
    
    # Verificar se NÃO tem OPENROUTER (ERRADO)
    print("3. Verificando OPENAI_MODELS_USE_OPENROUTER...")
    if hasattr(llm_client, 'OPENAI_MODELS_USE_OPENROUTER'):
        print("   ❌ ENCONTRADO! (ficheiro errado - versão antiga!)\n")
        tem_openrouter = True
    else:
        print("   ✅ NÃO ENCONTRADO! (correcto)\n")
        tem_openrouter = False
    
    # Verificar função uses_responses_api
    print("4. Testando função uses_responses_api()...")
    if hasattr(llm_client, 'uses_responses_api'):
        resultado = llm_client.uses_responses_api('openai/gpt-5.2')
        print(f"   uses_responses_api('openai/gpt-5.2') = {resultado}")
        if resultado:
            print("   ✅ Função correcta!\n")
            funcao_ok = True
        else:
            print("   ❌ Função incorrecta!\n")
            funcao_ok = False
    else:
        print("   ❌ Função NÃO EXISTE!\n")
        funcao_ok = False
    
    # RESULTADO FINAL
    print("="*70)
    if tem_responses_api and not tem_openrouter and funcao_ok:
        print("✅✅✅ FICHEIRO CORRECTO! RESPONSES API IMPLEMENTADA!")
        print("\nGPT-5.2 vai usar:")
        print("  🔵 OpenAI Responses API directa")
        print("  💰 Teu saldo OpenAI (5% mais barato)")
    else:
        print("❌❌❌ FICHEIRO ERRADO!")
        print("\nProblemas detectados:")
        if not tem_responses_api:
            print("  ❌ Falta OPENAI_MODELS_USE_RESPONSES_API")
        if tem_openrouter:
            print("  ❌ Tem OPENAI_MODELS_USE_OPENROUTER (versão antiga!)")
        if not funcao_ok:
            print("  ❌ Função uses_responses_api() incorrecta")
        print("\nGPT-5.2 vai usar:")
        print("  🟠 OpenRouter (5% mais caro)")
        print("\n⚠️ PRECISA SUBSTITUIR llm_client.py!")
    print("="*70 + "\n")

except Exception as e:
    print(f"\n❌ ERRO ao importar: {e}\n")
    print("Verifica se estás na pasta correcta:")
    print("  C:\\Users\\Guilherme\\Desktop\\TRIBUNAL_GOLDENMASTER_GUI")
    print("\n")

# -*- coding: utf-8 -*-
"""
COMPONENTE: Gestão de API Keys
═══════════════════════════════════════════════════════════════════════════

Adiciona interface para ver/editar/apagar API keys do OpenAI e OpenRouter.

ONDE USAR: 
- Adicionar como nova página no menu sidebar
- Chamar função pagina_api_keys() quando utilizador selecionar
═══════════════════════════════════════════════════════════════════════════
"""

import streamlit as st
import os
from pathlib import Path
from dotenv import load_dotenv, set_key, unset_key


def mask_api_key(key: str) -> str:
    """
    Mascara API key para mostrar apenas início e fim.
    
    Ex: sk-proj-abc123def456... → sk-proj-•••456
    """
    if not key or len(key) < 12:
        return "Não configurada"
    
    # Mostrar primeiros 8 caracteres + ••• + últimos 4
    return f"{key[:8]}•••{key[-4:]}"


def get_env_file_path() -> Path:
    """Retorna caminho do ficheiro .env da raiz do projecto."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    return base_dir / ".env"


def load_api_keys() -> dict:
    """
    Carrega API keys do ficheiro .env
    
    Returns:
        dict com 'openai' e 'openrouter'
    """
    load_dotenv(override=True)
    
    return {
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
    }


ALLOWED_KEY_NAMES = {"OPENAI_API_KEY", "OPENROUTER_API_KEY"}


def save_api_key(key_name: str, key_value: str) -> bool:
    """
    Guarda API key no ficheiro .env

    Args:
        key_name: "OPENAI_API_KEY" ou "OPENROUTER_API_KEY"
        key_value: Valor da key

    Returns:
        True se sucesso
    """
    try:
        # Validar key_name contra whitelist
        if key_name not in ALLOWED_KEY_NAMES:
            st.error(f"Nome de chave inválido: {key_name}")
            return False

        # Sanitizar key_value: rejeitar newlines e caracteres de controlo
        if any(c in key_value for c in ('\n', '\r', '\x00')):
            st.error("Valor da key contém caracteres inválidos")
            return False

        env_file = get_env_file_path()

        # Criar .env se não existir
        if not env_file.exists():
            env_file.touch()

        # Guardar key
        set_key(str(env_file), key_name, key_value)
        
        # Recarregar env
        load_dotenv(override=True)
        
        return True
    except Exception as e:
        st.error(f"Erro ao guardar: {e}")
        return False


def delete_api_key(key_name: str) -> bool:
    """
    Apaga API key do ficheiro .env

    Args:
        key_name: "OPENAI_API_KEY" ou "OPENROUTER_API_KEY"

    Returns:
        True se sucesso
    """
    try:
        # Validar key_name contra whitelist
        if key_name not in ALLOWED_KEY_NAMES:
            st.error(f"Nome de chave inválido: {key_name}")
            return False

        env_file = get_env_file_path()
        
        if env_file.exists():
            unset_key(str(env_file), key_name)
        
        # Recarregar env
        load_dotenv(override=True)
        
        return True
    except Exception as e:
        st.error(f"Erro ao apagar: {e}")
        return False


def pagina_api_keys():
    """
    Página completa de gestão de API Keys.
    
    USAR NO APP.PY:
    
    elif pagina == "api_keys":
        from components_api_config import pagina_api_keys
        pagina_api_keys()
    """
    
    st.header("🔑 Gestão de API Keys")
    
    st.markdown("""
    Gerir as API keys usadas pelo sistema. As keys são guardadas de forma segura no ficheiro `.env`.
    
    **Dual API System:**
    - 🔵 **OpenAI API**: Modelos OpenAI (gpt-5.2, gpt-4o) usam saldo OpenAI
    - 🟠 **OpenRouter API**: Outros modelos + backup automático
    """)
    
    st.divider()
    
    # Carregar keys actuais
    keys = load_api_keys()
    
    # =================================================================
    # OPENAI API KEY
    # =================================================================
    
    st.subheader("🔵 OpenAI API Key")
    
    col_oa1, col_oa2 = st.columns([3, 1])
    
    with col_oa1:
        openai_masked = mask_api_key(keys["openai"])
        st.text_input(
            "API Key actual:",
            value=openai_masked,
            disabled=True,
            help="Key mascarada por segurança"
        )
    
    with col_oa2:
        if keys["openai"]:
            st.metric("Status", "✅ Configurada")
        else:
            st.metric("Status", "❌ Ausente")
    
    # Expandir para editar/apagar
    with st.expander("✏️ Editar / Apagar OpenAI Key"):
        
        st.markdown("**Obter key:** [platform.openai.com/api-keys](https://platform.openai.com/api-keys)")
        
        nova_key_oa = st.text_input(
            "Nova API Key:",
            type="password",
            placeholder="sk-proj-...",
            help="Cole a key completa da OpenAI",
            key="input_openai_key"
        )
        
        col_btn_oa1, col_btn_oa2 = st.columns(2)
        
        with col_btn_oa1:
            if st.button("💾 Guardar", key="save_oa", use_container_width=True):
                if nova_key_oa:
                    if save_api_key("OPENAI_API_KEY", nova_key_oa):
                        st.success("✅ OpenAI Key guardada!")
                        st.rerun()
                    else:
                        st.error("❌ Erro ao guardar")
                else:
                    st.warning("⚠️ Cole a key primeiro")
        
        with col_btn_oa2:
            if st.button("🗑️ Apagar", key="del_oa", use_container_width=True):
                if delete_api_key("OPENAI_API_KEY"):
                    st.success("✅ OpenAI Key apagada!")
                    st.rerun()
                else:
                    st.error("❌ Erro ao apagar")
    
    st.divider()
    
    # =================================================================
    # OPENROUTER API KEY
    # =================================================================
    
    st.subheader("🟠 OpenRouter API Key")
    
    col_or1, col_or2 = st.columns([3, 1])
    
    with col_or1:
        openrouter_masked = mask_api_key(keys["openrouter"])
        st.text_input(
            "API Key actual:",
            value=openrouter_masked,
            disabled=True,
            help="Key mascarada por segurança"
        )
    
    with col_or2:
        if keys["openrouter"]:
            st.metric("Status", "✅ Configurada")
        else:
            st.metric("Status", "❌ Ausente")
    
    # Expandir para editar/apagar
    with st.expander("✏️ Editar / Apagar OpenRouter Key"):
        
        st.markdown("**Obter key:** [openrouter.ai/keys](https://openrouter.ai/keys)")
        
        nova_key_or = st.text_input(
            "Nova API Key:",
            type="password",
            placeholder="sk-or-v1-...",
            help="Cole a key completa do OpenRouter",
            key="input_openrouter_key"
        )
        
        col_btn_or1, col_btn_or2 = st.columns(2)
        
        with col_btn_or1:
            if st.button("💾 Guardar", key="save_or", use_container_width=True):
                if nova_key_or:
                    if save_api_key("OPENROUTER_API_KEY", nova_key_or):
                        st.success("✅ OpenRouter Key guardada!")
                        st.rerun()
                    else:
                        st.error("❌ Erro ao guardar")
                else:
                    st.warning("⚠️ Cole a key primeiro")
        
        with col_btn_or2:
            if st.button("🗑️ Apagar", key="del_or", use_container_width=True):
                if delete_api_key("OPENROUTER_API_KEY"):
                    st.success("✅ OpenRouter Key apagada!")
                    st.rerun()
                else:
                    st.error("❌ Erro ao apagar")
    
    st.divider()
    
    # =================================================================
    # INFORMAÇÃO ADICIONAL
    # =================================================================
    
    st.info("""
    **💡 Como funciona o Dual API System:**
    
    1. **Modelos OpenAI** (gpt-5.2, gpt-5.2-pro, gpt-4o):
       - Usa API OpenAI directa (saldo OpenAI)
       - Se falhar → fallback automático OpenRouter
    
    2. **Outros modelos** (Anthropic, Google):
       - Usa OpenRouter sempre
    
    3. **Segurança**:
       - Keys guardadas localmente em `.env`
       - Nunca enviadas para servidores externos
       - Mascaradas na interface
    """)
    
    # Botão reiniciar cliente (força recarregar keys)
    if st.button("🔄 Reiniciar Cliente LLM", use_container_width=True):
        # v4.0 FIX: usar função dedicada em vez de mutar global diretamente
        from src.llm_client import reset_llm_client
        reset_llm_client()

        st.success("✅ Cliente reiniciado! Keys recarregadas.")
        st.rerun()

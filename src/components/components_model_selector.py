# -*- coding: utf-8 -*-
"""
COMPONENTE: Seleção de Modelos Premium
═══════════════════════════════════════════════════════════════════════════

Interface para utilizador escolher entre GPT-5.2 e GPT-5.2-PRO para:
- Consolidador dos Auditores
- Conselheiro-Mor

ONDE USAR:
- Adicionar ANTES do botão "Processar Documento"
- Retorna choices que devem ser passados ao pipeline
═══════════════════════════════════════════════════════════════════════════
"""

import sys
from pathlib import Path

# Adicionar diretório raiz ao path (necessário para imports absolutos)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
from src.config import CHEFE_MODEL_OPTIONS, PRESIDENTE_MODEL_OPTIONS


def selecao_modelos_premium():
    """
    Interface de seleção de modelos premium.
    
    Returns:
        dict com 'chefe' e 'presidente' (keys dos modelos escolhidos)
    
    EXEMPLO USO NO APP.PY:
    
    # Antes de processar
    choices = selecao_modelos_premium()
    
    # Ao chamar pipeline
    chefe_model = get_chefe_model(choices['chefe'])
    presidente_model = get_presidente_model(choices['presidente'])
    """
    
    st.markdown("---")
    st.subheader("⚙️ Configuração de Modelos Premium")
    
    st.markdown("""
    Escolha a versão dos modelos principais. **GPT-5.2** oferece excelente qualidade 
    a custo controlado. **GPT-5.2-PRO** oferece máxima precisão mas custa ~10x mais.
    """)
    
    col_left, col_right = st.columns(2)
    
    # ===================================================================
    # CONSOLIDADOR DOS AUDITORES
    # ===================================================================

    with col_left:
        st.markdown("### 👔 Consolidador dos Auditores")
        
        st.markdown("""
        Consolida as 3 auditorias numa síntese única.
        Qualidade crítica para análise final.
        """)
        
        # Opções
        chefe_options = list(CHEFE_MODEL_OPTIONS.keys())
        chefe_display = [CHEFE_MODEL_OPTIONS[k]["display_name"] for k in chefe_options]
        
        # Radio buttons
        chefe_choice = st.radio(
            "Escolha o modelo:",
            options=chefe_options,
            format_func=lambda x: CHEFE_MODEL_OPTIONS[x]["display_name"],
            index=0,  # default: primeira opção (económico)
            key="radio_chefe"
        )
        
        # Mostrar detalhes da opção
        chefe_info = CHEFE_MODEL_OPTIONS[chefe_choice]
        
        st.info(f"""
        **{chefe_info['display_name']}**
        
        💰 Custo estimado: ~${chefe_info['cost_per_analysis']:.2f} por análise
        
        📝 {chefe_info['description']}
        """)
        
        if chefe_info["recommended"]:
            st.success("✅ Recomendado para uso geral")
    
    # ===================================================================
    # CONSELHEIRO-MOR
    # ===================================================================

    with col_right:
        st.markdown("### 👨‍⚖️ Conselheiro-Mor")

        st.markdown("""
        Parecer final baseado em auditorias e relatórios.
        Determina o parecer conclusivo.
        """)
        
        # Opções
        pres_options = list(PRESIDENTE_MODEL_OPTIONS.keys())
        pres_display = [PRESIDENTE_MODEL_OPTIONS[k]["display_name"] for k in pres_options]
        
        # Radio buttons
        pres_choice = st.radio(
            "Escolha o modelo:",
            options=pres_options,
            format_func=lambda x: PRESIDENTE_MODEL_OPTIONS[x]["display_name"],
            index=0,  # default: primeira opção (económico)
            key="radio_presidente"
        )
        
        # Mostrar detalhes da opção
        pres_info = PRESIDENTE_MODEL_OPTIONS[pres_choice]
        
        st.info(f"""
        **{pres_info['display_name']}**
        
        💰 Custo estimado: ~${pres_info['cost_per_analysis']:.2f} por análise
        
        📝 {pres_info['description']}
        """)
        
        if pres_info["recommended"]:
            st.success("✅ Recomendado para uso geral")
    
    # ===================================================================
    # CUSTO TOTAL ESTIMADO
    # ===================================================================
    
    st.markdown("---")
    
    custo_chefe = CHEFE_MODEL_OPTIONS[chefe_choice]["cost_per_analysis"]
    custo_pres = PRESIDENTE_MODEL_OPTIONS[pres_choice]["cost_per_analysis"]
    custo_total_premium = custo_chefe + custo_pres
    
    # Custo base (outros modelos: auditores, juízes, extratores)
    custo_base = 0.30  # estimativa conservadora
    
    custo_total = custo_base + custo_total_premium
    
    col_custo1, col_custo2, col_custo3 = st.columns(3)
    
    with col_custo1:
        st.metric(
            "💰 Custo Base",
            f"${custo_base:.2f}",
            help="Extratores + Auditores + Relatores (outros modelos)"
        )
    
    with col_custo2:
        st.metric(
            "⭐ Custo Premium",
            f"${custo_total_premium:.2f}",
            help="Consolidador + Conselheiro-Mor (modelos escolhidos)"
        )
    
    with col_custo3:
        st.metric(
            "📊 Custo Total Estimado",
            f"${custo_total:.2f}",
            help="Estimativa total da análise completa"
        )
    
    # Aviso se escolher PRO
    if chefe_choice == "gpt-5.2-pro" or pres_choice == "gpt-5.2-pro":
        st.warning("""
        ⚠️ **Atenção:** Escolheu modelo(s) PRO. 
        
        O custo será significativamente maior (~$0.40 adicional). 
        Recomendamos GPT-5.2 normal que já oferece excelente qualidade!
        """)
    
    st.markdown("---")
    
    # Retornar escolhas
    return {
        "chefe": chefe_choice,
        "presidente": pres_choice,
    }


def get_model_choices_from_session():
    """
    Retorna escolhas de modelos guardadas no session_state.
    
    Se não existirem, retorna defaults.
    
    Returns:
        dict com 'chefe' e 'presidente'
    """
    if "model_choices" not in st.session_state:
        return {
            "chefe": "gpt-5.2",
            "presidente": "gpt-5.2",
        }
    
    return st.session_state.model_choices


def save_model_choices_to_session(choices: dict):
    """
    Guarda escolhas de modelos no session_state.
    
    Args:
        choices: dict com 'chefe' e 'presidente'
    """
    st.session_state.model_choices = choices

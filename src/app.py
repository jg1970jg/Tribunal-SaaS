# -*- coding: utf-8 -*-
"""
LEXFORUM GUI - Interface Principal (Streamlit)
Pipeline de 3 Fases com LLMs via OpenRouter

Execute com: streamlit run src/app.py
"""

import streamlit as st
from pathlib import Path
import sys
import json
from datetime import datetime
import io
from typing import List, Optional
import shutil  # ← NOVO: Para apagar pastas

# Adicionar diretório raiz ao path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (
    AREAS_DIREITO,
    CORES,
    SIMBOLOS_VERIFICACAO,
    SUPPORTED_EXTENSIONS,
    OPENROUTER_API_KEY,
    LLM_CONFIGS,  # CORRIGIDO: usar LLM_CONFIGS em vez de EXTRATOR_MODELS
    AUDITOR_MODELS,
    JUIZ_MODELS,
    PRESIDENTE_MODEL,
    AGREGADOR_MODEL,
    CHEFE_MODEL,
    OUTPUT_DIR,
    HISTORICO_DIR,  # ← NOVO: Para apagar ficheiros do histórico
)
from src.pipeline.processor import LexForumProcessor, PipelineResult
from src.pipeline.constants import (
    FLAGS_BLOQUEANTES,
    ESTADOS_RESOLVIDOS,
    OVERRIDE_TYPES,
    is_resolvida,
    has_flags_bloqueantes,
    precisa_reparacao,
)
from src.document_loader import DocumentLoader, DocumentContent, get_supported_extensions
from src.llm_client import get_llm_client
from src.legal_verifier import get_legal_verifier
from src.utils.perguntas import parse_perguntas, validar_perguntas
from src.legal_verifier import VerificacaoLegal, CitacaoLegal
import logging

# ← NOVO: Import módulo perguntas adicionais (isolado)
# ← NOVO: Imports Dual API System
from src.components.components_api_config import pagina_api_keys
from src.components.components_model_selector import selecao_modelos_premium
from src.config import get_chefe_model, get_presidente_model
from src.perguntas.tab_perguntas import tab_perguntas_adicionais
from src.utils.metadata_manager import (
    listar_analises_com_titulos,
    atualizar_metadata,
    contar_analises_sem_titulo,
    gerar_titulo_automatico
)

logger = logging.getLogger(__name__)


def carregar_resultado(run_id: str) -> PipelineResult:
    """Carrega resultado de análise antiga do histórico."""
    from src.pipeline.processor import PipelineResult, FaseResult
    from pathlib import Path

    filepath = OUTPUT_DIR / run_id / "resultado.json"
    if not filepath.exists():
        # Tentar no histórico
        from src.config import HISTORICO_DIR
        filepath = HISTORICO_DIR / f"{run_id}.json"

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Reconstruir DocumentContent
    doc_data = data.get('documento')
    documento = None
    if doc_data:
        documento = DocumentContent(
            filename=doc_data.get('filename', ''),
            extension=doc_data.get('extension', ''),
            text=doc_data.get('text', ''),
            num_pages=doc_data.get('num_pages', 0),
            num_chars=doc_data.get('num_chars', 0),
            num_words=doc_data.get('num_words', 0),
            success=doc_data.get('success', True),
        )

    # Reconstruir PipelineResult
    resultado = PipelineResult(
        run_id=data['run_id'],
        documento=documento,
        area_direito=data.get('area_direito', ''),
        perguntas_utilizador=data.get('perguntas_utilizador', []),
        timestamp_inicio=datetime.fromisoformat(data['timestamp_inicio']) if data.get('timestamp_inicio') else datetime.now(),
    )

    # Reconstruir FaseResults
    def parse_fase_result(f_data):
        return FaseResult(
            fase=f_data.get('fase', ''),
            modelo=f_data.get('modelo', ''),
            role=f_data.get('role', ''),
            conteudo=f_data.get('conteudo', ''),
            tokens_usados=f_data.get('tokens_usados', 0),
            latencia_ms=f_data.get('latencia_ms', 0),
            sucesso=f_data.get('sucesso', True),
            erro=f_data.get('erro'),
        )

    resultado.fase1_extracoes = [parse_fase_result(f) for f in data.get('fase1_extracoes', [])]
    resultado.fase1_agregado = data.get('fase1_agregado', '')
    resultado.fase1_agregado_bruto = data.get('fase1_agregado_bruto', '')
    resultado.fase1_agregado_consolidado = data.get('fase1_agregado_consolidado', data.get('fase1_agregado', ''))
    resultado.fase2_auditorias = [parse_fase_result(f) for f in data.get('fase2_auditorias', [])]
    resultado.fase2_chefe = data.get('fase2_chefe', '')
    resultado.fase2_auditorias_brutas = data.get('fase2_auditorias_brutas', '')
    resultado.fase2_chefe_consolidado = data.get('fase2_chefe_consolidado', data.get('fase2_chefe', ''))
    resultado.fase3_pareceres = [parse_fase_result(f) for f in data.get('fase3_pareceres', [])]
    resultado.fase3_presidente = data.get('fase3_presidente', '')

    # Q&A
    resultado.respostas_juizes_qa = data.get('respostas_juizes_qa', [])
    resultado.respostas_finais_qa = data.get('respostas_finais_qa', '')

    # Verificações legais
    verificacoes = []
    for v in data.get('verificacoes_legais', []):
        try:
            citacao_data = v.get('citacao', {})
            citacao = CitacaoLegal(
                diploma=citacao_data.get('diploma', v.get('diploma', '')),
                artigo=citacao_data.get('artigo', v.get('artigo', '')),
                numero=citacao_data.get('numero'),
                alinea=citacao_data.get('alinea'),
                texto_original=citacao_data.get('texto_original', ''),
                texto_normalizado=citacao_data.get('texto_normalizado', v.get('texto_normalizado', '')),
            )
            verificacao = VerificacaoLegal(
                citacao=citacao,
                existe=v.get('existe', False),
                texto_encontrado=v.get('texto_encontrado'),
                fonte=v.get('fonte', ''),
                status=v.get('status', 'atencao'),
                simbolo=v.get('simbolo', SIMBOLOS_VERIFICACAO.get('atencao', '⚠')),
                aplicabilidade=v.get('aplicabilidade', '⚠'),
                mensagem=v.get('mensagem', ''),
            )
            verificacoes.append(verificacao)
        except Exception as e:
            logger.warning(f"Erro ao reconstruir verificação: {e}")
    resultado.verificacoes_legais = verificacoes

    # Stats finais
    resultado.veredicto_final = data.get('veredicto_final', '')
    resultado.simbolo_final = data.get('simbolo_final', '')
    resultado.status_final = data.get('status_final', '')
    resultado.sucesso = data.get('sucesso', False)
    resultado.total_tokens = data.get('total_tokens', 0)
    resultado.total_latencia_ms = data.get('total_latencia_ms', 0)

    if data.get('timestamp_fim'):
        resultado.timestamp_fim = datetime.fromisoformat(data['timestamp_fim'])

    return resultado

# Configuração da página
st.set_page_config(
    page_title="LexForum",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)


def carregar_css():
    """Carrega CSS personalizado."""
    st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #1a5f7a, #57c5b6);
        padding: 20px;
        border-radius: 10px;
        color: white;
        text-align: center;
        margin-bottom: 20px;
    }
    .resultado-box {
        padding: 20px;
        border-radius: 10px;
        margin: 15px 0;
    }
    .resultado-aprovado {
        background: linear-gradient(135deg, #d4edda, #c3e6cb);
        border: 3px solid #28a745;
    }
    .resultado-rejeitado {
        background: linear-gradient(135deg, #f8d7da, #f5c6cb);
        border: 3px solid #dc3545;
    }
    .resultado-atencao {
        background: linear-gradient(135deg, #fff3cd, #ffeeba);
        border: 3px solid #ffc107;
    }
    .fase-card {
        background: #f8f9fa;
        padding: 15px;
        border-radius: 8px;
        margin: 10px 0;
        border-left: 4px solid #1a5f7a;
    }
    .modelo-tag {
        display: inline-block;
        background: #e9ecef;
        padding: 3px 8px;
        border-radius: 4px;
        font-size: 0.85em;
        margin: 2px;
    }
    .verificacao-item {
        padding: 8px;
        margin: 5px 0;
        border-radius: 5px;
    }
    .verificacao-ok { background: #d4edda; }
    .verificacao-erro { background: #f8d7da; }
    .verificacao-alerta { background: #fff3cd; }
    .doc-card {
        background: #f8f9fa;
        padding: 10px;
        border-radius: 8px;
        margin: 5px 0;
        border: 1px solid #dee2e6;
    }
</style>
""", unsafe_allow_html=True)


def inicializar_sessao():
    """Inicializa variáveis de sessão."""
    if "processor" not in st.session_state:
        st.session_state.processor = LexForumProcessor()

    if "resultado" not in st.session_state:
        st.session_state.resultado = None

    if "resultados_multiplos" not in st.session_state:
        st.session_state.resultados_multiplos = []

    if "pagina" not in st.session_state:
        st.session_state.pagina = "analisar"

    if "documentos_carregados" not in st.session_state:
        st.session_state.documentos_carregados = []

    if "perguntas_raw_docs" not in st.session_state:
        st.session_state.perguntas_raw_docs = ""

    if "perguntas_raw_texto" not in st.session_state:
        st.session_state.perguntas_raw_texto = ""


def renderizar_ui_perguntas(page_key: str) -> str:
    """
    Renderiza UI de perguntas e retorna texto bruto.

    Args:
        page_key: Identificador único da página (ex: "docs" ou "texto")
    """
    st.markdown("### ❓ Perguntas do Utilizador (opcional)")
    st.markdown("*Pode fazer perguntas específicas sobre os documentos. Separe perguntas diferentes com: ---*")

    perguntas_raw = st.text_area(
        "Perguntas (opcional)",
        height=150,
        key=f"perguntas_utilizador_{page_key}",
        placeholder="""Escreva aqui a(s) pergunta(s). Pode usar várias linhas.

Para separar perguntas diferentes, escreva uma linha com:
---

Exemplo:
Qual é o prazo de recurso deste ato administrativo?
---
Que legislação portuguesa é aplicável a este caso?"""
    )

    if perguntas_raw and perguntas_raw.strip():
        perguntas = parse_perguntas(perguntas_raw)
        st.info(f"📊 Perguntas detectadas: {len(perguntas)}")

        pode_continuar, msg_validacao = validar_perguntas(perguntas)

        if pode_continuar:
            if "AVISO" in msg_validacao or "⚠" in msg_validacao:
                st.warning(msg_validacao)
            else:
                st.success(msg_validacao)

            with st.expander("📋 Preview das perguntas"):
                for i, p in enumerate(perguntas, 1):
                    preview = p[:200] + '...' if len(p) > 200 else p
                    st.markdown(f"**{i}.** {preview}")
        else:
            st.error(f"❌ {msg_validacao}")
            st.stop()

    return perguntas_raw


def _mostrar_paginas_problematicas_resumo(doc: DocumentContent):
    """Mostra resumo das páginas problemáticas de um documento."""
    if not doc.pdf_safe_result:
        return

    pdf_result = doc.pdf_safe_result
    problematic = pdf_result.get_problematic_pages()

    # Também verificar páginas com flags bloqueantes (usa helpers centralizados)
    from src.ui.page_repair import get_flag_explanation

    # Adicionar páginas com flags bloqueantes que não estão já na lista
    problematic_nums = {p.page_num for p in problematic}
    for page in pdf_result.pages:
        if page.page_num not in problematic_nums:
            # Se precisa reparação (usa helper centralizado)
            if precisa_reparacao(page):
                problematic.append(page)

    for page in problematic:
        status_emoji = "🔴" if page.status_final == "SUSPEITA" else "⚠️"
        st.markdown(f"- **Página {page.page_num}**: {page.status_final} {status_emoji}")
        st.caption(f"  Caracteres: {page.metrics.chars_clean} | Ruído: {page.metrics.noise_ratio:.1%}")

        # Mostrar flags com explicações
        if hasattr(page, 'flags') and page.flags:
            blocking = [f for f in page.flags if f in FLAGS_BLOQUEANTES]
            if blocking:
                for flag in blocking:
                    st.caption(f"  {get_flag_explanation(flag)}")

        # Mostrar imagem se existir
        if page.image_path and Path(page.image_path).exists():
            if st.button(f"Ver página {page.page_num}", key=f"preview_p{page.page_num}_{doc.filename}"):
                st.image(page.image_path, caption=f"Página {page.page_num}", use_container_width=True)


def renderizar_header():
    """Renderiza o cabeçalho."""
    st.markdown("""
    <div class="main-header">
        <h1>⚖️ LEXFORUM</h1>
        <p>Análise Jurídica com Pipeline de 3 Fases | Direito Português 🇵🇹</p>
    </div>
    """, unsafe_allow_html=True)


def renderizar_sidebar():
    """Renderiza a barra lateral."""
    with st.sidebar:
        st.markdown("## 📋 Menu")

        if st.button("📄 Analisar Documento(s)", use_container_width=True):
            st.session_state.pagina = "analisar"

        if st.button("📝 Analisar Texto", use_container_width=True):
            st.session_state.pagina = "texto"

        if st.button("📚 Histórico", use_container_width=True):
            st.session_state.pagina = "historico"

        # ← NOVO: Botão Perguntas Adicionais
        if st.button("💬 Perguntas Adicionais", use_container_width=True):
            st.session_state.pagina = "perguntas"

        # ← NOVO: Botão Gerir Títulos
        if st.button("✏️ Gerir Títulos", use_container_width=True):
            st.session_state.pagina = "titulos"

        # ← NOVO: Botão gestão API Keys
        if st.button("🔑 API Keys", use_container_width=True):
            st.session_state.pagina = "api_keys"

        if st.button("⚙️ Configurações", use_container_width=True):
            st.session_state.pagina = "config"

        if st.button("❓ Como Funciona", use_container_width=True):
            st.session_state.pagina = "ajuda"

        st.markdown("---")

        # Status API
        st.markdown("### 🔌 Status")

        if OPENROUTER_API_KEY and len(OPENROUTER_API_KEY) > 10:
            st.success("✓ API Key configurada")
        else:
            st.error("✗ API Key em falta")
            st.caption("Configure OPENROUTER_API_KEY no .env")

        st.markdown("---")

        # Modelos configurados - CORRIGIDO: usar LLM_CONFIGS
        st.markdown("### 🤖 Modelos")
        with st.expander("Ver modelos configurados"):
            st.caption("**Fase 1 - Extratores (5 especializados):**")
            for cfg in LLM_CONFIGS:
                st.caption(f"• {cfg['id']}: {cfg['model'].split('/')[-1]} ({cfg['role']})")

            st.caption("**Fase 2 - Auditores:**")
            for m in AUDITOR_MODELS:
                st.caption(f"• {m.split('/')[-1]}")

            st.caption("**Fase 3 - Relatores:**")
            for m in JUIZ_MODELS:
                st.caption(f"• {m.split('/')[-1]}")

            st.caption(f"**Conselheiro-Mor:** {PRESIDENTE_MODEL.split('/')[-1]}")

            st.caption(f"**Agregador:** {AGREGADOR_MODEL.split('/')[-1]}")
            st.caption(f"**Chefe:** {CHEFE_MODEL.split('/')[-1]}")

        st.markdown("---")
        st.caption("LexForum v2.0\nApenas Direito Português 🇵🇹")


def carregar_documentos(uploaded_files, use_pdf_safe: bool = True, out_dir: Path = None) -> List[DocumentContent]:
    """
    Carrega múltiplos documentos.

    Args:
        uploaded_files: Lista de ficheiros uploaded
        use_pdf_safe: Se True, usa PDF Seguro para PDFs
        out_dir: Diretório BASE para outputs do PDF Seguro (cada PDF terá subdiretório)
    """
    import hashlib

    documentos = []
    loader = DocumentLoader()

    # Inicializar caches
    if "pdf_bytes_cache" not in st.session_state:
        st.session_state.pdf_bytes_cache = {}
    if "pdf_out_dirs" not in st.session_state:
        st.session_state.pdf_out_dirs = {}

    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.read()
        uploaded_file.seek(0)  # Reset para reutilização

        ext = Path(uploaded_file.name).suffix.lower()

        # Usar PDF Seguro para PDFs se ativado
        if ext == ".pdf" and use_pdf_safe:
            # CORREÇÃO #1: Criar diretório ÚNICO por documento
            file_hash = hashlib.md5(file_bytes).hexdigest()[:8]
            stem = Path(uploaded_file.name).stem
            # Sanitizar nome (remover caracteres problemáticos)
            stem_safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
            out_dir_doc = out_dir / f"{stem_safe}_{file_hash}" if out_dir else None

            doc = loader.load_pdf_safe(
                io.BytesIO(file_bytes),
                filename=uploaded_file.name,
                out_dir=out_dir_doc
            )

            # Guardar bytes e diretório para uso posterior
            st.session_state.pdf_bytes_cache[uploaded_file.name] = file_bytes
            if out_dir_doc:
                st.session_state.pdf_out_dirs[uploaded_file.name] = out_dir_doc
        else:
            doc = loader.load(
                io.BytesIO(file_bytes),
                filename=uploaded_file.name
            )
        documentos.append(doc)

    return documentos


def pagina_analisar_documento():
    """Página para analisar documento(s)."""
    st.markdown("## 📄 Analisar Documento(s)")

    # Se há resultado carregado do histórico, mostrar diretamente
    if st.session_state.resultado:
        col_info, col_btn = st.columns([5, 1])
        with col_info:
            st.success(f"📖 A visualizar análise: {st.session_state.resultado.run_id}")
        with col_btn:
            if st.button("🔙 Nova Análise", use_container_width=True):
                st.session_state.resultado = None
                st.rerun()

        renderizar_resultado(st.session_state.resultado)
        return  # Não mostrar formulário

    col1, col2 = st.columns([2, 1])

    # Inicializar session_state para ficheiros acumulados
    if "ficheiros_acumulados" not in st.session_state:
        st.session_state.ficheiros_acumulados = {}  # {nome: bytes}

    with col1:
        # Upload de ficheiros (múltiplos - ACUMULATIVO)
        uploaded_files = st.file_uploader(
            "Adicionar documento(s)",
            type=["pdf", "docx", "xlsx", "txt"],
            accept_multiple_files=True,
            help="Adicione ficheiros um a um ou vários de cada vez. Os ficheiros são ACUMULADOS automaticamente.",
            key="uploader_principal"
        )

        # Acumular novos ficheiros
        if uploaded_files:
            for f in uploaded_files:
                if f.name not in st.session_state.ficheiros_acumulados:
                    st.session_state.ficheiros_acumulados[f.name] = f.getvalue()

        # Mostrar ficheiros acumulados com opção de remover
        if st.session_state.ficheiros_acumulados:
            st.markdown(f"### 📁 {len(st.session_state.ficheiros_acumulados)} documento(s) seleccionado(s)")

            # Botão para limpar todos
            col_info, col_limpar = st.columns([3, 1])
            with col_limpar:
                if st.button("🗑️ Limpar todos", key="limpar_todos"):
                    st.session_state.ficheiros_acumulados = {}
                    st.session_state.documentos_carregados = []
                    st.rerun()

            # Lista de ficheiros com botão remover individual
            ficheiros_a_remover = []
            for nome, dados in st.session_state.ficheiros_acumulados.items():
                col_nome, col_tam, col_rem = st.columns([3, 1, 1])
                with col_nome:
                    st.write(f"📄 {nome}")
                with col_tam:
                    st.write(f"{len(dados)/1024:.1f} KB")
                with col_rem:
                    if st.button("❌", key=f"rem_{nome}", help=f"Remover {nome}"):
                        ficheiros_a_remover.append(nome)

            # Remover ficheiros marcados
            for nome in ficheiros_a_remover:
                del st.session_state.ficheiros_acumulados[nome]
                st.rerun()

        # Opção PDF Seguro
        use_pdf_safe = st.checkbox(
            "Usar PDF Seguro (extração página-a-página)",
            value=True,
            help="Ativa controlo por página, deteção de problemas e reparação guiada para PDFs"
        )

        # Processar ficheiros acumulados
        if st.session_state.ficheiros_acumulados:
            # Criar objectos UploadedFile-like a partir dos bytes guardados
            import uuid
            from io import BytesIO

            class FicheiroPseudo:
                def __init__(self, name, data):
                    self.name = name
                    self._data = data
                def read(self):
                    return self._data
                def getvalue(self):
                    return self._data
                def seek(self, pos):
                    pass

            ficheiros_para_carregar = [
                FicheiroPseudo(nome, dados)
                for nome, dados in st.session_state.ficheiros_acumulados.items()
            ]

            # Criar diretório temporário para PDF Seguro
            temp_out_dir = OUTPUT_DIR / f"temp_{uuid.uuid4().hex[:8]}"

            # Carregar documentos
            with st.spinner("A processar documento(s)..."):
                documentos = carregar_documentos(
                    ficheiros_para_carregar,
                    use_pdf_safe=use_pdf_safe,
                    out_dir=temp_out_dir if use_pdf_safe else None
                )
                st.session_state.documentos_carregados = documentos
                if use_pdf_safe:
                    st.session_state.pdf_safe_out_dir = temp_out_dir

            # Mostrar resumo dos documentos processados
            st.markdown("---")
            st.markdown("**Documentos processados:**")

            for i, doc in enumerate(documentos):
                if doc.success:
                    # Mostrar info adicional para PDF Seguro
                    extra_info = ""
                    if doc.pdf_safe_enabled:
                        if doc.pages_problematic > 0:
                            extra_info = f" | <span style='color:orange;'>🔴 {doc.pages_problematic} página(s) problemática(s)</span>"
                        else:
                            extra_info = " | <span style='color:green;'>✅ Todas as páginas OK</span>"

                    st.markdown(f"""
                    <div class="doc-card">
                        ✅ <strong>{doc.filename}</strong><br>
                        <small>{doc.num_chars:,} caracteres | {doc.num_words:,} palavras | {doc.num_pages} página(s){extra_info}</small>
                    </div>
                    """, unsafe_allow_html=True)

                    # Mostrar aviso de páginas problemáticas
                    if doc.pdf_safe_enabled and doc.pages_problematic > 0:
                        with st.expander(f"⚠️ Ver páginas problemáticas ({doc.pages_problematic})"):
                            _mostrar_paginas_problematicas_resumo(doc)
                else:
                    st.error(f"❌ {doc.filename}: {doc.error}")

            # Preview do primeiro documento
            docs_ok = [d for d in documentos if d.success]
            if docs_ok:
                with st.expander(f"📖 Preview: {docs_ok[0].filename}"):
                    st.text_area(
                        "Texto extraído",
                        value=docs_ok[0].text[:5000] + ("..." if len(docs_ok[0].text) > 5000 else ""),
                        height=200,
                        disabled=True
                    )

        # Área do direito
        area = st.selectbox(
            "Área do Direito",
            options=AREAS_DIREITO,
            help="Selecione a área do direito aplicável"
        )

    with col2:
        st.markdown("### ℹ️ Pipeline de 3 Fases")
        st.markdown("""
        **Fase 1 - Extração:**
        5 LLMs especializados extraem informação

        **Fase 2 - Auditoria:**
        4 LLMs auditam e validam a extração

        **Fase 3 - Relatoria:**
        3 LLMs emitem parecer + Conselheiro-Mor verifica
        """)

        st.markdown("### 🏛️ Símbolos")
        st.markdown(f"""
        - **{SIMBOLOS_VERIFICACAO['aprovado']}** Procedente/Aprovado
        - **{SIMBOLOS_VERIFICACAO['rejeitado']}** Improcedente/Rejeitado
        - **{SIMBOLOS_VERIFICACAO['atencao']}** Atenção/Parcial
        """)

        st.markdown("### 📋 Formatos")
        for ext, nome in SUPPORTED_EXTENSIONS.items():
            st.caption(f"• {ext} - {nome}")

    st.markdown("---")

    # UI de perguntas Q&A
    perguntas_raw = renderizar_ui_perguntas("docs")

    st.markdown("---")

    # ← NOVO: Interface escolha modelos premium
    model_choices = selecao_modelos_premium()
    
    # Guardar escolhas no session_state
    st.session_state.model_choices = model_choices

    st.markdown("---")

    # Verificar se há documentos válidos (definir ANTES de usar)
    docs_validos = [d for d in st.session_state.documentos_carregados if d.success]

    # ← NOVO: Campo título do projeto
    st.markdown("### 📝 Título do Projeto (opcional)")

    titulo_projeto = st.text_input(
        "Dê um nome amigável a esta análise:",
        value="",
        placeholder="Ex: Contrato Arrendamento - João Silva",
        help="Se deixar vazio, será gerado automaticamente baseado no nome do ficheiro",
        key="titulo_projeto_docs"
    )

    # Mostrar pré-visualização do título automático
    if docs_validos and not titulo_projeto:
        titulo_auto = gerar_titulo_automatico(docs_validos[0].filename, area)
        st.caption(f"💡 Título automático: {titulo_auto}")

    st.markdown("---")

    # Verificar páginas problemáticas não resolvidas (usa helper centralizado)
    paginas_nao_resolvidas = 0
    docs_com_problemas = []

    for doc in docs_validos:
        if doc.pdf_safe_enabled and doc.pdf_safe_result:
            # Contar TODAS as páginas que precisam reparação (consistente com verificar_pode_finalizar)
            unresolved = [p for p in doc.pdf_safe_result.pages if precisa_reparacao(p)]

            if unresolved:
                paginas_nao_resolvidas += len(unresolved)
                docs_com_problemas.append(f"{doc.filename} ({len(unresolved)})")

    # Condições para executar
    tem_api = OPENROUTER_API_KEY and len(OPENROUTER_API_KEY) > 10
    tem_docs = len(docs_validos) > 0
    sem_paginas_bloqueantes = paginas_nao_resolvidas == 0

    pode_executar = tem_docs and tem_api and sem_paginas_bloqueantes

    # Botão de execução
    col_btn1, col_btn2, col_btn3 = st.columns([1, 2, 1])

    with col_btn2:
        if not tem_api:
            st.warning("⚠️ Configure a OPENROUTER_API_KEY no ficheiro .env")

        # CORREÇÃO #2: Mostrar erro se há páginas não resolvidas
        if paginas_nao_resolvidas > 0:
            st.error(f"❌ **BLOQUEADO:** {paginas_nao_resolvidas} página(s) problemática(s) não resolvida(s) em: {', '.join(docs_com_problemas)}")
            st.info("✅ Resolva as páginas problemáticas (marcar visual-only ou reparar) antes de continuar.")

        # Construir help text dinâmico
        help_text = None
        if not pode_executar:
            reasons = []
            if not tem_api:
                reasons.append("API Key não configurada")
            if not tem_docs:
                reasons.append("Nenhum documento carregado")
            if not sem_paginas_bloqueantes:
                reasons.append(f"{paginas_nao_resolvidas} página(s) problemática(s) não resolvida(s)")
            help_text = "Bloqueado: " + "; ".join(reasons)

        executar = st.button(
            f"⚖️ ANALISAR {len(docs_validos)} DOCUMENTO(S)" if docs_validos else "⚖️ ANALISAR",
            use_container_width=True,
            type="primary",
            disabled=not pode_executar,
            help=help_text
        )

        if executar:
            executar_pipeline_documentos(docs_validos, area, perguntas_raw, titulo_projeto)

    # Mostrar resultado(s)
    if st.session_state.resultado:
        renderizar_resultado(st.session_state.resultado)


def pagina_analisar_texto():
    """Página para analisar texto direto."""
    st.markdown("## 📝 Analisar Texto")

    # Se há resultado carregado do histórico, mostrar diretamente
    if st.session_state.resultado:
        col_info, col_btn = st.columns([5, 1])
        with col_info:
            st.success(f"📖 A visualizar análise: {st.session_state.resultado.run_id}")
        with col_btn:
            if st.button("🔙 Nova Análise", key="nova_texto", use_container_width=True):
                st.session_state.resultado = None
                st.rerun()

        renderizar_resultado(st.session_state.resultado)
        return  # Não mostrar formulário

    col1, col2 = st.columns([2, 1])

    with col1:
        texto = st.text_area(
            "Texto do caso",
            height=400,
            placeholder="""Cole aqui o texto do caso a analisar...

Exemplo:
O requerente celebrou contrato de arrendamento com o requerido em 01/01/2024, nos termos do artigo 1022º do Código Civil. O requerido deixou de pagar a renda desde março de 2024, encontrando-se em dívida no valor de €3.000,00.

Nos termos do artigo 1083º do Código Civil, o requerente pretende a resolução do contrato e a condenação do requerido no pagamento das rendas em atraso, acrescidas de juros de mora, conforme artigo 806º do CC."""
        )

        area = st.selectbox(
            "Área do Direito",
            options=AREAS_DIREITO,
            key="area_texto"
        )

    with col2:
        st.markdown("### ℹ️ Dicas")
        st.markdown("""
        Para uma análise completa, inclua:

        - **Factos**: O que aconteceu
        - **Datas**: Quando aconteceu
        - **Partes**: Quem está envolvido
        - **Valores**: Montantes em causa
        - **Legislação**: Artigos aplicáveis
        - **Pedido**: O que se pretende

        O sistema irá:
        1. Extrair informação (5 LLMs especializados)
        2. Auditar a extração (3 LLMs)
        3. Emitir parecer jurídico (3 LLMs)
        4. Presidente verificar e decidir
        5. Verificar legislação citada no DRE
        """)

    st.markdown("---")

    # UI de perguntas Q&A
    perguntas_raw = renderizar_ui_perguntas("texto")

    st.markdown("---")

    # ← NOVO: Interface escolha modelos premium
    model_choices = selecao_modelos_premium()
    
    # Guardar escolhas no session_state
    st.session_state.model_choices = model_choices

    st.markdown("---")

    pode_executar = len(texto.strip()) >= 50 and OPENROUTER_API_KEY and len(OPENROUTER_API_KEY) > 10

    col_btn1, col_btn2, col_btn3 = st.columns([1, 2, 1])

    with col_btn2:
        if not OPENROUTER_API_KEY or len(OPENROUTER_API_KEY) < 10:
            st.warning("⚠️ Configure a OPENROUTER_API_KEY no ficheiro .env")

        if st.button("⚖️ ANALISAR TEXTO", use_container_width=True, type="primary", disabled=not pode_executar):
            if len(texto.strip()) < 50:
                st.error("⚠️ Introduza um texto com pelo menos 50 caracteres")
            else:
                executar_pipeline_texto(texto, area, perguntas_raw)

    if st.session_state.resultado:
        renderizar_resultado(st.session_state.resultado)


def executar_pipeline_documentos(documentos: List[DocumentContent], area: str, perguntas_raw: str = "", titulo: str = ""):
    """Executa o pipeline para múltiplos documentos."""
    progress_bar = st.progress(0)
    status_text = st.empty()

    # Mostrar info sobre perguntas
    perguntas = parse_perguntas(perguntas_raw)
    if perguntas:
        st.info(f"📊 {len(perguntas)} pergunta(s) serão processadas na Fase 3 e 4")

    # v4.0 FIX: Calcular modelos localmente, NÃO mutar config global
    # (engine.py já resolve modelos internamente via tier_models)
    if "model_choices" in st.session_state:
        _local_chefe = get_chefe_model(st.session_state.model_choices["chefe"])
        _local_presidente = get_presidente_model(st.session_state.model_choices["presidente"])
    else:
        _local_chefe = CHEFE_MODEL
        _local_presidente = PRESIDENTE_MODEL

    # Combinar texto de todos os documentos
    textos_combinados = []
    for doc in documentos:
        if doc.text and doc.text.strip():
            textos_combinados.append(f"=== DOCUMENTO: {doc.filename} ===\n\n{doc.text}")
        else:
            st.warning(f"⚠️ Documento '{doc.filename}' não tem texto extraível!")

    if not textos_combinados:
        st.error("❌ Nenhum documento tem texto extraível! Verifique se os PDFs não são imagens escaneadas.")
        return

    texto_final = "\n\n" + "=" * 50 + "\n\n" + "\n\n".join(textos_combinados)

    # Debug: mostrar tamanho do texto
    st.info(f"📊 Texto total: {len(texto_final):,} caracteres")

    # Criar documento combinado
    documento_combinado = DocumentContent(
        filename=f"combinado_{len(documentos)}_docs.txt" if len(documentos) > 1 else documentos[0].filename,
        extension=documentos[0].extension if len(documentos) == 1 else ".txt",
        text=texto_final,
        num_chars=len(texto_final),
        num_words=len(texto_final.split()),
        num_pages=sum(d.num_pages for d in documentos),
        success=True
    )

    def callback(fase, progresso, mensagem):
        progress_bar.progress(progresso / 100)
        status_text.text(f"🔄 {fase}: {mensagem}")

    # === NOVO: Verificacao de tamanho e aviso ao utilizador ===
    from src.config import classificar_documento
    doc_chars = len(texto_final) if texto_final else 0
    classificacao = classificar_documento(doc_chars)

    if not classificacao["pode_processar"]:
        st.error(classificacao["mensagem"])
        return

    if classificacao["requer_confirmacao"]:
        st.warning(classificacao["mensagem"])
        if not st.checkbox("Compreendo e desejo continuar", key="confirmar_emergencia_docs"):
            st.info("Marque a caixa acima para prosseguir com a analise.")
            return

    if classificacao["nivel"] == 2 and classificacao["mensagem"]:
        st.info(classificacao["mensagem"])
    # === Fim da verificacao ===

    try:
        processor = LexForumProcessor(
            chefe_model=_local_chefe,
            presidente_model=_local_presidente,
            callback_progresso=callback,
        )
        resultado = processor.processar(documento_combinado, area, perguntas_raw, titulo)  # ← NOVO: passar titulo
        st.session_state.resultado = resultado

        progress_bar.progress(100)
        status_text.text("✅ Análise concluída!")

        st.rerun()

    except ValueError as e:
        st.error(f"❌ Erro de validação: {e}")
    except Exception as e:
        logger.exception("Erro no processamento")
        st.error(f"❌ Erro: {str(e)}")
        import traceback
        st.code(traceback.format_exc())


def executar_pipeline_texto(texto: str, area: str, perguntas_raw: str = ""):
    """Executa o pipeline com texto direto."""
    progress_bar = st.progress(0)
    status_text = st.empty()

    # Mostrar info sobre perguntas
    perguntas = parse_perguntas(perguntas_raw)
    if perguntas:
        st.info(f"📊 {len(perguntas)} pergunta(s) serão processadas na Fase 3 e 4")

    # v4.0 FIX: Calcular modelos localmente, NÃO mutar config global
    if "model_choices" in st.session_state:
        _local_chefe = get_chefe_model(st.session_state.model_choices["chefe"])
        _local_presidente = get_presidente_model(st.session_state.model_choices["presidente"])
    else:
        _local_chefe = CHEFE_MODEL
        _local_presidente = PRESIDENTE_MODEL

    def callback(fase, progresso, mensagem):
        progress_bar.progress(progresso / 100)
        status_text.text(f"🔄 {fase}: {mensagem}")

    # === NOVO: Verificacao de tamanho e aviso ao utilizador ===
    from src.config import classificar_documento
    doc_chars = len(texto.strip()) if texto else 0
    classificacao = classificar_documento(doc_chars)

    if not classificacao["pode_processar"]:
        st.error(classificacao["mensagem"])
        return

    if classificacao["requer_confirmacao"]:
        st.warning(classificacao["mensagem"])
        if not st.checkbox("Compreendo e desejo continuar", key="confirmar_emergencia_texto"):
            st.info("Marque a caixa acima para prosseguir com a analise.")
            return

    if classificacao["nivel"] == 2 and classificacao["mensagem"]:
        st.info(classificacao["mensagem"])
    # === Fim da verificacao ===

    try:
        processor = LexForumProcessor(
            chefe_model=_local_chefe,
            presidente_model=_local_presidente,
            callback_progresso=callback,
        )
        resultado = processor.processar_texto(texto, area, perguntas_raw)
        st.session_state.resultado = resultado

        progress_bar.progress(100)
        status_text.text("✅ Análise concluída!")

        st.rerun()

    except ValueError as e:
        st.error(f"❌ Erro de validação: {e}")
    except Exception as e:
        logger.exception("Erro no processamento")
        st.error(f"❌ Erro: {str(e)}")
        import traceback
        st.code(traceback.format_exc())


def renderizar_resultado(resultado: PipelineResult):
    """Renderiza o resultado do pipeline."""
    st.markdown("---")
    st.markdown("## 📋 Resultado da Análise")

    # Box do veredicto
    status_class = {
        "aprovado": "resultado-aprovado",
        "rejeitado": "resultado-rejeitado",
        "atencao": "resultado-atencao",
    }.get(resultado.status_final, "resultado-atencao")

    cor = CORES.get(resultado.status_final, CORES["neutro"])

    st.markdown(f"""
    <div class="resultado-box {status_class}" style="text-align: center;">
        <h1 style="color: {cor}; font-size: 4em; margin: 0;">
            {resultado.simbolo_final}
        </h1>
        <h2 style="color: {cor}; margin: 10px 0;">
            PARECER FINAL: {resultado.veredicto_final}
        </h2>
        <p>Run ID: {resultado.run_id}</p>
    </div>
    """, unsafe_allow_html=True)

    # Métricas
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Tokens Usados", f"{resultado.total_tokens:,}")

    with col2:
        latencia_s = resultado.total_latencia_ms / 1000 if resultado.total_latencia_ms else 0
        st.metric("Latência Total", f"{latencia_s:.1f}s")

    with col3:
        st.metric("Citações Verificadas", len(resultado.verificacoes_legais))

    with col4:
        if resultado.timestamp_fim and resultado.timestamp_inicio:
            duracao = (resultado.timestamp_fim - resultado.timestamp_inicio).total_seconds()
        else:
            duracao = 0
        st.metric("Duração", f"{duracao:.0f}s")

    # Tabs com detalhes (incluir Q&A se houver perguntas, páginas se PDF Seguro)
    tem_qa = bool(resultado.perguntas_utilizador)
    tem_pdf_safe = (resultado.documento and
                    hasattr(resultado.documento, 'pdf_safe_enabled') and
                    resultado.documento.pdf_safe_enabled)

    # Construir lista de tabs dinamicamente
    tab_names = ["🎯 Presidente"]
    if tem_qa:
        tab_names.append("❓ Q&A")
    if tem_pdf_safe:
        tab_names.append("📄 Páginas")
    tab_names.extend([
        "📊 Fase 1: Extração",
        "🔍 Fase 2: Auditoria",
        "⚖️ Fase 3: Relatoria",
        "📜 Verificação Legal"
    ])

    tabs = st.tabs(tab_names)
    tab_idx = 0

    tab_presidente = tabs[tab_idx]
    tab_idx += 1

    tab_qa = None
    if tem_qa:
        tab_qa = tabs[tab_idx]
        tab_idx += 1

    tab_pages = None
    if tem_pdf_safe:
        tab_pages = tabs[tab_idx]
        tab_idx += 1

    tab_ext = tabs[tab_idx]
    tab_idx += 1
    tab_aud = tabs[tab_idx]
    tab_idx += 1
    tab_julg = tabs[tab_idx]
    tab_idx += 1
    tab_legal = tabs[tab_idx]

    with tab_presidente:
        st.markdown("### Parecer do Conselheiro-Mor")
        if resultado.fase3_presidente:
            st.markdown(resultado.fase3_presidente)
        else:
            st.info("Parecer do Conselheiro-Mor não disponível")

    # Tab Q&A (só aparece se houver perguntas)
    if tab_qa is not None:
        with tab_qa:
            st.markdown("### ❓ Perguntas e Respostas")

            st.markdown("#### Perguntas do Utilizador")
            for i, p in enumerate(resultado.perguntas_utilizador, 1):
                st.markdown(f"**{i}.** {p}")

            st.markdown("---")

            st.markdown("#### Respostas dos Relatores")
            if resultado.respostas_juizes_qa:
                for r in resultado.respostas_juizes_qa:
                    with st.expander(f"Relator {r.get('juiz', '?')}: {r.get('modelo', 'desconhecido')}"):
                        st.markdown(r.get('resposta', 'Sem resposta'))

            st.markdown("---")

            st.markdown("#### Resposta Consolidada (Presidente)")
            if resultado.respostas_finais_qa:
                st.markdown(resultado.respostas_finais_qa)
            else:
                st.info("Resposta consolidada disponível na decisão do presidente")

    # Tab Páginas (só aparece se PDF Seguro)
    if tab_pages is not None:
        with tab_pages:
            st.markdown("### 📄 Controlo de Páginas (PDF Seguro)")

            if resultado.documento and resultado.documento.pdf_safe_result:
                pdf_result = resultado.documento.pdf_safe_result

                # Estatísticas
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total", pdf_result.total_pages)
                with col2:
                    st.metric("OK", pdf_result.pages_ok)
                with col3:
                    st.metric("Suspeita", pdf_result.pages_suspeita)
                with col4:
                    st.metric("Sem Texto", pdf_result.pages_sem_texto)

                # Verificar se pode finalizar
                from src.ui.page_repair import verificar_pode_finalizar
                pode_finalizar, msg = verificar_pode_finalizar(pdf_result)

                if pode_finalizar:
                    st.success(f"✅ {msg}")
                else:
                    st.warning(f"⚠️ {msg}")

                st.markdown("---")

                # Páginas problemáticas
                problematic = pdf_result.get_problematic_pages()
                problematic_nums = {p.page_num for p in problematic}

                # Incluir páginas OK com flags bloqueantes (usa helpers centralizados)
                for page in pdf_result.pages:
                    # Se já está na lista, skip
                    if page.page_num in problematic_nums:
                        continue

                    # Se precisa reparação (usa helper centralizado)
                    if precisa_reparacao(page):
                        problematic.append(page)
                        problematic_nums.add(page.page_num)

                # Ordenar por número de página (UX)
                problematic.sort(key=lambda p: p.page_num)

                if problematic:
                    st.markdown("### Páginas Problemáticas")

                    for page in problematic:
                        status_icon = {
                            "SUSPEITA": "🔴",
                            "SEM_TEXTO": "⚫",
                            "VISUAL_ONLY": "👁️",
                            "REPARADA": "✅",
                        }.get(page.status_final, "⚠️")

                        with st.expander(f"Página {page.page_num} - {page.status_final} {status_icon}"):
                            col_info, col_img = st.columns([2, 1])

                            with col_info:
                                st.markdown(f"**Caracteres:** {page.metrics.chars_clean}")
                                st.markdown(f"**Ruído:** {page.metrics.noise_ratio:.1%}")

                                # Mostrar flags com explicações
                                if page.flags:
                                    st.warning(f"**Alertas:** {len(page.flags)} flag(s)")
                                    from src.ui.page_repair import get_flag_explanation
                                    for flag in page.flags:
                                        explanation = get_flag_explanation(flag)
                                        st.caption(explanation)

                                if page.override_type:
                                    st.info(f"Reparação: {page.override_type}")

                            with col_img:
                                if page.image_path and Path(page.image_path).exists():
                                    st.image(page.image_path, caption=f"Página {page.page_num}", width=200)

                            # Mostrar opções de reparação (usa helper centralizado)
                            if precisa_reparacao(page):
                                st.markdown("**Reparação:**")
                                note = st.text_input(
                                    "Nota",
                                    key=f"note_{resultado.run_id}_{page.page_num}",
                                    placeholder="Descrição opcional..."
                                )

                                col_btn1, col_btn2 = st.columns(2)
                                with col_btn1:
                                    if st.button("Marcar Visual-only", key=f"visual_{resultado.run_id}_{page.page_num}"):
                                        from src.pipeline.pdf_safe import save_override
                                        out_dir = st.session_state.get('pdf_safe_out_dir', OUTPUT_DIR / resultado.run_id)
                                        save_override(out_dir, page.page_num, "visual_only", note=note or "Marcado via UI")
                                        page.status_final = "VISUAL_ONLY"
                                        page.override_type = "visual_only"
                                        st.success("✅ Marcado!")
                                        st.rerun()
                else:
                    st.success("✅ Todas as páginas foram extraídas corretamente!")

                # Provenance
                if pdf_result.document_provenance:
                    with st.expander("📋 Headers/Footers removidos"):
                        for line in pdf_result.document_provenance:
                            st.caption(line)
            else:
                st.info("ℹ️ Informação de páginas não disponível")

    with tab_ext:
        st.markdown("### Fase 1: Extração")
        if resultado.fase1_extracoes:
            st.markdown(f"*{len(resultado.fase1_extracoes)} extratores + Agregador LOSSLESS*")

            # Mostrar consolidado primeiro (principal)
            consolidado = getattr(resultado, 'fase1_agregado_consolidado', '') or resultado.fase1_agregado
            if consolidado:
                st.markdown("#### Extração Consolidada (Agregador)")
                st.info(f"Modelo: {AGREGADOR_MODEL}")
                st.markdown(consolidado)

            # Expander para bruto
            bruto = getattr(resultado, 'fase1_agregado_bruto', '')
            if bruto:
                with st.expander(f"📋 Ver extrações brutas ({len(resultado.fase1_extracoes)} modelos concatenados)"):
                    st.markdown(bruto)

            st.markdown("---")
            st.markdown("#### Extrações Individuais")
            for i, ext in enumerate(resultado.fase1_extracoes):
                with st.expander(f"Extrator {i+1}: {ext.modelo}", expanded=False):
                    st.caption(f"Tokens: {ext.tokens_usados} | Latência: {ext.latencia_ms:.0f}ms | Sucesso: {'✓' if ext.sucesso else '✗'}")
                    if ext.erro:
                        st.error(f"Erro: {ext.erro}")
                    st.markdown(ext.conteudo if ext.conteudo else "*Sem conteúdo*")
        else:
            st.info("ℹ️ Nenhuma extração disponível")

    with tab_aud:
        st.markdown("### Fase 2: Auditoria")
        if resultado.fase2_auditorias:
            st.markdown(f"*{len(resultado.fase2_auditorias)} auditores + Chefe LOSSLESS*")

            # Mostrar consolidado primeiro (principal)
            consolidado = getattr(resultado, 'fase2_chefe_consolidado', '') or resultado.fase2_chefe
            if consolidado:
                st.markdown("#### Auditoria Consolidada (Chefe)")
                st.info(f"Modelo: {CHEFE_MODEL}")
                st.markdown(consolidado)

            # Expander para bruto
            bruto = getattr(resultado, 'fase2_auditorias_brutas', '')
            if bruto:
                with st.expander(f"📋 Ver auditorias brutas ({len(resultado.fase2_auditorias)} modelos concatenados)"):
                    st.markdown(bruto)

            st.markdown("---")
            st.markdown("#### Auditorias Individuais")
            for i, aud in enumerate(resultado.fase2_auditorias):
                with st.expander(f"Auditor {i+1}: {aud.modelo}", expanded=False):
                    st.caption(f"Tokens: {aud.tokens_usados} | Latência: {aud.latencia_ms:.0f}ms | Sucesso: {'✓' if aud.sucesso else '✗'}")
                    if aud.erro:
                        st.error(f"Erro: {aud.erro}")
                    st.markdown(aud.conteudo if aud.conteudo else "*Sem conteúdo*")
        else:
            st.info("ℹ️ Nenhuma auditoria disponível")

    with tab_julg:
        st.markdown("### Fase 3: Relatoria")
        if resultado.fase3_pareceres:
            st.markdown(f"*{len(resultado.fase3_pareceres)} relatores executados*")

            for i, juiz in enumerate(resultado.fase3_pareceres):
                with st.expander(f"Relator {i+1}: {juiz.modelo}", expanded=(i == 0)):
                    st.caption(f"Tokens: {juiz.tokens_usados} | Latência: {juiz.latencia_ms:.0f}ms | Sucesso: {'✓' if juiz.sucesso else '✗'}")
                    if juiz.erro:
                        st.error(f"Erro: {juiz.erro}")
                    st.markdown(juiz.conteudo if juiz.conteudo else "*Sem conteúdo*")
        else:
            st.info("ℹ️ Nenhum parecer disponível")

    with tab_legal:
        st.markdown("### Verificação de Citações Legais")

        if resultado.verificacoes_legais:
            # Contar por status (aceitar variações)
            aprovadas = sum(1 for v in resultado.verificacoes_legais if v.status in ["aprovado", "verificada", "ok"])
            rejeitadas = sum(1 for v in resultado.verificacoes_legais if v.status in ["rejeitado", "nao_encontrada", "erro"])
            atencao_count = sum(1 for v in resultado.verificacoes_legais if v.status in ["atencao", "requer_atencao", "pendente"] or v.status not in ["aprovado", "verificada", "ok", "rejeitado", "nao_encontrada", "erro"])

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("✓ Verificadas", aprovadas)
            with col2:
                st.metric("✗ Não encontradas", rejeitadas)
            with col3:
                st.metric("⚠ Atenção", atencao_count)

            st.markdown("---")

            for v in resultado.verificacoes_legais:
                # Determinar cor com base no status
                if v.status in ["aprovado", "verificada", "ok"]:
                    cor_fundo = "#d4edda"
                    cor_borda = "#28a745"
                elif v.status in ["rejeitado", "nao_encontrada", "erro"]:
                    cor_fundo = "#f8d7da"
                    cor_borda = "#dc3545"
                else:
                    cor_fundo = "#fff3cd"
                    cor_borda = "#ffc107"

                # Obter texto normalizado
                texto_norm = ""
                if hasattr(v, 'citacao') and v.citacao:
                    texto_norm = getattr(v.citacao, 'texto_normalizado', '') or f"{getattr(v.citacao, 'diploma', '')} art. {getattr(v.citacao, 'artigo', '')}"

                # Obter símbolo
                simbolo = getattr(v, 'simbolo', '⚠') or '⚠'

                # Obter fonte e mensagem
                fonte = getattr(v, 'fonte', '') or ''
                mensagem = getattr(v, 'mensagem', '') or ''
                aplicabilidade = getattr(v, 'aplicabilidade', '⚠') or '⚠'

                st.markdown(
                    f"""
                    <div style="padding: 12px; margin: 8px 0; border-radius: 5px; background-color: {cor_fundo}; border-left: 4px solid {cor_borda};">
                        <strong style="color: #333;">{simbolo} {texto_norm}</strong><br>
                        <small style="color: #555;">Fonte: {fonte} | Aplicabilidade: {aplicabilidade}</small><br>
                        <small style="color: #666;">{mensagem}</small>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
        else:
            st.info("ℹ️ Nenhuma citação legal encontrada para verificar")

    # Exportação
    st.markdown("---")
    st.markdown("### 📤 Exportar")

    col1, col2, col3 = st.columns(3)

    with col1:
        json_data = json.dumps(resultado.to_dict(), ensure_ascii=False, indent=2)
        st.download_button(
            "📋 Baixar JSON",
            data=json_data,
            file_name=f"lexforum_{resultado.run_id}.json",
            mime="application/json"
        )

    with col2:
        # Gerar Markdown
        md_content = f"""# LexForum - Resultado

**Run ID:** {resultado.run_id}
**Data:** {resultado.timestamp_inicio.strftime('%d/%m/%Y %H:%M') if resultado.timestamp_inicio else 'N/A'}
**Documento:** {resultado.documento.filename if resultado.documento else 'Texto direto'}
**Área:** {resultado.area_direito}

---

## {resultado.simbolo_final} PARECER FINAL: {resultado.veredicto_final}

---

## Parecer do Conselheiro-Mor

{resultado.fase3_presidente or 'N/A'}

---

## Verificações Legais

"""
        for v in resultado.verificacoes_legais:
            md_content += f"- {v.simbolo} {v.citacao.texto_normalizado}\n"

        st.download_button(
            "📄 Baixar Markdown",
            data=md_content,
            file_name=f"lexforum_{resultado.run_id}.md",
            mime="text/markdown"
        )

    with col3:
        st.info(f"📁 Outputs em:\noutputs/{resultado.run_id}/")


def pagina_historico():
    """Página de histórico."""
    st.markdown("## 📚 Histórico de Análises")

    # ← NOVO: Usar função que retorna títulos
    analises = listar_analises_com_titulos(OUTPUT_DIR)

    if analises:
        st.markdown(f"*{len(analises)} análises encontradas*")

        for run_id, titulo_display, data in analises[:20]:
            # Carregar dados completos
            processor = LexForumProcessor()
            data_completa = processor.carregar_run(run_id)
            
            if not data_completa:
                continue
            
            # Extrair informações
            simbolo = data_completa.get("simbolo_final", "")
            veredicto = data_completa.get("status_final", "")
            
            # Criar título do expander
            if simbolo:
                titulo_expander = f"{simbolo} {titulo_display}"
            else:
                titulo_expander = f"📁 {titulo_display}"

            with st.expander(titulo_expander):
                col_info, col_btns = st.columns([3, 1])

                with col_info:
                    st.markdown(f"**Run ID:** `{run_id}`")
                    st.markdown(f"**Área:** {data_completa.get('area_direito', 'N/A')}")
                    st.markdown(f"**Tokens:** {data_completa.get('total_tokens', 0):,}")
                    
                    # Documento original
                    doc_info = data_completa.get('documento', {})
                    if isinstance(doc_info, dict):
                        doc_nome = doc_info.get('filename', 'N/A')
                    else:
                        doc_nome = str(doc_info) if doc_info else 'N/A'
                    st.markdown(f"**Documento:** {doc_nome}")

                    # Contar perguntas iniciais do pipeline
                    n_perguntas_iniciais = len(data_completa.get('perguntas_utilizador', []))

                    # Contar perguntas adicionais (ficheiros pergunta_*.json na pasta perguntas/)
                    perguntas_dir = OUTPUT_DIR / run_id / "perguntas"
                    n_perguntas_adicionais = 0
                    if perguntas_dir.exists():
                        n_perguntas_adicionais = len(list(perguntas_dir.glob("pergunta_*.json")))

                    # Mostrar totais
                    if n_perguntas_iniciais > 0:
                        st.markdown(f"**Perguntas Q&A (inicial):** {n_perguntas_iniciais}")
                    if n_perguntas_adicionais > 0:
                        st.markdown(f"**Perguntas Adicionais:** {n_perguntas_adicionais}")

                with col_btns:
                    if st.button("📖 Ver", key=f"ver_{run_id}", use_container_width=True):
                        try:
                            resultado = carregar_resultado(run_id)
                            st.session_state.resultado = resultado
                            st.session_state.pagina = "analisar"
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Erro ao carregar: {e}")
                    
                    # ← NOVO: Botão apagar com confirmação
                    if st.button("🗑️ Apagar", key=f"del_{run_id}", use_container_width=True, type="secondary"):
                        # Usar session_state para confirmação
                        if f"confirm_delete_{run_id}" not in st.session_state:
                            st.session_state[f"confirm_delete_{run_id}"] = False
                        
                        st.session_state[f"confirm_delete_{run_id}"] = True
                
                # Mostrar confirmação se botão apagar foi clicado
                if st.session_state.get(f"confirm_delete_{run_id}", False):
                    st.warning(f"⚠️ **TEM CERTEZA?** Isto apaga PERMANENTEMENTE a análise!")
                    st.caption(f"Análise: {titulo_display}")
                    st.caption(f"Run ID: {run_id}")
                    
                    col_confirm, col_cancel = st.columns(2)
                    
                    with col_confirm:
                        if st.button("✅ SIM, APAGAR!", key=f"confirm_yes_{run_id}", use_container_width=True, type="primary"):
                            try:
                                # Apagar pasta da análise
                                analise_dir = OUTPUT_DIR / run_id
                                if analise_dir.exists():
                                    shutil.rmtree(analise_dir)
                                
                                # Apagar do histórico também
                                historico_file = HISTORICO_DIR / f"{run_id}.json"
                                if historico_file.exists():
                                    historico_file.unlink()
                                
                                st.success(f"✅ Análise '{titulo_display}' apagada com sucesso!")
                                st.session_state[f"confirm_delete_{run_id}"] = False
                                
                                # Aguardar um pouco para mostrar mensagem
                                import time
                                time.sleep(1)
                                st.rerun()
                            
                            except Exception as e:
                                st.error(f"❌ Erro ao apagar: {e}")
                                st.session_state[f"confirm_delete_{run_id}"] = False
                    
                    with col_cancel:
                        if st.button("❌ Cancelar", key=f"confirm_no_{run_id}", use_container_width=True):
                            st.session_state[f"confirm_delete_{run_id}"] = False
                            st.rerun()

                # Preview da decisão
                if not st.session_state.get(f"confirm_delete_{run_id}", False):
                    presidente = data_completa.get('fase3_presidente', '')
                    if presidente:
                        st.markdown("**Preview da decisão:**")
                        st.text(presidente[:500] + "..." if len(presidente) > 500 else presidente)
    else:
        st.info("📭 Nenhuma análise no histórico")


def pagina_configuracoes():
    """Página de configurações."""
    st.markdown("## ⚙️ Configurações")

    st.markdown("### 🔑 API Key")

    if OPENROUTER_API_KEY and len(OPENROUTER_API_KEY) > 10:
        st.success(f"✓ API Key configurada: `{OPENROUTER_API_KEY[:15]}...{OPENROUTER_API_KEY[-4:]}`")
    else:
        st.error("✗ API Key não configurada")
        st.markdown("""
        **Para configurar:**
        1. Copie `.env.example` para `.env`
        2. Obtenha a chave em: https://openrouter.ai/keys
        3. Cole em `OPENROUTER_API_KEY=sk-or-v1-...`
        4. Reinicie o Streamlit
        """)

    st.markdown("---")

    st.markdown("### 🤖 Modelos Configurados")

    col1, col2 = st.columns(2)

    with col1:
        # CORRIGIDO: usar LLM_CONFIGS
        st.markdown("**Fase 1 - Extratores (5 especializados):**")
        for cfg in LLM_CONFIGS:
            st.code(f"{cfg['id']}: {cfg['model']} ({cfg['role']})")

        st.markdown("**Fase 2 - Auditores:**")
        for i, m in enumerate(AUDITOR_MODELS, 1):
            st.code(f"{i}. {m}")

    with col2:
        st.markdown("**Fase 3 - Relatores:**")
        for i, m in enumerate(JUIZ_MODELS, 1):
            st.code(f"{i}. {m}")

        st.markdown("**Conselheiro-Mor:**")
        st.code(PRESIDENTE_MODEL)

        st.markdown("---")

        st.markdown("**Agregador (Fase 1 - LOSSLESS):**")
        st.code(AGREGADOR_MODEL)

        st.markdown("**Consolidador (Fase 2 - LOSSLESS):**")
        st.code(CHEFE_MODEL)

    st.markdown("---")

    st.markdown("### 🧪 Testar Conexão APIs")

    col_test1, col_test2 = st.columns(2)

    with col_test1:
        if st.button("🔵 Testar OpenAI API", use_container_width=True):
            with st.spinner("A testar OpenAI..."):
                try:
                    client = get_llm_client()
                    results = client.test_connection()
                    
                    if results.get("openai", {}).get("success"):
                        st.success(results["openai"]["message"])
                    else:
                        st.error(results["openai"]["message"])
                except Exception as e:
                    st.error(f"✗ Erro: {str(e)}")

    with col_test2:
        if st.button("🟠 Testar OpenRouter API", use_container_width=True):
            with st.spinner("A testar OpenRouter..."):
                try:
                    client = get_llm_client()
                    results = client.test_connection()
                    
                    if results.get("openrouter", {}).get("success"):
                        st.success(results["openrouter"]["message"])
                    else:
                        st.error(results["openrouter"]["message"])
                except Exception as e:
                    st.error(f"✗ Erro: {str(e)}")

    st.markdown("---")

    st.markdown("### 🗑️ Limpar Sessão")

    if st.button("🔄 Limpar dados da sessão"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.success("✓ Sessão limpa!")
        st.rerun()


def pagina_ajuda():
    """Página de ajuda."""
    st.markdown("## ❓ Como Funciona")

    st.markdown("""
    ### 🏛️ O LexForum

    Este sistema analisa documentos jurídicos usando **Inteligência Artificial** através de um pipeline de 3 fases com **12 chamadas a modelos LLM** (5 extratores + 3 auditores + 3 relatores + Conselheiro-Mor):

    ---

    ### 📊 Fase 1: Extração (7 modelos especializados + Agregador LOSSLESS)

    ```
    Documento → [E1: Claude Sonnet 4.5] → Extração Jurídica Geral
             → [E2: Gemini 3 Flash] → Visão Alternativa
             → [E3: GPT-4o] → Validação Cruzada
             → [E4: Claude 3.5 Sonnet] → Dados Estruturados (datas, €, %)
             → [E5: DeepSeek] → Docs Administrativos (anexos, tabelas)
             → [E6: Llama 4 Maverick] → Extração Complementar
             → [E7: Mistral Medium 3] → Extração Complementar
                                  ↓
                        [AGREGADOR LLM: consolida LOSSLESS]
    ```

    **O que extraem:**
    - **E1-E3 (Generalistas):** Factos, contexto jurídico, referências legais
    - **E4-E5 (Especialistas):** TODAS as datas, valores €, percentagens, refs legais
    - **E6-E7 (Complementares):** Cobertura adicional, perspectivas alternativas

    **Agregador LOSSLESS:** GPT-5.2 consolida as 7 extrações **sem perder informação única**.
    Se um extrator encontrou um dado que outros não encontraram, esse dado é mantido.

    ---

    ### 🔍 Fase 2: Auditoria (4 modelos + Consolidador LOSSLESS)

    ```
    Extração Consolidada → [Auditor 1] → Auditoria A
                        → [Auditor 2] → Auditoria B
                        → [Auditor 3] → Auditoria C
                                             ↓
                               [CHEFE LLM: consolida LOSSLESS]
    ```

    **O que verificam:**
    - Completude da extração
    - Inconsistências
    - Informação em falta
    - Legislação portuguesa aplicável

    **Consolidador LOSSLESS:** GPT-5.2 consolida as 4 auditorias **sem perder críticas únicas**.

    ---

    ### ⚖️ Fase 3: Relatoria (3 modelos + Conselheiro-Mor)

    ```
    Auditoria Consolidada → [Relator 1] → Parecer A
                         → [Relator 2] → Parecer B
                         → [Relator 3] → Parecer C
                                          ↓
                        [CONSELHEIRO-MOR: verifica e decide]
                                          ↓
                               PARECER FINAL
    ```

    **O que emitem:**
    - Enquadramento legal (legislação portuguesa)
    - Análise dos factos à luz da lei
    - Jurisprudência relevante
    - Recomendação fundamentada

    ---

    ### 📜 Verificação Legal

    O sistema extrai automaticamente citações legais e verifica no **DRE (Diário da República Electrónico)**:

    - ✓ Legislação encontrada e verificada
    - ✗ Legislação não encontrada
    - ⚠ Requer verificação manual

    ---

    ### 📁 Outputs Auditáveis

    Cada análise gera ficheiros em `outputs/<run_id>/`:
    - `resultado.json` - Dados completos
    - `RESUMO.md` - Resumo legível
    - `fase1_extrator_E1.md` até `E5.md` - Extrações individuais
    - `fase1_agregado_consolidado.md` - Extração LOSSLESS
    - `fase2_*.md` - Auditorias
    - `fase3_*.md` - Pareceres
    - `fase4_conselheiro_mor.md` - Parecer final
    - `signals_coverage_report.json` - Relatório de cobertura de dados

    ---

    ### ⚠️ Aviso Legal

    Este é um sistema de **apoio à decisão**. **NÃO substitui aconselhamento jurídico profissional.**
    """)


# ═══════════════════════════════════════════════════════════════════════════
# PÁGINA: PERGUNTAS ADICIONAIS (NOVO - ISOLADO)
# ═══════════════════════════════════════════════════════════════════════════

def pagina_perguntas():
    """
    Página para fazer perguntas sobre análises já concluídas.
    
    Usa módulo TOTALMENTE ISOLADO que NÃO interfere com pipeline principal!
    """
    # ← NOVO: Interface escolha modelos premium
    st.markdown("### ⚙️ Configuração Modelos")
    model_choices = selecao_modelos_premium()
    
    # Guardar escolhas no session_state
    st.session_state.model_choices_perguntas = model_choices
    
    st.markdown("---")
    
    # v4.0 FIX: Calcular modelos localmente, NÃO mutar config global
    if "model_choices_perguntas" in st.session_state:
        _local_chefe_perguntas = get_chefe_model(st.session_state.model_choices_perguntas["chefe"])
        presidente_model_escolhido = get_presidente_model(st.session_state.model_choices_perguntas["presidente"])
    else:
        _local_chefe_perguntas = CHEFE_MODEL
        presidente_model_escolhido = PRESIDENTE_MODEL
    
    tab_perguntas_adicionais(
        output_dir=OUTPUT_DIR,
        auditor_models=AUDITOR_MODELS,
        juiz_models=JUIZ_MODELS,
        presidente_model=presidente_model_escolhido,
        llm_client=get_llm_client(),
        chefe_model=_local_chefe_perguntas,
    )


def pagina_gerir_titulos():
    """Página para gerir títulos de análises existentes."""
    st.title("✏️ Gerir Títulos das Análises")
    
    st.markdown("""
    Edite os títulos das suas análises para facilitar identificação.
    Análises sem título mostram apenas o código.
    """)
    
    # Listar análises
    analises = listar_analises_com_titulos(OUTPUT_DIR)
    
    if not analises:
        st.warning("📭 Nenhuma análise encontrada!")
        return
    
    # Contar sem título
    sem_titulo = contar_analises_sem_titulo(OUTPUT_DIR)
    
    if sem_titulo > 0:
        st.info(f"📝 {sem_titulo} análise(s) sem título personalizado")
    
    st.markdown("---")
    
    # Editor de títulos
    st.markdown("### 📋 Editar Títulos")
    
    for run_id, titulo_display, data in analises:
        with st.expander(f"📁 {titulo_display}", expanded=False):
            # Carregar metadata para pegar título atual
            from src.utils.metadata_manager import carregar_metadata
            metadata = carregar_metadata(run_id, OUTPUT_DIR)
            
            titulo_atual = metadata.get('titulo', '') if metadata else ''
            
            # Linha 1: Campo de título
            novo_titulo = st.text_input(
                "Título:",
                value=titulo_atual,
                placeholder="Ex: Contrato Arrendamento - João Silva",
                key=f"titulo_{run_id}"
            )
            
            # Linha 2: Botões Guardar e Apagar
            col_save, col_delete = st.columns(2)
            
            with col_save:
                if st.button("💾 Guardar Título", key=f"save_{run_id}", use_container_width=True, type="primary"):
                    if novo_titulo and novo_titulo != titulo_atual:
                        atualizar_metadata(run_id, OUTPUT_DIR, titulo=novo_titulo)
                        st.success(f"✅ Título atualizado!")
                        st.rerun()
                    elif not novo_titulo:
                        st.warning("⚠️ Título não pode ser vazio!")
            
            with col_delete:
                if st.button("🗑️ Apagar Análise", key=f"del_{run_id}", use_container_width=True, type="secondary"):
                    # Usar session_state para confirmação
                    if f"confirm_delete_{run_id}" not in st.session_state:
                        st.session_state[f"confirm_delete_{run_id}"] = False
                    
                    st.session_state[f"confirm_delete_{run_id}"] = True
            
            # Mostrar info adicional
            st.caption(f"**Run ID:** `{run_id}`")
            st.caption(f"**Data:** {data}")
            
            if metadata:
                area = metadata.get('area_direito', 'N/A')
                st.caption(f"**Área:** {area}")
            
            # Mostrar confirmação se botão apagar foi clicado
            if st.session_state.get(f"confirm_delete_{run_id}", False):
                st.markdown("---")
                st.error("⚠️ **ATENÇÃO! APAGAR ANÁLISE PERMANENTEMENTE?**")
                st.warning(f"**Análise:** {titulo_display}")
                st.warning(f"**Run ID:** {run_id}")
                st.caption("⚠️ Esta ação NÃO PODE SER DESFEITA! Todos os ficheiros serão eliminados!")
                
                col_yes, col_no = st.columns(2)
                
                with col_yes:
                    if st.button("✅ SIM, APAGAR TUDO!", key=f"yes_{run_id}", use_container_width=True, type="primary"):
                        try:
                            # Apagar pasta da análise
                            analise_dir = OUTPUT_DIR / run_id
                            if analise_dir.exists():
                                shutil.rmtree(analise_dir)
                            
                            # Apagar do histórico também
                            historico_file = HISTORICO_DIR / f"{run_id}.json"
                            if historico_file.exists():
                                historico_file.unlink()
                            
                            st.success(f"✅ Análise '{titulo_display}' apagada com sucesso!")
                            st.session_state[f"confirm_delete_{run_id}"] = False
                            
                            # Aguardar para mostrar mensagem
                            import time
                            time.sleep(1.5)
                            st.rerun()
                        
                        except Exception as e:
                            st.error(f"❌ Erro ao apagar: {e}")
                            st.session_state[f"confirm_delete_{run_id}"] = False
                
                with col_no:
                    if st.button("❌ CANCELAR", key=f"no_{run_id}", use_container_width=True):
                        st.session_state[f"confirm_delete_{run_id}"] = False
                        st.rerun()


def main():
    """Função principal."""
    carregar_css()
    inicializar_sessao()
    renderizar_header()
    renderizar_sidebar()

    pagina = st.session_state.pagina

    if pagina == "analisar":
        pagina_analisar_documento()
    elif pagina == "texto":
        pagina_analisar_texto()
    elif pagina == "historico":
        pagina_historico()
    elif pagina == "perguntas":
        pagina_perguntas()  # ← NOVO: Página perguntas adicionais
    elif pagina == "titulos":
        pagina_gerir_titulos()  # ← NOVO: Página gerir títulos
    elif pagina == "api_keys":
        pagina_api_keys()  # ← NOVO: Página gestão API Keys
    elif pagina == "config":
        pagina_configuracoes()
    elif pagina == "ajuda":
        pagina_ajuda()
    else:
        pagina_analisar_documento()


if __name__ == "__main__":
    main()

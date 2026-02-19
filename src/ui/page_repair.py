"""
UI de Reparação de Páginas Problemáticas (Streamlit).

Permite:
- Ver imagem de páginas problemáticas
- Reparar via upload, transcrição manual, ou marcar visual-only
- Exportar seleção de páginas
- Reanalisar após reparação
"""

import sys
from pathlib import Path

# Adicionar diretório raiz ao path (necessário para imports absolutos)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
from typing import Optional

from src.pipeline.pdf_safe import (
    PageRecord,
    PDFSafeResult,
    save_override,
    export_selected_pages,
    get_pdf_safe_loader,
)
from src.pipeline.constants import (
    ESTADOS_BLOQUEANTES,
    FLAGS_BLOQUEANTES,
    is_resolvida,
    has_flags_bloqueantes,
)


def renderizar_paginas_problematicas(
    pdf_result: PDFSafeResult,
    out_dir: Path,
    pdf_bytes: bytes,
    on_repair_callback: Optional[callable] = None
):
    """
    Renderiza secção de páginas problemáticas no Streamlit.

    Args:
        pdf_result: Resultado do PDF Seguro
        out_dir: Diretório de outputs
        pdf_bytes: Bytes do PDF original
        on_repair_callback: Callback para reanálise após reparação
    """
    problematic = pdf_result.get_problematic_pages()

    if not problematic:
        st.success("Todas as páginas foram extraídas com sucesso!")
        return

    st.warning(f"**{len(problematic)} página(s) requerem atenção**")

    # Estatísticas
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Páginas", pdf_result.total_pages)
    with col2:
        st.metric("OK", pdf_result.pages_ok)
    with col3:
        sem_texto = sum(1 for p in problematic if p.status_final == "SEM_TEXTO")
        suspeita = len(problematic) - sem_texto
        st.metric("Problemáticas", f"{suspeita} SUSPEITA / {sem_texto} SEM_TEXTO")

    st.markdown("---")

    # Lista de páginas problemáticas
    st.markdown("### Páginas Problemáticas")

    # Seleção para export
    if "selected_pages" not in st.session_state:
        st.session_state.selected_pages = []

    for page in problematic:
        with st.expander(
            f"Página {page.page_num} - {page.status_final} {_get_status_emoji(page)}",
            expanded=False
        ):
            _renderizar_pagina(page, out_dir, pdf_bytes, on_repair_callback)

    st.markdown("---")

    # Ações em lote
    st.markdown("### Ações em Lote")

    col1, col2 = st.columns(2)

    with col1:
        # Seleção de páginas para export
        page_nums = [p.page_num for p in problematic]
        selected = st.multiselect(
            "Selecionar páginas para exportar",
            options=page_nums,
            default=st.session_state.selected_pages,
            key="export_selection"
        )
        st.session_state.selected_pages = selected

    with col2:
        if selected:
            if st.button(f"Exportar {len(selected)} página(s) para PDF"):
                export_path = out_dir / f"export_pages_{'_'.join(map(str, selected))}.pdf"
                overrides_dir = out_dir / "overrides"

                success = export_selected_pages(
                    pdf_bytes,
                    selected,
                    export_path,
                    overrides_dir if overrides_dir.exists() else None
                )

                if success:
                    st.success(f"Exportado para: {export_path}")
                    # Download button
                    with open(export_path, 'rb') as f:
                        st.download_button(
                            "Baixar PDF exportado",
                            data=f.read(),
                            file_name=export_path.name,
                            mime="application/pdf"
                        )
                else:
                    st.error("Erro ao exportar páginas")

    # Reanalisar
    if on_repair_callback:
        st.markdown("---")
        repaired_count = sum(1 for p in pdf_result.pages if p.override_type)
        if repaired_count > 0:
            if st.button(f"Reanalisar {repaired_count} página(s) reparada(s)", type="primary"):
                on_repair_callback()


def _get_status_emoji(page: PageRecord) -> str:
    """Retorna emoji para o status da página."""
    status_map = {
        "OK": "",
        "SUSPEITA": "",
        "SEM_TEXTO": "",
        "VISUAL_ONLY": "",
        "REPARADA": "",
    }
    return status_map.get(page.status_final, "")


def _renderizar_pagina(
    page: PageRecord,
    out_dir: Path,
    pdf_bytes: bytes,
    on_repair_callback: Optional[callable]
):
    """Renderiza detalhes e opções de uma página."""

    # Info da página
    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown(f"**Status:** {page.status_final}")
        st.markdown(f"**Caracteres:** {page.metrics.chars_clean}")
        st.markdown(f"**Ruído:** {page.metrics.noise_ratio:.1%}")

        if page.flags:
            st.markdown(f"**Flags:** {', '.join(page.flags)}")

        if page.coverage_status:
            st.markdown(f"**Cobertura:** {page.coverage_status}")

        if page.override_type:
            st.info(f"**Reparação:** {page.override_type}")
            if page.override_note:
                st.caption(f"Nota: {page.override_note}")

    with col2:
        # Deteções automáticas
        if page.metrics.dates_detected:
            st.caption(f"Datas detetadas: {len(page.metrics.dates_detected)}")
        if page.metrics.values_detected:
            st.caption(f"Valores detetados: {len(page.metrics.values_detected)}")
        if page.metrics.legal_refs_detected:
            st.caption(f"Refs legais detetadas: {len(page.metrics.legal_refs_detected)}")

    # Imagem da página
    image_path = Path(page.image_path)
    if image_path.exists():
        if st.button(f"Ver imagem página {page.page_num}", key=f"view_{page.page_num}"):
            st.image(str(image_path), caption=f"Página {page.page_num}", use_container_width=True)

    # Preview do texto
    if page.text_clean:
        with st.expander("Ver texto extraído"):
            st.text_area(
                "Texto",
                value=page.text_clean[:2000] + ("..." if len(page.text_clean) > 2000 else ""),
                height=200,
                disabled=True,
                key=f"text_{page.page_num}"
            )

    st.markdown("---")

    # Opções de reparação (só se não estiver já reparada)
    if page.status_final not in ["OK", "REPARADA", "VISUAL_ONLY"]:
        st.markdown("**Opções de Reparação:**")

        repair_tabs = st.tabs(["Upload", "Transcrição Manual", "Visual-only"])

        # Tab 1: Upload
        with repair_tabs[0]:
            uploaded = st.file_uploader(
                "Carregar página substituta (PDF/PNG/JPG)",
                type=["pdf", "png", "jpg", "jpeg"],
                key=f"upload_{page.page_num}"
            )

            if uploaded:
                # Guardar upload
                overrides_dir = out_dir / "overrides"
                overrides_dir.mkdir(exist_ok=True)

                upload_ext = Path(uploaded.name).suffix
                upload_path = overrides_dir / f"page_{page.page_num:03d}_upload{upload_ext}"

                with open(upload_path, 'wb') as f:
                    f.write(uploaded.read())

                # Tentar extrair texto
                extracted_text = ""
                loader = get_pdf_safe_loader()

                if upload_ext.lower() in ['.png', '.jpg', '.jpeg']:
                    extracted_text = loader.ocr_page(str(upload_path))
                elif upload_ext.lower() == '.pdf':
                    # Extrair primeira página do PDF
                    import fitz
                    doc = fitz.open(str(upload_path))
                    if len(doc) > 0:
                        extracted_text = doc[0].get_text("text")
                    doc.close()

                note = st.text_input("Nota (opcional)", key=f"note_upload_{page.page_num}")

                if st.button("Confirmar Upload", key=f"confirm_upload_{page.page_num}"):
                    save_override(
                        out_dir,
                        page.page_num,
                        "upload",
                        text=extracted_text,
                        note=note,
                        original_image=page.image_path
                    )
                    page.override_type = "upload"
                    page.override_text = extracted_text
                    page.override_note = note
                    page.status_final = "REPARADA"
                    st.success("Upload guardado!")
                    st.rerun()

        # Tab 2: Transcrição Manual
        with repair_tabs[1]:
            manual_text = st.text_area(
                "Transcrever conteúdo da página",
                height=300,
                placeholder="Digite ou cole a transcrição manual do conteúdo desta página...",
                key=f"manual_{page.page_num}"
            )

            note = st.text_input("Nota (opcional)", key=f"note_manual_{page.page_num}")

            if st.button("Guardar Transcrição", key=f"confirm_manual_{page.page_num}"):
                if manual_text.strip():
                    save_override(
                        out_dir,
                        page.page_num,
                        "manual_transcription",
                        text=manual_text,
                        note=note,
                        original_image=page.image_path
                    )
                    page.override_type = "manual_transcription"
                    page.override_text = manual_text
                    page.override_note = note
                    page.status_final = "REPARADA"
                    st.success("Transcrição guardada!")
                    st.rerun()
                else:
                    st.warning("Introduza algum texto")

        # Tab 3: Visual-only
        with repair_tabs[2]:
            st.info("Marcar como Visual-only indica que esta página contém apenas elementos "
                   "visuais (assinatura, carimbo, etc.) sem texto relevante.")

            note = st.text_input(
                "Descrição do conteúdo visual",
                placeholder="Ex: Página de assinaturas, carimbo autenticação...",
                key=f"note_visual_{page.page_num}"
            )

            if st.button("Marcar Visual-only", key=f"confirm_visual_{page.page_num}"):
                save_override(
                    out_dir,
                    page.page_num,
                    "visual_only",
                    text="",
                    note=note or "Página marcada como visual-only",
                    original_image=page.image_path
                )
                page.override_type = "visual_only"
                page.override_note = note
                page.status_final = "VISUAL_ONLY"
                st.success("Marcada como visual-only!")
                st.rerun()


def renderizar_resumo_cobertura(pdf_result: PDFSafeResult, coverage_matrix: dict):
    """
    Renderiza resumo da matriz de cobertura.

    Args:
        pdf_result: Resultado do PDF Seguro
        coverage_matrix: Matriz de cobertura dos extratores
    """
    st.markdown("### Matriz de Cobertura")

    if not coverage_matrix or not coverage_matrix.get("pages"):
        st.info("Matriz de cobertura não disponível")
        return

    # Contar por status
    pages_info = coverage_matrix["pages"]
    coberta = sum(1 for p in pages_info.values() if p.get("status") == "COBERTA")
    parcial = sum(1 for p in pages_info.values() if p.get("status") == "PARCIAL")
    nao_coberta = sum(1 for p in pages_info.values() if p.get("status") == "NAO_COBERTA")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Cobertas (3 extratores)", coberta)
    with col2:
        st.metric("Parciais (1-2 extratores)", parcial)
    with col3:
        st.metric("Não cobertas", nao_coberta)

    # Tabela de cobertura
    if st.checkbox("Ver detalhes por página"):
        data = []
        for pn, info in sorted(pages_info.items(), key=lambda x: int(x[0])):
            covered = info.get("covered_by", {})
            data.append({
                "Página": pn,
                "E1": "" if covered.get("E1") else "",
                "E2": "" if covered.get("E2") else "",
                "E3": "" if covered.get("E3") else "",
                "Status": info.get("status", "?"),
            })

        st.dataframe(data, use_container_width=True)


def verificar_pode_finalizar(pdf_result: PDFSafeResult) -> tuple:
    """
    Verifica se o run pode ser finalizado.

    Usa helpers centralizados de src.pipeline.constants.

    Returns:
        (pode_finalizar: bool, mensagem: str)
    """
    problematic_pages = []
    reasons = {}  # page_num -> list of reasons

    for page in pdf_result.pages:
        # Se pagina foi resolvida, ignorar tudo
        if is_resolvida(page):
            continue

        page_reasons = []

        # Verificar estado bloqueante
        if page.status_final in ESTADOS_BLOQUEANTES:
            page_reasons.append(f"estado={page.status_final}")

        # Verificar flags bloqueantes
        if has_flags_bloqueantes(page):
            blocking_flags = [f for f in page.flags if f in FLAGS_BLOQUEANTES]
            page_reasons.extend(blocking_flags)

        if page_reasons:
            problematic_pages.append(page.page_num)
            reasons[page.page_num] = page_reasons

    if problematic_pages:
        # Construir mensagem detalhada
        details = []
        for pn in problematic_pages[:5]:
            details.append(f"pg{pn}({', '.join(reasons[pn][:2])})")

        msg = f"{len(problematic_pages)} página(s) com problemas: {', '.join(details)}"
        if len(problematic_pages) > 5:
            msg += "..."

        return False, msg

    return True, "Todas as páginas verificadas"


def get_flag_explanation(flag: str) -> str:
    """Retorna explicação amigável para uma flag (compatível com Windows cp1252)."""
    explanations = {
        "SUSPEITA_DATA_NAO_EXTRAIDO": "[AVISO] Pagina contem datas que podem nao ter sido extraidas",
        "SUSPEITA_VALOR_NAO_EXTRAIDO": "[AVISO] Pagina contem valores monetarios que podem nao ter sido extraidos",
        "SUSPEITA_REF_LEGAL_NAO_EXTRAIDO": "[AVISO] Pagina contem referencias legais que podem nao ter sido extraidas",
        "COBERTURA_NAO_COBERTA": "[ERRO] Nenhum extrator conseguiu processar esta pagina",
        "COBERTURA_PARCIAL": "[AVISO] Apenas alguns extratores processaram esta pagina",
    }
    return explanations.get(flag, f"[AVISO] {flag}")

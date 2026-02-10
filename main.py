# -*- coding: utf-8 -*-
"""
MAIN - Tribunal SaaS V2 (FastAPI)
============================================================
Servidor principal com:
  - Conexão ao Supabase
  - Rota de saúde (GET /health)
  - Rota protegida de teste (GET /me)
  - Rota de análise (POST /analyze)
============================================================
"""

import io
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, UploadFile, File, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth_service import get_current_user, get_supabase, get_supabase_admin
from src.engine import (
    executar_analise_documento,
    InsufficientBalanceError,
    InvalidDocumentError,
    MissingApiKeyError,
    EngineError,
)

logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente (.env)
load_dotenv()


# ============================================================
# LIFESPAN - startup / shutdown
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa recursos no arranque e limpa no shutdown."""
    # -- Startup --
    # Validar que as variáveis obrigatórias existem
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if not supabase_url or not supabase_key:
        print("[AVISO] SUPABASE_URL ou SUPABASE_KEY não definidos no .env")
    else:
        get_supabase()
        print(f"[OK] Supabase (anon) conectado: {supabase_url[:40]}...")

    if not service_role_key:
        print("[AVISO] SUPABASE_SERVICE_ROLE_KEY não definida - operações de servidor falharão")
    else:
        get_supabase_admin()
        print(f"[OK] Supabase (service_role) conectado.")

    print("[OK] Tribunal SaaS V2 - Servidor iniciado.")

    yield

    # -- Shutdown --
    print("[OK] Servidor encerrado.")


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Tribunal SaaS V2",
    description="API para análise jurídica com IA",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS - permitir frontend Lovable e qualquer subdomínio
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.lovableproject\.com",
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ROTAS PÚBLICAS
# ============================================================

@app.get("/health")
async def health():
    """Rota de saúde - verifica se o servidor está online."""
    return {"status": "online"}


# ============================================================
# ROTAS PROTEGIDAS (requerem autenticação)
# ============================================================

@app.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """Retorna dados do utilizador autenticado (rota de teste)."""
    return {
        "user_id": user["id"],
        "email": user["email"],
    }


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    area_direito: str = Form("Civil"),
    perguntas_raw: str = Form(""),
    titulo: str = Form(""),
    chefe_model_key: str = Form("gpt-5.2"),
    presidente_model_key: str = Form("gpt-5.2"),
    user: dict = Depends(get_current_user),
):
    """
    Executa a análise jurídica completa de um documento.

    - Verifica saldo do utilizador (mínimo 2.00 EUR)
    - Extrai texto do ficheiro (PDF, DOCX, XLSX, TXT)
    - Executa pipeline de 4 fases (Extração, Auditoria, Julgamento, Presidente)
    - Retorna resultado completo em JSON
    """
    try:
        file_bytes = await file.read()

        resultado = executar_analise_documento(
            user_id=user["id"],
            file_bytes=file_bytes,
            filename=file.filename,
            area_direito=area_direito,
            perguntas_raw=perguntas_raw,
            titulo=titulo,
            chefe_model_key=chefe_model_key,
            presidente_model_key=presidente_model_key,
        )

        return resultado.to_dict()

    except InsufficientBalanceError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Saldo insuficiente. Por favor carregue a conta. "
                   f"(Saldo atual: {e.saldo_atual:.2f} EUR, "
                   f"mínimo: {e.saldo_minimo:.2f} EUR)",
        )

    except InvalidDocumentError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    except MissingApiKeyError as e:
        logger.error(f"API Key em falta: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço temporariamente indisponível. Contacte o suporte.",
        )

    except EngineError as e:
        logger.error(f"Erro do engine: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    except Exception as e:
        logger.exception("Erro inesperado no endpoint /analyze")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno do servidor.",
        )


# ============================================================
# EXPORTAÇÃO DE RELATÓRIOS (PDF / DOCX)
# ============================================================

class ExportRequest(BaseModel):
    analysis_result: Dict[str, Any]


def _build_pdf(data: Dict[str, Any]) -> bytes:
    """Gera PDF profissional a partir do resultado da análise."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm,
        leftMargin=2 * cm, rightMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "TribunalTitle", parent=styles["Title"],
        fontSize=20, textColor=HexColor("#1a1a2e"), spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        "SectionHead", parent=styles["Heading1"],
        fontSize=14, textColor=HexColor("#16213e"), spaceBefore=16, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        "BodyText2", parent=styles["BodyText"],
        fontSize=10, leading=14, spaceAfter=6,
    ))

    elements = []

    # Título
    elements.append(Paragraph("Relatório de Análise Jurídica — Tribunal AI", styles["TribunalTitle"]))
    elements.append(Spacer(1, 10))

    # Metadados
    meta = [
        ["Run ID", data.get("run_id", "N/A")],
        ["Área de Direito", data.get("area_direito", "N/A")],
        ["Início", data.get("timestamp_inicio", "N/A")],
        ["Fim", data.get("timestamp_fim", "N/A")],
        ["Total Tokens", str(data.get("total_tokens", 0))],
    ]
    t = Table(meta, colWidths=[5 * cm, 12 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), HexColor("#e8e8e8")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 16))

    # Veredicto Final
    simbolo = data.get("simbolo_final", "")
    veredicto = data.get("veredicto_final", "Sem veredicto")
    elements.append(Paragraph("Veredicto Final", styles["SectionHead"]))
    elements.append(Paragraph(f"{simbolo} {veredicto}", styles["BodyText2"]))
    elements.append(Spacer(1, 10))

    # Fase 1 — Extração
    elements.append(Paragraph("Fase 1 — Extração", styles["SectionHead"]))
    f1 = data.get("fase1_agregado_consolidado") or data.get("fase1_agregado", "")
    for line in (f1 or "Sem dados de extração.").split("\n"):
        if line.strip():
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            elements.append(Paragraph(safe, styles["BodyText2"]))
    elements.append(Spacer(1, 10))

    # Fase 2 — Auditoria
    elements.append(Paragraph("Fase 2 — Auditoria", styles["SectionHead"]))
    f2 = data.get("fase2_chefe_consolidado") or data.get("fase2_chefe", "")
    for line in (f2 or "Sem dados de auditoria.").split("\n"):
        if line.strip():
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            elements.append(Paragraph(safe, styles["BodyText2"]))
    elements.append(Spacer(1, 10))

    # Fase 3 — Julgamento
    elements.append(Paragraph("Fase 3 — Julgamento", styles["SectionHead"]))
    for p in data.get("fase3_pareceres", []):
        conteudo = p.get("conteudo", "") if isinstance(p, dict) else ""
        modelo = p.get("modelo", "") if isinstance(p, dict) else ""
        if modelo:
            elements.append(Paragraph(f"<b>{modelo}</b>", styles["BodyText2"]))
        for line in conteudo.split("\n"):
            if line.strip():
                safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                elements.append(Paragraph(safe, styles["BodyText2"]))
        elements.append(Spacer(1, 6))

    # Fase 4 — Presidente
    elements.append(Paragraph("Fase 4 — Decisão do Presidente", styles["SectionHead"]))
    f4 = data.get("fase3_presidente", "")
    for line in (f4 or "Sem decisão do presidente.").split("\n"):
        if line.strip():
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            elements.append(Paragraph(safe, styles["BodyText2"]))

    # Rodapé
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(
        "Gerado automaticamente por Tribunal AI — Este documento não substitui aconselhamento jurídico.",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=7, textColor=HexColor("#999999")),
    ))

    doc.build(elements)
    return buf.getvalue()


def _build_docx(data: Dict[str, Any]) -> bytes:
    """Gera DOCX profissional a partir do resultado da análise."""
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    style = doc.styles["Normal"]
    style.font.size = Pt(10)
    style.font.name = "Calibri"

    # Título
    title = doc.add_heading("Relatório de Análise Jurídica — Tribunal AI", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Metadados (tabela)
    table = doc.add_table(rows=5, cols=2)
    table.style = "Light Grid Accent 1"
    meta_rows = [
        ("Run ID", data.get("run_id", "N/A")),
        ("Área de Direito", data.get("area_direito", "N/A")),
        ("Início", data.get("timestamp_inicio", "N/A")),
        ("Fim", data.get("timestamp_fim", "N/A")),
        ("Total Tokens", str(data.get("total_tokens", 0))),
    ]
    for i, (label, value) in enumerate(meta_rows):
        table.rows[i].cells[0].text = label
        table.rows[i].cells[1].text = str(value) if value else "N/A"
        for cell in table.rows[i].cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)

    doc.add_paragraph("")

    # Veredicto Final
    doc.add_heading("Veredicto Final", level=1)
    simbolo = data.get("simbolo_final", "")
    veredicto = data.get("veredicto_final", "Sem veredicto")
    p = doc.add_paragraph(f"{simbolo} {veredicto}")
    p.runs[0].bold = True

    # Fase 1
    doc.add_heading("Fase 1 — Extração", level=1)
    f1 = data.get("fase1_agregado_consolidado") or data.get("fase1_agregado", "")
    doc.add_paragraph(f1 or "Sem dados de extração.")

    # Fase 2
    doc.add_heading("Fase 2 — Auditoria", level=1)
    f2 = data.get("fase2_chefe_consolidado") or data.get("fase2_chefe", "")
    doc.add_paragraph(f2 or "Sem dados de auditoria.")

    # Fase 3
    doc.add_heading("Fase 3 — Julgamento", level=1)
    for p_data in data.get("fase3_pareceres", []):
        if isinstance(p_data, dict):
            modelo = p_data.get("modelo", "")
            conteudo = p_data.get("conteudo", "")
            if modelo:
                doc.add_heading(modelo, level=2)
            doc.add_paragraph(conteudo)

    # Fase 4
    doc.add_heading("Fase 4 — Decisão do Presidente", level=1)
    f4 = data.get("fase3_presidente", "")
    doc.add_paragraph(f4 or "Sem decisão do presidente.")

    # Rodapé
    doc.add_paragraph("")
    footer = doc.add_paragraph(
        "Gerado automaticamente por Tribunal AI — "
        "Este documento não substitui aconselhamento jurídico."
    )
    footer.runs[0].font.size = Pt(7)
    footer.runs[0].font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@app.post("/export/pdf")
async def export_pdf(req: ExportRequest):
    """Exporta o resultado da análise como PDF."""
    try:
        pdf_bytes = _build_pdf(req.analysis_result)
        run_id = req.analysis_result.get("run_id", "relatorio")
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=relatorio_{run_id}.pdf"},
        )
    except Exception as e:
        logger.exception("Erro ao gerar PDF")
        raise HTTPException(status_code=500, detail=f"Erro ao gerar PDF: {e}")


@app.post("/export/docx")
async def export_docx(req: ExportRequest):
    """Exporta o resultado da análise como DOCX."""
    try:
        docx_bytes = _build_docx(req.analysis_result)
        run_id = req.analysis_result.get("run_id", "relatorio")
        return StreamingResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename=relatorio_{run_id}.docx"},
        )
    except Exception as e:
        logger.exception("Erro ao gerar DOCX")
        raise HTTPException(status_code=500, detail=f"Erro ao gerar DOCX: {e}")


# ============================================================
# PERGUNTAS PÓS-ANÁLISE (POST /ask)
# ============================================================

ASK_MODELS = [
    "openai/gpt-5.2",
    "anthropic/claude-opus-4.5",
    "google/gemini-3-pro-preview",
]

ASK_SYSTEM_PROMPT = (
    "És um jurista especializado em Direito Português. "
    "Com base na análise jurídica fornecida, responde à pergunta do utilizador "
    "de forma clara e fundamentada, citando legislação quando aplicável."
)

CONSOLIDATION_SYSTEM_PROMPT = (
    "Recebeste 3 respostas de juristas diferentes à mesma pergunta. "
    "Consolida-as numa única resposta coerente, mantendo os pontos de consenso "
    "e assinalando divergências. Cita legislação mencionada. "
    "Responde em português de Portugal."
)


class AskRequest(BaseModel):
    question: str
    analysis_result: Dict[str, Any]
    document_id: str = ""
    previous_qa: List[Dict[str, str]] = []


def _build_ask_context(data: Dict[str, Any], previous_qa: List[Dict[str, str]] = None) -> str:
    """Extrai contexto relevante do resultado da análise para a pergunta."""
    parts = []

    area = data.get("area_direito", "")
    if area:
        parts.append(f"Área de Direito: {area}")

    veredicto = data.get("veredicto_final", "")
    simbolo = data.get("simbolo_final", "")
    if veredicto:
        parts.append(f"Veredicto: {simbolo} {veredicto}")

    f1 = data.get("fase1_agregado_consolidado") or data.get("fase1_agregado", "")
    if f1:
        parts.append(f"EXTRAÇÃO:\n{f1[:3000]}")

    f2 = data.get("fase2_chefe_consolidado") or data.get("fase2_chefe", "")
    if f2:
        parts.append(f"AUDITORIA:\n{f2[:3000]}")

    f4 = data.get("fase3_presidente", "")
    if f4:
        parts.append(f"DECISÃO PRESIDENTE:\n{f4[:3000]}")

    # Documentos adicionais (se existirem)
    docs_adicionais = data.get("documentos_adicionais", [])
    if docs_adicionais:
        parts.append("DOCUMENTOS ADICIONAIS:")
        for doc in docs_adicionais:
            nome = doc.get("filename", "documento")
            texto = doc.get("text", "")[:2000]
            parts.append(f"--- {nome} ---\n{texto}")

    # Histórico de Q&A anteriores
    if previous_qa:
        parts.append("PERGUNTAS E RESPOSTAS ANTERIORES:")
        for qa in previous_qa:
            parts.append(f"P: {qa.get('question', '')}")
            parts.append(f"R: {qa.get('answer', '')}")

    return "\n\n".join(parts) if parts else "Sem contexto de análise disponível."


@app.post("/ask")
async def ask_question(req: AskRequest):
    """
    Pergunta pós-análise: envia a pergunta a 3 LLMs com contexto
    da análise anterior + histórico de Q&A e consolida as respostas.
    """
    from src.llm_client import get_llm_client

    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="A pergunta não pode estar vazia.")

    context = _build_ask_context(req.analysis_result, req.previous_qa)

    prompt = f"""ANÁLISE JURÍDICA ANTERIOR:

{context}

PERGUNTA DO UTILIZADOR:
{question}

Responde de forma clara, citando legislação quando aplicável."""

    llm = get_llm_client()
    individual_responses: List[Dict[str, str]] = []

    # Enviar a 3 LLMs
    for model in ASK_MODELS:
        try:
            resp = llm.chat_simple(
                model=model,
                prompt=prompt,
                system_prompt=ASK_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=4096,
            )
            individual_responses.append({
                "model": model,
                "response": resp.content,
            })
        except Exception as e:
            logger.warning(f"Modelo {model} falhou no /ask: {e}")
            individual_responses.append({
                "model": model,
                "response": f"[Erro: modelo indisponível]",
            })

    # Consolidar respostas
    if len([r for r in individual_responses if not r["response"].startswith("[Erro")]) == 0:
        raise HTTPException(
            status_code=503,
            detail="Nenhum modelo conseguiu responder. Tente novamente.",
        )

    respostas_texto = "\n\n".join([
        f"## {r['model']}:\n{r['response']}"
        for r in individual_responses
        if not r["response"].startswith("[Erro")
    ])

    consolidation_prompt = f"""PERGUNTA: {question}

RESPOSTAS DOS 3 JURISTAS:

{respostas_texto}

Consolida estas respostas numa única resposta final coerente."""

    try:
        consolidated = llm.chat_simple(
            model=ASK_MODELS[0],
            prompt=consolidation_prompt,
            system_prompt=CONSOLIDATION_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=4096,
        )
        answer = consolidated.content
    except Exception as e:
        logger.warning(f"Consolidação falhou: {e}")
        answer = next(
            (r["response"] for r in individual_responses if not r["response"].startswith("[Erro")),
            "Não foi possível gerar resposta.",
        )

    # Guardar Q&A no Supabase (se document_id fornecido)
    if req.document_id:
        try:
            sb = get_supabase_admin()
            doc_resp = sb.table("documents").select("analysis_result").eq("id", req.document_id).single().execute()
            current_result = doc_resp.data.get("analysis_result") or {}

            qa_history = current_result.get("qa_history", [])
            qa_history.append({
                "question": question,
                "answer": answer,
                "individual_responses": individual_responses,
                "timestamp": datetime.now().isoformat(),
            })
            current_result["qa_history"] = qa_history

            sb.table("documents").update(
                {"analysis_result": current_result}
            ).eq("id", req.document_id).execute()

            logger.info(f"Q&A guardada no Supabase: doc={req.document_id}, total_qa={len(qa_history)}")
        except Exception as e:
            logger.warning(f"Erro ao guardar Q&A no Supabase: {e}")

    return {
        "question": question,
        "answer": answer,
        "individual_responses": individual_responses,
    }


# ============================================================
# ADICIONAR DOCUMENTO A PROJECTO EXISTENTE
# ============================================================

@app.post("/analyze/add")
async def add_document_to_project(
    file: UploadFile = File(...),
    document_id: str = Form(...),
    user: dict = Depends(get_current_user),
):
    """
    Adiciona um novo documento a um projecto existente.
    Extrai o texto e guarda-o no analysis_result do documento original.
    """
    from src.engine import carregar_documento_de_bytes

    # 1. Ler o ficheiro novo
    file_bytes = await file.read()
    filename = file.filename or "documento"

    if len(file_bytes) == 0:
        raise HTTPException(status_code=422, detail="Ficheiro vazio.")

    # 2. Extrair texto
    try:
        doc = carregar_documento_de_bytes(file_bytes, filename)
        novo_texto = doc.text
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Erro ao extrair texto: {e}")

    if not novo_texto or len(novo_texto.strip()) < 50:
        raise HTTPException(
            status_code=422,
            detail="O documento não contém texto suficiente.",
        )

    # 3. Ler analysis_result actual do Supabase
    try:
        sb = get_supabase_admin()
        doc_resp = sb.table("documents").select("analysis_result, user_id").eq("id", document_id).single().execute()
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Documento não encontrado: {e}")

    # Verificar que o documento pertence ao utilizador
    doc_user_id = doc_resp.data.get("user_id", "")
    if doc_user_id and doc_user_id != user.get("id"):
        raise HTTPException(status_code=403, detail="Sem permissão para este documento.")

    current_result = doc_resp.data.get("analysis_result") or {}

    # 4. Adicionar o novo texto ao campo "documentos_adicionais"
    docs_adicionais = current_result.get("documentos_adicionais", [])
    docs_adicionais.append({
        "filename": filename,
        "text": novo_texto,
        "chars": len(novo_texto),
        "added_at": datetime.now().isoformat(),
    })
    current_result["documentos_adicionais"] = docs_adicionais

    # 5. Gravar no Supabase
    try:
        sb.table("documents").update(
            {"analysis_result": current_result}
        ).eq("id", document_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao guardar no Supabase: {e}")

    logger.info(
        f"Documento adicionado: doc={document_id}, file={filename}, "
        f"chars={len(novo_texto)}, total_docs_adicionais={len(docs_adicionais)}"
    )

    # 6. Retornar confirmação
    return {
        "status": "ok",
        "document_id": document_id,
        "added_file": filename,
        "added_chars": len(novo_texto),
        "total_additional_docs": len(docs_adicionais),
        "message": f"Documento '{filename}' adicionado ao projecto. "
                   f"Use /ask para fazer perguntas sobre todos os documentos.",
    }

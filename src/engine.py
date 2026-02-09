# -*- coding: utf-8 -*-
"""
ENGINE - Motor de Analise do Tribunal SaaS V2
============================================================
Logica PURA de processamento, sem qualquer dependencia Streamlit.
Coordena o pipeline de 4 fases (Extracao, Auditoria, Julgamento, Presidente).

Recebe:
  - file_bytes (bytes) ou texto (str)
  - area_direito (str)
  - perguntas_raw (str, opcional)
  - user_id (str) para verificacao de saldo no Supabase

Retorna:
  - PipelineResult com todos os resultados

Verificacao de saldo:
  - Antes de iniciar, consulta user_wallets no Supabase
  - Se saldo < SALDO_MINIMO (2.00 EUR), lanca InsufficientBalanceError
============================================================
"""

import sys
from pathlib import Path

# Garantir que o projecto raiz esta no path (para auth_service, etc.)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import io
import hashlib
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass

# Nota: src.__init__.py estende __path__ para incluir Referencia antiga/src/
# Portanto todos os imports `from src.*` encontram os modulos da referencia.
from src.config import (
    AREAS_DIREITO,
    OPENROUTER_API_KEY,
    OUTPUT_DIR,
)
from src.pipeline.processor import TribunalProcessor, PipelineResult, FaseResult
from src.document_loader import DocumentLoader, DocumentContent
from src.llm_client import get_llm_client
from src.utils.perguntas import parse_perguntas, validar_perguntas
from src.utils.metadata_manager import gerar_titulo_automatico

from auth_service import get_supabase_admin

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTES
# ============================================================

SALDO_MINIMO = 2.00  # EUR - saldo minimo para iniciar analise


# ============================================================
# EXCEPCOES CUSTOMIZADAS
# ============================================================

class EngineError(Exception):
    """Erro base do engine."""
    pass


class InsufficientBalanceError(EngineError):
    """Saldo insuficiente para executar a analise."""

    def __init__(self, saldo_atual: float, saldo_minimo: float = SALDO_MINIMO):
        self.saldo_atual = saldo_atual
        self.saldo_minimo = saldo_minimo
        super().__init__(
            f"Saldo insuficiente: {saldo_atual:.2f} EUR. "
            f"Minimo necessario: {saldo_minimo:.2f} EUR."
        )


class InvalidDocumentError(EngineError):
    """Documento invalido ou sem texto extraivel."""
    pass


class MissingApiKeyError(EngineError):
    """API Key nao configurada."""
    pass


# ============================================================
# VERIFICACAO DE SALDO
# ============================================================

SALDO_INICIAL = 0.00  # EUR - saldo atribuido a novos utilizadores


def verificar_saldo(user_id: str) -> float:
    """
    Consulta o saldo do utilizador na tabela user_wallets do Supabase.
    Usa a service_role key para ignorar RLS.

    Se o utilizador nao tiver wallet (novo utilizador), cria uma
    automaticamente com saldo inicial.

    Args:
        user_id: UUID do utilizador (de auth.users)

    Returns:
        Saldo atual em EUR (float)

    Raises:
        EngineError: Se nao conseguir consultar o Supabase
    """
    try:
        sb = get_supabase_admin()

        # Consultar wallet (sem .single() para evitar PGRST116 em 0 rows)
        response = (
            sb.table("user_wallets")
            .select("balance")
            .eq("user_id", user_id)
            .execute()
        )

        # Se o utilizador nao tem wallet, criar uma automaticamente
        if not response.data:
            logger.info(f"Wallet nao encontrada para {user_id[:8]}... Criando nova wallet.")
            insert_response = (
                sb.table("user_wallets")
                .insert({"user_id": user_id, "balance": SALDO_INICIAL})
                .execute()
            )
            if not insert_response.data:
                raise EngineError(
                    f"Nao foi possivel criar wallet para user_id={user_id}."
                )
            print(f"[WALLET] Nova wallet criada para {user_id[:8]}... com {SALDO_INICIAL:.2f} EUR")
            return SALDO_INICIAL

        saldo = float(response.data[0]["balance"])
        return saldo

    except EngineError:
        raise
    except Exception as e:
        raise EngineError(f"Erro ao consultar saldo no Supabase: {e}")


def exigir_saldo_minimo(user_id: str, minimo: float = SALDO_MINIMO) -> float:
    """
    Verifica se o utilizador tem saldo suficiente.

    Args:
        user_id: UUID do utilizador
        minimo: Saldo minimo exigido (default: 2.00 EUR)

    Returns:
        Saldo atual (se >= minimo)

    Raises:
        InsufficientBalanceError: Se saldo < minimo
    """
    saldo = verificar_saldo(user_id)

    if saldo < minimo:
        raise InsufficientBalanceError(saldo_atual=saldo, saldo_minimo=minimo)

    print(f"[SALDO] Utilizador {user_id[:8]}... tem {saldo:.2f} EUR (minimo: {minimo:.2f} EUR)")
    return saldo


# ============================================================
# CARREGAMENTO DE DOCUMENTOS (SEM STREAMLIT)
# ============================================================

def carregar_documento_de_bytes(
    file_bytes: bytes,
    filename: str,
    use_pdf_safe: bool = True,
    out_dir: Optional[Path] = None,
) -> DocumentContent:
    """
    Carrega e extrai texto de um ficheiro a partir dos seus bytes.

    Args:
        file_bytes: Conteudo binario do ficheiro
        filename: Nome original do ficheiro (ex: "contrato.pdf")
        use_pdf_safe: Se True, usa extracao pagina-a-pagina para PDFs
        out_dir: Directorio para outputs do PDF Seguro

    Returns:
        DocumentContent com texto extraido

    Raises:
        InvalidDocumentError: Se o documento nao tiver texto extraivel
    """
    loader = DocumentLoader()
    ext = Path(filename).suffix.lower()

    if ext == ".pdf" and use_pdf_safe:
        file_hash = hashlib.md5(file_bytes).hexdigest()[:8]
        stem = Path(filename).stem
        stem_safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
        out_dir_doc = out_dir / f"{stem_safe}_{file_hash}" if out_dir else None

        doc = loader.load_pdf_safe(
            io.BytesIO(file_bytes),
            filename=filename,
            out_dir=out_dir_doc,
        )
    else:
        doc = loader.load(
            io.BytesIO(file_bytes),
            filename=filename,
        )

    if not doc.success:
        raise InvalidDocumentError(
            f"Falha ao carregar documento '{filename}': {doc.error}"
        )

    if not doc.text or not doc.text.strip():
        raise InvalidDocumentError(
            f"Documento '{filename}' nao tem texto extraivel. "
            f"Verifique se o PDF nao e uma imagem escaneada."
        )

    print(f"[DOC] Carregado: {filename} | {doc.num_chars:,} chars | {doc.num_words:,} palavras | {doc.num_pages} paginas")
    return doc


def carregar_multiplos_documentos(
    ficheiros: List[Dict[str, bytes]],
    use_pdf_safe: bool = True,
    out_dir: Optional[Path] = None,
) -> List[DocumentContent]:
    """
    Carrega multiplos documentos a partir de bytes.

    Args:
        ficheiros: Lista de dicts com {"filename": str, "bytes": bytes}
        use_pdf_safe: Se True, usa PDF Seguro para PDFs
        out_dir: Directorio base para outputs

    Returns:
        Lista de DocumentContent carregados com sucesso
    """
    documentos = []
    for f in ficheiros:
        try:
            doc = carregar_documento_de_bytes(
                file_bytes=f["bytes"],
                filename=f["filename"],
                use_pdf_safe=use_pdf_safe,
                out_dir=out_dir,
            )
            documentos.append(doc)
        except InvalidDocumentError as e:
            print(f"[AVISO] {e}")

    return documentos


# ============================================================
# COMBINACAO DE DOCUMENTOS
# ============================================================

def combinar_documentos(documentos: List[DocumentContent]) -> DocumentContent:
    """
    Combina multiplos DocumentContent num unico documento.

    Args:
        documentos: Lista de documentos validos

    Returns:
        DocumentContent combinado
    """
    if len(documentos) == 1:
        return documentos[0]

    textos = []
    for doc in documentos:
        textos.append(f"=== DOCUMENTO: {doc.filename} ===\n\n{doc.text}")

    texto_final = "\n\n" + "=" * 50 + "\n\n" + "\n\n".join(textos)

    return DocumentContent(
        filename=f"combinado_{len(documentos)}_docs.txt",
        extension=".txt",
        text=texto_final,
        num_chars=len(texto_final),
        num_words=len(texto_final.split()),
        num_pages=sum(d.num_pages for d in documentos),
        success=True,
    )


# ============================================================
# FUNCAO PRINCIPAL: EXECUTAR ANALISE
# ============================================================

def executar_analise(
    user_id: str,
    file_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    texto: Optional[str] = None,
    area_direito: str = "Civil",
    perguntas_raw: str = "",
    titulo: str = "",
    use_pdf_safe: bool = True,
    chefe_model_key: str = "gpt-5.2",
    presidente_model_key: str = "gpt-5.2",
    callback_progresso: Optional[Callable[[str, int, str], None]] = None,
) -> PipelineResult:
    """
    Funcao principal do engine. Executa a analise completa do Tribunal.

    Fluxo:
        1. Verifica saldo no Supabase (>= 2.00 EUR)
        2. Valida API keys
        3. Carrega documento (bytes) ou usa texto direto
        4. Executa pipeline de 4 fases via TribunalProcessor
        5. Retorna PipelineResult

    Args:
        user_id: UUID do utilizador autenticado
        file_bytes: Conteudo binario do ficheiro (mutualmente exclusivo com texto)
        filename: Nome do ficheiro (obrigatorio se file_bytes fornecido)
        texto: Texto direto para analise (mutualmente exclusivo com file_bytes)
        area_direito: Area do direito (Civil, Penal, Trabalho, etc.)
        perguntas_raw: Perguntas do utilizador separadas por ---
        titulo: Titulo do projecto (opcional, gerado automaticamente se vazio)
        use_pdf_safe: Usar extracao segura pagina-a-pagina para PDFs
        chefe_model_key: Chave do modelo Chefe ("gpt-5.2" ou "gpt-5.2-pro")
        presidente_model_key: Chave do modelo Presidente ("gpt-5.2" ou "gpt-5.2-pro")
        callback_progresso: Callback(fase, progresso_percent, mensagem)

    Returns:
        PipelineResult com todos os resultados

    Raises:
        InsufficientBalanceError: Se saldo < 2.00 EUR
        InvalidDocumentError: Se documento invalido
        MissingApiKeyError: Se API key nao configurada
        EngineError: Outros erros
    """
    timestamp_inicio = datetime.now()

    # ── 1. Verificar saldo no Supabase ──
    print(f"[ENGINE] Verificando saldo para user {user_id[:8]}...")
    saldo = exigir_saldo_minimo(user_id)
    print(f"[ENGINE] Saldo OK: {saldo:.2f} EUR")

    # ── 2. Validar API keys ──
    if not OPENROUTER_API_KEY or len(OPENROUTER_API_KEY) < 10:
        raise MissingApiKeyError(
            "OPENROUTER_API_KEY nao configurada. "
            "Defina no ficheiro .env"
        )

    # ── 3. Validar inputs ──
    if file_bytes is None and texto is None:
        raise EngineError("Deve fornecer file_bytes ou texto.")

    if file_bytes is not None and texto is not None:
        raise EngineError("Forneça file_bytes OU texto, nao ambos.")

    if file_bytes is not None and not filename:
        raise EngineError("filename e obrigatorio quando file_bytes e fornecido.")

    if area_direito not in AREAS_DIREITO:
        raise EngineError(
            f"Area do direito invalida: '{area_direito}'. "
            f"Opcoes: {', '.join(AREAS_DIREITO)}"
        )

    # Validar perguntas se fornecidas
    if perguntas_raw and perguntas_raw.strip():
        perguntas = parse_perguntas(perguntas_raw)
        if perguntas:
            pode, msg = validar_perguntas(perguntas)
            if not pode:
                raise EngineError(f"Perguntas invalidas: {msg}")
            print(f"[ENGINE] {len(perguntas)} pergunta(s) detectadas")
    else:
        print("[ENGINE] Sem perguntas do utilizador")

    # ── 4. Configurar modelos premium ──
    from src.config import get_chefe_model, get_presidente_model
    import src.config as config_module

    config_module.CHEFE_MODEL = get_chefe_model(chefe_model_key)
    config_module.PRESIDENTE_MODEL = get_presidente_model(presidente_model_key)
    print(f"[ENGINE] Modelos: Chefe={chefe_model_key}, Presidente={presidente_model_key}")

    # ── 5. Carregar documento ou criar a partir de texto ──
    if file_bytes is not None:
        print(f"[ENGINE] Carregando documento: {filename}")
        temp_out_dir = OUTPUT_DIR / f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        documento = carregar_documento_de_bytes(
            file_bytes=file_bytes,
            filename=filename,
            use_pdf_safe=use_pdf_safe,
            out_dir=temp_out_dir if use_pdf_safe else None,
        )
    else:
        # Texto direto
        if len(texto.strip()) < 50:
            raise EngineError("Texto deve ter pelo menos 50 caracteres.")

        documento = DocumentContent(
            filename="texto_direto.txt",
            extension=".txt",
            text=texto,
            num_chars=len(texto),
            num_words=len(texto.split()),
            success=True,
        )
        print(f"[ENGINE] Texto direto: {len(texto):,} caracteres")

    # ── 6. Gerar titulo se nao fornecido ──
    if not titulo:
        titulo = gerar_titulo_automatico(documento.filename, area_direito)
    print(f"[ENGINE] Titulo: {titulo}")

    # ── 7. Callback de progresso (wrapper para print) ──
    def _callback_default(fase: str, progresso: int, mensagem: str):
        print(f"[{progresso:3d}%] {fase}: {mensagem}")

    callback = callback_progresso or _callback_default

    # ── 8. Executar pipeline ──
    print(f"[ENGINE] Iniciando pipeline de 4 fases...")
    print(f"[ENGINE] Area: {area_direito}")
    print(f"[ENGINE] Documento: {documento.filename} ({documento.num_chars:,} chars)")

    try:
        processor = TribunalProcessor(callback_progresso=callback)
        resultado = processor.processar(documento, area_direito, perguntas_raw, titulo)
    except ValueError as e:
        raise EngineError(f"Erro de validacao no pipeline: {e}")
    except Exception as e:
        logger.exception("Erro fatal no pipeline")
        raise EngineError(f"Erro no pipeline: {e}")

    # ── 9. Reportar resultado ──
    duracao = (datetime.now() - timestamp_inicio).total_seconds()
    print(f"[ENGINE] Pipeline concluido em {duracao:.1f}s")
    print(f"[ENGINE] Veredicto: {resultado.simbolo_final} {resultado.veredicto_final}")
    print(f"[ENGINE] Tokens: {resultado.total_tokens:,}")
    print(f"[ENGINE] Run ID: {resultado.run_id}")

    return resultado


def executar_analise_texto(
    user_id: str,
    texto: str,
    area_direito: str = "Civil",
    perguntas_raw: str = "",
    **kwargs,
) -> PipelineResult:
    """
    Atalho para executar_analise com texto direto.
    """
    return executar_analise(
        user_id=user_id,
        texto=texto,
        area_direito=area_direito,
        perguntas_raw=perguntas_raw,
        **kwargs,
    )


def executar_analise_documento(
    user_id: str,
    file_bytes: bytes,
    filename: str,
    area_direito: str = "Civil",
    perguntas_raw: str = "",
    **kwargs,
) -> PipelineResult:
    """
    Atalho para executar_analise com ficheiro binario.
    """
    return executar_analise(
        user_id=user_id,
        file_bytes=file_bytes,
        filename=filename,
        area_direito=area_direito,
        perguntas_raw=perguntas_raw,
        **kwargs,
    )


def executar_analise_multiplos_documentos(
    user_id: str,
    ficheiros: List[Dict[str, bytes]],
    area_direito: str = "Civil",
    perguntas_raw: str = "",
    titulo: str = "",
    use_pdf_safe: bool = True,
    **kwargs,
) -> PipelineResult:
    """
    Analisa multiplos documentos combinando-os antes do pipeline.

    Args:
        user_id: UUID do utilizador
        ficheiros: Lista de {"filename": str, "bytes": bytes}
        area_direito: Area do direito
        perguntas_raw: Perguntas separadas por ---
        titulo: Titulo do projecto
        use_pdf_safe: Usar PDF Seguro
        **kwargs: Argumentos adicionais para executar_analise

    Returns:
        PipelineResult
    """
    # Verificar saldo ANTES de carregar documentos
    print(f"[ENGINE] Verificando saldo para user {user_id[:8]}...")
    exigir_saldo_minimo(user_id)

    # Carregar todos os documentos
    temp_out_dir = OUTPUT_DIR / f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    documentos = carregar_multiplos_documentos(
        ficheiros=ficheiros,
        use_pdf_safe=use_pdf_safe,
        out_dir=temp_out_dir if use_pdf_safe else None,
    )

    if not documentos:
        raise InvalidDocumentError("Nenhum documento foi carregado com sucesso.")

    # Combinar documentos
    documento_combinado = combinar_documentos(documentos)

    # Executar com o documento combinado (sem re-verificar saldo)
    return executar_analise(
        user_id=user_id,
        file_bytes=documento_combinado.text.encode("utf-8"),
        filename=documento_combinado.filename,
        texto=None,
        area_direito=area_direito,
        perguntas_raw=perguntas_raw,
        titulo=titulo,
        use_pdf_safe=False,  # Ja foi processado
        **kwargs,
    )

# -*- coding: utf-8 -*-
"""
ENGINE - Motor de Analise do LexForum
============================================================
Logica PURA de processamento, sem qualquer dependencia Streamlit.
Coordena o pipeline de 4 fases (Extracao, Auditoria, Relatoria, Conselheiro-Mor).

Recebe:
  - file_bytes (bytes) ou texto (str)
  - area_direito (str)
  - perguntas_raw (str, opcional)
  - user_id (str) para verificacao de saldo no Supabase
  - tier (str) para selecao de modelos (bronze/silver/gold)

Retorna:
  - PipelineResult com todos os resultados

Sistema de Wallet (block/settle/cancel):
  - ANTES de processar: bloqueia creditos estimados
  - APOS sucesso: liquida creditos (debita real, devolve diferenca)
  - SE erro: cancela bloqueio (devolve tudo)
============================================================
"""

import os
import sys
import uuid
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

# --- Wallet imports (APENAS de wallet_manager.py) ---
from src.wallet_manager import (
    WalletManager as NewWalletManager,
    InsufficientCreditsError,
    WalletError,
)

# --- Tier imports ---
from src.tier_config import (
    TierLevel,
    calculate_tier_cost,
    get_tier_models,
    TIER_CONFIG,
)

from dataclasses import dataclass

from src.config import (
    AREAS_DIREITO,
    OPENROUTER_API_KEY,
    OUTPUT_DIR,
)
from src.pipeline.processor import LexForumProcessor, PipelineResult, FaseResult
from src.document_loader import DocumentLoader, DocumentContent
from src.llm_client import get_llm_client
from src.utils.perguntas import parse_perguntas, validar_perguntas
from src.utils.metadata_manager import gerar_titulo_automatico
from auth_service import get_supabase_admin

logger = logging.getLogger(__name__)

# ============================================================
# EXCEPCOES CUSTOMIZADAS
# ============================================================

class EngineError(Exception):
    """Erro base do engine."""
    pass

class InsufficientBalanceError(EngineError):
    """Saldo insuficiente para executar a analise."""
    def __init__(self, saldo_atual: float, saldo_necessario: float = 0.50):
        self.saldo_atual = saldo_atual
        self.saldo_minimo = saldo_necessario  # compat com main.py
        self.saldo_necessario = saldo_necessario
        super().__init__(
            f"Saldo insuficiente: ${saldo_atual:.2f} USD. "
            f"Necessario: ~${saldo_necessario:.2f} USD."
        )

class InvalidDocumentError(EngineError):
    """Documento invalido ou sem texto extraivel."""
    pass

class MissingApiKeyError(EngineError):
    """API Key nao configurada."""
    pass


# ============================================================
# WALLET MANAGER (singleton)
# ============================================================

_wallet_manager: Optional[NewWalletManager] = None

def get_wallet_manager() -> NewWalletManager:
    """Retorna WalletManager singleton (usa service_role key)."""
    global _wallet_manager
    if _wallet_manager is None:
        sb = get_supabase_admin()
        _wallet_manager = NewWalletManager(sb)
    return _wallet_manager


def _is_wallet_skip() -> bool:
    """Verifica se SKIP_WALLET_CHECK esta ativo."""
    return os.environ.get("SKIP_WALLET_CHECK", "").lower() == "true"


def verificar_saldo_wallet(user_id: str, num_chars: int = 0) -> Dict[str, Any]:
    """
    Verifica saldo do utilizador usando WalletManager.
    Se SKIP_WALLET_CHECK=true, ignora a verificação.
    """
    if _is_wallet_skip():
        print(f"[WALLET] SKIP_WALLET_CHECK ativo - ignorando verificação de saldo")
        return {"saldo_atual": 999.99, "custo_estimado": 0.0, "suficiente": True}

    wm = get_wallet_manager()
    try:
        balance = wm.get_balance(user_id, user_email="")
        return {
            "saldo_atual": balance["available"],
            "custo_estimado": 0.0,
            "suficiente": balance["available"] > 0.50,
        }
    except WalletError as e:
        logger.error(f"Erro ao verificar saldo: {e}")
        raise InsufficientBalanceError(saldo_atual=0.0, saldo_necessario=0.50)
    except Exception as e:
        logger.error(f"Erro de conexão ao verificar saldo: {e}")
        raise EngineError(f"Erro ao verificar saldo (serviço indisponível): {e}")


def bloquear_creditos(
    user_id: str,
    analysis_id: str,
    tier: TierLevel,
    document_tokens: int = 0,
) -> Dict[str, Any]:
    """
    Bloqueia creditos ANTES do processamento.

    Args:
        user_id: UUID do utilizador
        analysis_id: UUID da analise
        tier: Nivel do tier selecionado
        document_tokens: Tamanho do documento em tokens

    Returns:
        Dict com transaction_id, blocked_usd, balance_after

    Raises:
        InsufficientBalanceError: Se saldo insuficiente
    """
    if _is_wallet_skip():
        print(f"[WALLET] SKIP - bloqueio ignorado para analysis {analysis_id}")
        return {
            "transaction_id": "skip",
            "blocked_usd": 0.0,
            "balance_after": 999.99,
        }

    wm = get_wallet_manager()

    # Calcular custo estimado com base no tier
    costs = calculate_tier_cost(tier, document_tokens)
    estimated_cost = costs["custo_cliente"]  # Ja inclui margem 100%

    try:
        result = wm.block_credits(
            user_id=user_id,
            analysis_id=analysis_id,
            estimated_cost_usd=estimated_cost,
            reason=f"Analise tier={tier.value}",
        )
        print(
            f"[WALLET] Bloqueio OK: analysis={analysis_id}, "
            f"tier={tier.value}, blocked=${result['blocked_usd']:.4f}"
        )
        return result
    except InsufficientCreditsError as e:
        raise InsufficientBalanceError(
            saldo_atual=e.available,
            saldo_necessario=e.required,
        )


def liquidar_creditos(analysis_id: str, custo_real_usd: float) -> Dict[str, Any]:
    """
    Liquida creditos APOS processamento com sucesso.
    Debita custo real x2 (margem 100%), devolve diferenca.
    """
    if _is_wallet_skip():
        print(f"[WALLET] SKIP - liquidacao ignorada (custo real=${custo_real_usd:.4f})")
        return {
            "status": "skipped",
            "real_cost": custo_real_usd,
            "blocked": 0.0,
            "refunded": 0.0,
        }

    wm = get_wallet_manager()
    try:
        result = wm.settle_credits(
            analysis_id=analysis_id,
            real_cost_usd=custo_real_usd,
        )
        print(
            f"[WALLET] Liquidacao OK: analysis={analysis_id}, "
            f"real=${custo_real_usd:.4f}, refunded=${result.get('refunded', 0):.4f}"
        )
        return result
    except Exception as e:
        logger.error(f"[WALLET] ERRO ao liquidar (analise JA executada): {e}")
        return {"status": "error", "error": str(e), "real_cost": custo_real_usd}


def cancelar_bloqueio(analysis_id: str) -> None:
    """Cancela bloqueio se analise falhar. Devolve tudo."""
    if _is_wallet_skip():
        print(f"[WALLET] SKIP - cancelamento ignorado para {analysis_id}")
        return

    wm = get_wallet_manager()
    try:
        wm.cancel_block(analysis_id=analysis_id)
        print(f"[WALLET] Bloqueio cancelado: analysis={analysis_id}")
    except Exception as e:
        logger.error(f"[WALLET] ERRO ao cancelar bloqueio: {e}")


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
    tier: str = "bronze",
    use_pdf_safe: bool = True,
    callback_progresso: Optional[Callable[[str, int, str], None]] = None,
) -> PipelineResult:
    """
    Funcao principal do engine. Executa a analise completa do LexForum.

    Fluxo:
      1. Determina tier e modelos
      2. Valida API keys e inputs
      3. Carrega documento
      4. BLOQUEIA creditos (wallet)
      5. Executa pipeline de 4 fases
      6. LIQUIDA creditos (sucesso) ou CANCELA bloqueio (erro)
      7. Retorna PipelineResult

    Args:
        user_id: UUID do utilizador autenticado
        file_bytes: Conteudo binario do ficheiro
        filename: Nome do ficheiro
        texto: Texto direto para analise
        area_direito: Area do direito (Civil, Penal, Trabalho, etc.)
        perguntas_raw: Perguntas do utilizador separadas por ---
        titulo: Titulo do projecto (opcional)
        tier: Tier selecionado (bronze, silver, gold)
        use_pdf_safe: Usar extracao segura pagina-a-pagina para PDFs
        callback_progresso: Callback(fase, progresso_percent, mensagem)

    Returns:
        PipelineResult com todos os resultados

    Raises:
        InsufficientBalanceError: Se saldo insuficiente
        InvalidDocumentError: Se documento invalido
        MissingApiKeyError: Se API key nao configurada
        EngineError: Outros erros
    """
    timestamp_inicio = datetime.now()
    analysis_id = str(uuid.uuid4())

    # ── 1. Determinar tier e modelos ──
    try:
        tier_level = TierLevel(tier.lower())
    except ValueError:
        tier_level = TierLevel.BRONZE
        print(f"[ENGINE] Tier '{tier}' invalido, usando BRONZE")

    tier_models = get_tier_models(tier_level)
    print(f"[ENGINE] Tier: {tier_level.value} | Analysis ID: {analysis_id}")

    # Extrair modelos do tier
    consolidador_model_key = tier_models.get("audit_chief", "gpt-5.2")
    conselheiro_model_key = tier_models.get("president", "gpt-5.2")
    auditor_claude_model = tier_models.get("audit_claude", "sonnet-4.5")
    relator_claude_model = tier_models.get("judgment_claude", "sonnet-4.5")
    extraction_model = tier_models.get("extraction", "sonnet-4.5")

    # ── 2. Verificar saldo basico ──
    print(f"[ENGINE] Verificando saldo wallet para user {user_id[:8]}...")
    wallet_info = verificar_saldo_wallet(user_id, num_chars=0)
    print(f"[ENGINE] Saldo OK: ${wallet_info['saldo_atual']:.2f} USD")

    # ── 3. Validar API keys ──
    if not OPENROUTER_API_KEY or len(OPENROUTER_API_KEY) < 10:
        raise MissingApiKeyError(
            "OPENROUTER_API_KEY nao configurada. "
            "Defina no ficheiro .env"
        )

    # ── 4. Validar inputs ──
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

    # ── 5. Configurar modelos conforme tier ──
    from src.config import (
        get_chefe_model,
        get_presidente_model,
        get_auditor_claude_model,
        get_juiz_claude_model,
    )
    import src.config as config_module

    config_module.CHEFE_MODEL = get_chefe_model(consolidador_model_key)
    config_module.PRESIDENTE_MODEL = get_presidente_model(conselheiro_model_key)

    auditor_model = get_auditor_claude_model(auditor_claude_model)
    relator_model = get_juiz_claude_model(relator_claude_model)

    if len(config_module.AUDITOR_MODELS) > 1:
        config_module.AUDITOR_MODELS[1] = auditor_model
        config_module.AUDITORES[1]["model"] = auditor_model

    if len(config_module.JUIZ_MODELS) > 1:
        config_module.JUIZ_MODELS[1] = relator_model
        config_module.JUIZES[1]["model"] = relator_model

    # Aplicar modelo de extracção do tier ao E1 (Claude)
    from src.tier_config import get_openrouter_model
    extraction_openrouter = get_openrouter_model(extraction_model)
    if len(config_module.LLM_CONFIGS) > 0:
        config_module.LLM_CONFIGS[0]["model"] = extraction_openrouter
        config_module.EXTRATOR_MODELS[0] = extraction_openrouter
        config_module.EXTRATOR_MODELS_NEW[0] = extraction_openrouter

    print(
        f"[ENGINE] Modelos ({tier_level.value}): "
        f"E1={extraction_openrouter}, "
        f"Consolidador={config_module.CHEFE_MODEL}, Conselheiro={config_module.PRESIDENTE_MODEL}, "
        f"A2={auditor_model}, J2={relator_model}"
    )

    # ── 6. Carregar documento ou criar a partir de texto ──
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

    # ── 7. Gerar titulo se nao fornecido ──
    if not titulo:
        titulo = gerar_titulo_automatico(documento.filename, area_direito)
    print(f"[ENGINE] Titulo: {titulo}")

    # ── 8. BLOQUEAR CREDITOS (antes de processar) ──
    num_chars = documento.num_chars if documento else 0
    document_tokens = num_chars // 4  # Estimativa grosseira

    try:
        block_result = bloquear_creditos(
            user_id=user_id,
            analysis_id=analysis_id,
            tier=tier_level,
            document_tokens=document_tokens,
        )
        print(
            f"[ENGINE] Creditos bloqueados: ${block_result['blocked_usd']:.4f}, "
            f"saldo restante: ${block_result['balance_after']:.2f}"
        )
    except InsufficientBalanceError:
        raise  # Re-raise para o main.py tratar
    except Exception as e:
        logger.error(f"[ENGINE] Erro ao bloquear creditos: {e}")
        raise EngineError(f"Erro ao preparar pagamento: {e}")

    # ── 9. Callback de progresso ──
    def _callback_default(fase: str, progresso: int, mensagem: str):
        print(f"[{progresso:3d}%] {fase}: {mensagem}")

    callback = callback_progresso or _callback_default

    # ── 10. Executar pipeline ──
    print(f"[ENGINE] Iniciando pipeline de 4 fases...")
    print(f"[ENGINE] Area: {area_direito}")
    print(f"[ENGINE] Documento: {documento.filename} ({documento.num_chars:,} chars)")

    try:
        processor = LexForumProcessor(
            chefe_model=config_module.CHEFE_MODEL,
            presidente_model=config_module.PRESIDENTE_MODEL,
            callback_progresso=callback,
        )
        processor._tier = tier  # Passar tier para o performance tracker
        resultado = processor.processar(documento, area_direito, perguntas_raw, titulo)
    except ValueError as e:
        # ── ERRO: Cancelar bloqueio ──
        cancelar_bloqueio(analysis_id)
        raise EngineError(f"Erro de validacao no pipeline: {e}")
    except Exception as e:
        # ── ERRO: Cancelar bloqueio ──
        cancelar_bloqueio(analysis_id)
        logger.exception("Erro fatal no pipeline")
        raise EngineError(f"Erro no pipeline: {e}")

    # ── 11. LIQUIDAR CREDITOS (sucesso) ──
    custo_real_usd = 0.0
    if resultado.custos and resultado.custos.get("custo_total_usd"):
        custo_real_usd = float(resultado.custos["custo_total_usd"])

    wallet_settlement = None
    if custo_real_usd > 0:
        wallet_settlement = liquidar_creditos(analysis_id, custo_real_usd)
    else:
        # Custo = 0, cancelar bloqueio
        logger.warning("[ENGINE] Custo real = $0.00 — cancelando bloqueio")
        cancelar_bloqueio(analysis_id)
        wallet_settlement = {"status": "cancelled_zero_cost", "real_cost": 0.0}

    # Adicionar info da wallet ao resultado
    if resultado.custos is None:
        resultado.custos = {}
    resultado.custos["wallet"] = wallet_settlement
    resultado.custos["tier"] = tier_level.value
    resultado.custos["analysis_id"] = analysis_id

    # ── 12. Reportar resultado ──
    duracao = (datetime.now() - timestamp_inicio).total_seconds()
    print(f"[ENGINE] Pipeline concluido em {duracao:.1f}s")
    print(f"[ENGINE] Parecer: {resultado.simbolo_final} {resultado.veredicto_final}")
    print(f"[ENGINE] Tokens: {resultado.total_tokens:,}")
    if custo_real_usd > 0:
        print(f"[ENGINE] Custo APIs: ${custo_real_usd:.4f}")
    print(f"[ENGINE] Tier: {tier_level.value} | Analysis ID: {analysis_id}")
    print(f"[ENGINE] Run ID: {resultado.run_id}")

    return resultado


def executar_analise_texto(
    user_id: str,
    texto: str,
    area_direito: str = "Civil",
    perguntas_raw: str = "",
    **kwargs,
) -> PipelineResult:
    """Atalho para executar_analise com texto direto."""
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
    """Atalho para executar_analise com ficheiro binario."""
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
    tier: str = "bronze",
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
        tier: Tier selecionado (bronze, silver, gold)
        use_pdf_safe: Usar PDF Seguro
        **kwargs: Argumentos adicionais para executar_analise

    Returns:
        PipelineResult
    """
    # Verificar saldo ANTES de carregar documentos
    print(f"[ENGINE] Verificando saldo wallet para user {user_id[:8]}...")
    verificar_saldo_wallet(user_id, num_chars=0)

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

    # Executar com o documento combinado
    return executar_analise(
        user_id=user_id,
        file_bytes=documento_combinado.text.encode("utf-8"),
        filename=documento_combinado.filename,
        texto=None,
        area_direito=area_direito,
        perguntas_raw=perguntas_raw,
        titulo=titulo,
        tier=tier,
        use_pdf_safe=False,  # Ja foi processado
        **kwargs,
    )

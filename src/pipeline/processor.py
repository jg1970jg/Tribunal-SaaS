# -*- coding: utf-8 -*-
"""
Processador Principal da Câmara de Análise - Pipeline de 3 Fases com LLMs + Q&A.

Fase 1: 7 Extratores LLM -> Agregador (SEM perguntas)
Fase 2: 4 Auditores LLM -> Consolidador (SEM perguntas)
Fase 3: 3 Relatores LLM -> Parecer + Q&A (COM perguntas)
Fase 4: Conselheiro-Mor -> Parecer + Q&A Consolidado (COM perguntas)
"""

import sys
from pathlib import Path

# Adicionar diretório raiz ao path (necessário para imports absolutos)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
import uuid

import base64

from src.cost_controller import CostController, BudgetExceededError
from src.wallet_manager import InsufficientCreditsError
from src.pipeline.zone_processor import should_use_zones, create_zone_plan, log_zone_plan  # NOVO: C5 zonas
from src.config import (
    EXTRATOR_MODELS,
    AUDITOR_MODELS,
    AUDITOR_SUBSTITUTES,
    JUDGE_SUBSTITUTES,
    CONSOLIDADOR_SUBSTITUTES,
    PRESIDENTE_SUBSTITUTES,
    JUIZ_MODELS,
    PRESIDENTE_MODEL,
    AGREGADOR_MODEL,
    CHEFE_MODEL,
    OUTPUT_DIR,
    HISTORICO_DIR,
    LOG_LEVEL,
    SIMBOLOS_VERIFICACAO,
    AREAS_DIREITO,
    LLM_CONFIGS,
    CHUNK_SIZE_CHARS,
    CHUNK_OVERLAP_CHARS,
    USE_UNIFIED_PROVENANCE,
    COVERAGE_MIN_THRESHOLD,
    VISION_CAPABLE_MODELS,
    # MetaIntegrity config
    USE_META_INTEGRITY,
    ALWAYS_GENERATE_META_REPORT,
    META_INTEGRITY_TIMESTAMP_TOLERANCE,
    META_INTEGRITY_PAGES_TOLERANCE_PERCENT,
    META_INTEGRITY_CITATION_COUNT_TOLERANCE,
    # Confidence Policy config
    CONFIDENCE_MAX_PENALTY,
    CONFIDENCE_SEVERE_CEILING,
    APPLY_CONFIDENCE_POLICY,
)
from src.pipeline.schema_unified import (
    Chunk,
    SourceSpan,
    EvidenceItem,
    ItemType,
    ExtractionMethod,
    ExtractionRun,
    ExtractionStatus,
    Coverage,
    CharRange,
    DocumentMeta,
    UnifiedExtractionResult,
)
from src.pipeline.extractor_unified import (
    SYSTEM_EXTRATOR_UNIFIED,
    build_unified_prompt,
    parse_unified_output,
    aggregate_with_provenance,
    validate_and_filter_extractors,
    calculate_coverage,
    items_to_markdown,
    render_agregado_markdown_from_json,
)
from src.pipeline.page_mapper import CharToPageMapper
from src.pipeline.schema_audit import (
    Citation,
    AuditFinding,
    AuditReport,
    CoverageCheck,
    JudgePoint,
    JudgeOpinion,
    Disagreement,
    FinalDecision,
    ConflictResolution,
    FindingType,
    Severity,
    DecisionType,
    parse_json_safe,
    parse_audit_report,
    parse_judge_opinion,
    parse_final_decision,
    # Chefe consolidado
    ChefeConsolidatedReport,
    ConsolidatedFinding,
    Divergence,
    parse_chefe_report,
)
from src.pipeline.integrity import (
    IntegrityValidator,
    IntegrityReport,
    validate_citation,
    validate_audit_report,
    validate_judge_opinion,
    validate_final_decision,
)
from src.pipeline.meta_integrity import (
    MetaIntegrityValidator,
    MetaIntegrityReport,
    MetaIntegrityConfig,
    validate_run_meta_integrity,
)
from src.pipeline.confidence_policy import (
    ConfidencePolicyCalculator,
    compute_penalty,
    apply_penalty_to_confidence,
)
from src.llm_client import get_llm_client
from src.document_loader import DocumentContent
from src.legal_verifier import VerificacaoLegal, get_legal_verifier
from prompts_maximos import (
    PROMPT_EXTRATOR_TEXTO,
    PROMPT_EXTRATOR_VISUAL,
    PROMPT_AGREGADOR_DEDUP,
    PROMPT_AUDITOR,
    PROMPT_ADVOGADO_DIABO,
    PROMPT_AUDITOR_SENIOR,
    PROMPT_JUIZ,
    PROMPT_CONSELHEIRO_MOR,
    PROMPT_CURADOR_SENIOR,
)
from src.utils.perguntas import parse_perguntas, validar_perguntas
from src.utils.metadata_manager import guardar_metadata, gerar_titulo_automatico

logger = logging.getLogger(__name__)


@dataclass
class FaseResult:
    """Resultado de uma fase do pipeline."""
    fase: str
    modelo: str
    role: str  # extrator_1, auditor_2, relator_3, etc.
    conteudo: str
    tokens_usados: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latencia_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    sucesso: bool = True
    erro: Optional[str] = None

    def __post_init__(self):
        if self.conteudo is None:
            self.conteudo = ""

    def to_dict(self) -> Dict:
        return {
            "fase": self.fase,
            "modelo": self.modelo,
            "role": self.role,
            "conteudo": self.conteudo[:2000] + "..." if len(self.conteudo) > 2000 else self.conteudo,
            "conteudo_completo_length": len(self.conteudo),
            "tokens_usados": self.tokens_usados,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latencia_ms": self.latencia_ms,
            "timestamp": self.timestamp.isoformat(),
            "sucesso": self.sucesso,
            "erro": self.erro,
        }


@dataclass
class PipelineResult:
    """Resultado completo do pipeline."""
    run_id: str
    documento: Optional[DocumentContent]
    area_direito: str
    fase1_extracoes: List[FaseResult] = field(default_factory=list)
    fase1_agregado: str = ""  # Backwards compat - alias para consolidado
    fase1_agregado_bruto: str = ""  # Concatenação simples com marcadores
    fase1_agregado_consolidado: str = ""  # Processado pelo Agregador LLM (LOSSLESS)
    fase2_auditorias: List[FaseResult] = field(default_factory=list)
    fase2_chefe: str = ""  # Backwards compat - alias para consolidado
    fase2_auditorias_brutas: str = ""  # Concatenação simples com marcadores
    fase2_chefe_consolidado: str = ""  # Processado pelo Chefe LLM (LOSSLESS)
    fase3_pareceres: List[FaseResult] = field(default_factory=list)
    fase3_presidente: str = ""
    verificacoes_legais: List[VerificacaoLegal] = field(default_factory=list)
    veredicto_final: str = ""
    simbolo_final: str = ""
    status_final: str = ""
    # Q&A
    perguntas_utilizador: List[str] = field(default_factory=list)
    respostas_juizes_qa: List[Dict] = field(default_factory=list)
    respostas_finais_qa: str = ""
    # Timestamps e estatísticas
    timestamp_inicio: datetime = field(default_factory=datetime.now)
    timestamp_fim: Optional[datetime] = None
    total_tokens: int = 0
    total_latencia_ms: float = 0.0
    sucesso: bool = True
    erro: Optional[str] = None
    # Custos REAIS
    custos: Optional[Dict] = None
    # FIX 2026-02-14: Sumário estruturado por IA (para frontend)
    resumo_por_ia: Optional[Dict] = None
    # v4.0: Fase 0 Triagem
    fase0_triage: Optional[Dict] = None

    def to_dict(self) -> Dict:
        return {
            "run_id": self.run_id,
            "documento": self.documento.to_dict() if self.documento else None,
            "area_direito": self.area_direito,
            "fase1_extracoes": [f.to_dict() for f in self.fase1_extracoes],
            "fase1_agregado": self.fase1_agregado,
            "fase1_agregado_bruto": self.fase1_agregado_bruto,
            "fase1_agregado_consolidado": self.fase1_agregado_consolidado,
            "fase2_auditorias": [f.to_dict() for f in self.fase2_auditorias],
            "fase2_chefe": self.fase2_chefe,
            "fase2_auditorias_brutas": self.fase2_auditorias_brutas,
            "fase2_chefe_consolidado": self.fase2_chefe_consolidado,
            "fase3_pareceres": [f.to_dict() for f in self.fase3_pareceres],
            "fase3_presidente": self.fase3_presidente,
            "verificacoes_legais": [v.to_dict() for v in self.verificacoes_legais],
            "veredicto_final": self.veredicto_final,
            "simbolo_final": self.simbolo_final,
            "status_final": self.status_final,
            "perguntas_utilizador": self.perguntas_utilizador,
            "respostas_juizes_qa": self.respostas_juizes_qa,
            "respostas_finais_qa": self.respostas_finais_qa,
            "timestamp_inicio": self.timestamp_inicio.isoformat(),
            "timestamp_fim": self.timestamp_fim.isoformat() if self.timestamp_fim else None,
            "total_tokens": self.total_tokens,
            "total_latencia_ms": self.total_latencia_ms,
            "duracao_total_s": round(
                (self.timestamp_fim - self.timestamp_inicio).total_seconds(), 1
            ) if self.timestamp_fim else 0.0,
            "sucesso": self.sucesso,
            "erro": self.erro,
            # Custos REAIS
            "custos": self.custos,
            # FIX 2026-02-14: Sumário estruturado por IA
            "resumo_por_ia": self.resumo_por_ia,
            # v4.0: Fase 0 Triagem
            "fase0_triage": self.fase0_triage,
            # Metadados do documento
            "documento_texto": self.documento.text if self.documento and self.documento.text else "",
            "documento_filename": self.documento.filename if self.documento else "",
            "documento_chars": self.documento.num_chars if self.documento else 0,
            "documento_palavras": self.documento.num_words if self.documento else 0,
            "documento_paginas": getattr(self.documento, 'num_pages', None) if self.documento else None,
        }


def _call_with_retry(func, func_name="LLM", max_retries=3, backoff_times=None, deadline=None):
    """Chama func() com até max_retries tentativas. Backoff: 3s, 8s, 15s.

    Args:
        deadline: tempo máximo total em segundos (None = sem limite).
                  Se excedido, retorna None imediatamente.
    """
    if backoff_times is None:
        backoff_times = [3, 8, 15]

    _start = time.monotonic()
    for attempt in range(max_retries):
        if deadline and (time.monotonic() - _start) >= deadline:
            logger.warning(
                f"[RETRY] {func_name}: deadline {deadline}s excedido "
                f"após {attempt} tentativas ({time.monotonic() - _start:.0f}s)"
            )
            return None
        try:
            return func()
        except Exception as e:
            elapsed = time.monotonic() - _start
            if deadline and elapsed >= deadline:
                logger.warning(
                    f"[RETRY] {func_name}: deadline {deadline}s excedido "
                    f"na tentativa {attempt+1}: {e}"
                )
                return None
            if attempt < max_retries - 1:
                wait = backoff_times[min(attempt, len(backoff_times) - 1)]
                logger.warning(
                    f"[RETRY] {func_name} tentativa {attempt+1}/{max_retries} falhou: {e}. "
                    f"Retry em {wait}s..."
                )
                time.sleep(wait)
            else:
                logger.error(f"[RETRY] {func_name} FALHOU após {max_retries} tentativas: {e}")
                return None


class LexForumProcessor:
    """
    Processador principal da Câmara de Análise com pipeline de 3 fases + Q&A.

    Fase 1 - EXTRAÇÃO (perguntas_count=0):
        3 LLMs extraem informação do documento
        Agregador concatena e marca origem

    Fase 2 - AUDITORIA (perguntas_count=0):
        3 LLMs auditam a extração
        Consolidador concatena e consolida

    Fase 3 - RELATORIA (perguntas_count=N):
        3 LLMs emitem parecer jurídico + respondem Q&A

    Fase 4 - CONSELHEIRO-MOR (perguntas_count=N):
        Conselheiro-Mor verifica e emite parecer (✓/✗/⚠)
        Consolida respostas Q&A
    """

    # Prompts do sistema — v4.0 Handover (importados de prompts_maximos.py)
    SYSTEM_EXTRATOR = PROMPT_EXTRATOR_TEXTO
    SYSTEM_EXTRATOR_VISUAL = PROMPT_EXTRATOR_VISUAL

    SYSTEM_AUDITOR = PROMPT_AUDITOR
    SYSTEM_ADVOGADO_DIABO = PROMPT_ADVOGADO_DIABO
    SYSTEM_AUDITOR_SENIOR = PROMPT_AUDITOR_SENIOR

    SYSTEM_RELATOR = PROMPT_JUIZ

    SYSTEM_RELATOR_QA = PROMPT_JUIZ + """

IMPORTANTE: Após o parecer, responde às PERGUNTAS DO UTILIZADOR de forma clara e numerada."""

    SYSTEM_CONSELHEIRO = PROMPT_CONSELHEIRO_MOR

    SYSTEM_CONSELHEIRO_QA = PROMPT_CONSELHEIRO_MOR + """

IMPORTANTE: Após o parecer, consolida as RESPOSTAS Q&A eliminando contradições e fornecendo respostas finais claras e numeradas."""

    @staticmethod
    def _build_system_agregador(num_extractors: int = 7) -> str:
        """Gera system prompt do agregador — v4.0 Handover com deduplicação semântica."""
        return PROMPT_AGREGADOR_DEDUP.replace(
            "7 different AIs",
            f"{num_extractors} different AIs"
        )
    SYSTEM_CONSOLIDADOR = """És o CONSOLIDADOR da Fase 2. Recebes 4 auditorias da mesma extração feitas por modelos diferentes.

TAREFA CRÍTICA - CONSOLIDAÇÃO LOSSLESS:
- NUNCA percas críticas únicas
- Se um auditor identificou um problema que outros não viram, MANTÉM essa crítica
- Remove apenas críticas EXATAS duplicadas

FORMATO OBRIGATÓRIO:

## 1. AVALIAÇÃO DA COMPLETUDE
- [A1,A2,A3,A4] Observação consensual X
- [A1] Observação Y (único - OBRIGATÓRIO manter)

## 2. INCONSISTÊNCIAS IDENTIFICADAS
### Críticas (por gravidade)
- [A1,A2,A3,A4] ⚠️ CRÍTICO: Descrição (consenso total)
- [A2,A3,A4] ⚠️ IMPORTANTE: Descrição (parcial)
- [A1] ⚠️ ATENÇÃO: Descrição (único - verificar)

## 3. INFORMAÇÃO EM FALTA
- [A1,A2,A3,A4] Falta: Descrição
- [A2] Falta: Descrição (único)

## 4. RELEVÂNCIA JURÍDICA
- [A1,A2,A3,A4] Análise da relevância
- [A1,A3] Ponto adicional

## 5. LEGISLAÇÃO PORTUGUESA APLICÁVEL
- [A1,A2,A3,A4] Artigo Xº do Diploma Y - Justificação
- [A1] Artigo Zº (sugestão única - verificar aplicabilidade)

## 6. RECOMENDAÇÕES PARA FASE 3
- [A1,A2,A3,A4] Recomendação prioritária
- [A2] Recomendação adicional (único)

## 7. DIVERGÊNCIAS ENTRE AUDITORES
(Se A1 critica X e A2 discorda, listar aqui)
- Tema: [descrição]
  - A1: [posição do A1]
  - A2: [posição do A2]
  - A3: [posição do A3 ou "não mencionou"]
  - A4: [posição do A4 ou "não mencionou"]

## 8. CONTROLO DE COBERTURA (OBRIGATÓRIO)

REGRAS NÃO-NEGOCIÁVEIS:
1) Tens de preencher as 4 subsecções abaixo: [A1], [A2], [A3] e [A4]
2) Se um auditor NÃO tiver críticas exclusivas, escreve LITERALMENTE:
   "(nenhuma — todas as críticas foram partilhadas)"
3) A "Confirmação" SÓ pode ser "SIM" se as 4 subsecções estiverem preenchidas (com críticas ou com "(nenhuma — ...)")
4) Se "Confirmação" for "NÃO", OBRIGATORIAMENTE lista cada crítica omitida em "ITENS NÃO INCORPORADOS" com razão concreta
5) Quando Confirmação=SIM, para cada item exclusivo listado, indica onde foi incorporado:
   - [A1] crítica X → incorporado em: ##2/Inconsistências, linha 3
   - [A2] observação Y → incorporado em: ##1/Completude, linha 1

FORMATO OBRIGATÓRIO PARA CRÍTICAS:
- crítica curta e objetiva (máx 100 caracteres)
- NÃO usar "detalhes adicionais" ou textos vagos

**[A1] encontrou exclusivamente:**
- crítica A → incorporado em: [secção/linha]
- observação B → incorporado em: [secção/linha]
(ou: "(nenhuma — todas as críticas foram partilhadas)")

**[A2] encontrou exclusivamente:**
- crítica C → incorporado em: [secção/linha]
(ou: "(nenhuma — todas as críticas foram partilhadas)")

**[A3] encontrou exclusivamente:**
- crítica D → incorporado em: [secção/linha]
(ou: "(nenhuma — todas as críticas foram partilhadas)")

**[A4] encontrou exclusivamente:**
- crítica E → incorporado em: [secção/linha]
(ou: "(nenhuma — todas as críticas foram partilhadas)")

**Confirmação:** SIM
(ou: **Confirmação:** NÃO)
Escreve exatamente "Confirmação: SIM" ou "Confirmação: NÃO" - escolhe apenas um.

**ITENS NÃO INCORPORADOS** (obrigatório se Confirmação=NÃO):
- [AX] crítica: motivo concreto da não incorporação
(ou: "(nenhum)" se Confirmação=SIM)

---
LEGENDA:
- [A1,A2,A3,A4] = Consenso total (4 auditores)
- [A1,A2,A3] / [A2,A3,A4] = Consenso forte (3 auditores)
- [A1,A2] / [A2,A3] / [A3,A4] = Consenso parcial (2 auditores)
- [A1] / [A2] / [A3] / [A4] = Único (1 auditor - NUNCA ELIMINAR sem justificação)

PRIORIDADE: Validade legal > Inconsistências críticas > Completude > Sugestões

REGRA NÃO-NEGOCIÁVEL: Na dúvida, MANTÉM. Melhor redundância que perda de críticas."""

    SYSTEM_CONSOLIDADOR_JSON = """IMPORTANT: You MUST respond with ONLY valid JSON. No text before or after the JSON. No markdown code blocks. Just the raw JSON object starting with { and ending with }.

És o CONSOLIDADOR da Fase 2. Recebes auditorias da mesma extração feitas por múltiplos modelos.
Deves consolidar todas as auditorias num ÚNICO relatório JSON estruturado.

DADOS DE ENTRADA:
Cada auditor fornece findings com evidence_item_ids que referenciam items da Fase 1.
Preserva SEMPRE estes evidence_item_ids na consolidação.

DEVES devolver APENAS um JSON válido com a seguinte estrutura:
{
  "chefe_id": "CHEFE",
  "consolidated_findings": [
    {
      "finding_id": "finding_consolidated_001",
      "claim": "Afirmação consolidada (obrigatório)",
      "finding_type": "facto|inferencia|hipotese",
      "severity": "critico|alto|medio|baixo",
      "sources": ["A1", "A2", "A3", "A4"],
      "evidence_item_ids": ["item_001", "item_002"],
      "citations": [
        {
          "doc_id": "id do documento",
          "start_char": 1234,
          "end_char": 1300,
          "page_num": 5,
          "excerpt": "trecho citado (max 200 chars)",
          "source_auditor": "A1"
        }
      ],
      "consensus_level": "total|forte|parcial|unico",
      "notes": "observações"
    }
  ],
  "divergences": [
    {
      "topic": "tema da divergência",
      "positions": [
        {"auditor_id": "A1", "position": "posição do A1"},
        {"auditor_id": "A2", "position": "posição do A2"}
      ],
      "resolution": "como foi resolvido (se aplicável)",
      "unresolved": true
    }
  ],
  "coverage_check": {
    "auditors_seen": ["A1", "A2", "A3", "A4"],
    "docs_seen": ["doc_xxx"],
    "pages_seen": [1, 2, 3, 4, 5],
    "coverage_percent": 95.0,
    "unique_findings_by_auditor": {
      "A1": 2,
      "A2": 1,
      "A3": 0,
      "A4": 3
    }
  },
  "recommendations_phase3": [
    {
      "priority": "alta|media|baixa",
      "recommendation": "descrição",
      "sources": ["A1", "A2"]
    }
  ],
  "legal_refs_consolidated": [
    {
      "ref": "Art. 1022º CC",
      "sources": ["A1", "A2", "A3"],
      "applicability": "alta|media|baixa",
      "notes": ""
    }
  ],
  "open_questions": ["pergunta 1", "pergunta 2"],
  "errors": [],
  "warnings": []
}

REGRAS CRÍTICAS:
1. OBRIGATÓRIO: Unir evidence_item_ids de todos os auditores (sem duplicados)
2. Consolidar findings de TODOS os auditores preservando proveniência
3. consensus_level: "total" (todos concordam), "forte" (3+), "parcial" (2), "unico" (1)
4. NUNCA perder findings únicos - marcar como consensus_level: "unico"
5. Manter TODAS as citations originais com source_auditor
6. Divergências reais entre auditores devem ir para "divergences"
7. Se parsing falhar em algum auditor, registar em "errors" mas continuar
8. CRÍTICO - EXCERPT: Ao consolidar citations, manter SEMPRE o excerpt ORIGINAL do auditor. NÃO reescrever excerpts. Se o excerpt original estiver vazio, deixar vazio."""

    # =========================================================================
    # PROMPTS JSON PARA FASES 2-4 (PROVENIÊNCIA ESTRUTURADA)
    # =========================================================================

    SYSTEM_AUDITOR_JSON = """IMPORTANT: You MUST respond with ONLY valid JSON. No text before or after the JSON. No markdown code blocks. Just the raw JSON object starting with { and ending with }.

""" + PROMPT_AUDITOR + """

DADOS DE ENTRADA:
Recebes items extraídos na Fase 1 em formato JSON estruturado. Cada item tem:
- item_id: identificador único (ex: "item_001")
- item_type: tipo do item (date, monetary, entity, etc.)
- value: valor normalizado
- page, start_char, end_char: localização exacta no documento

DEVES devolver APENAS um JSON válido com a seguinte estrutura:
{
  "findings": [
    {
      "finding_id": "F001",
      "claim": "Afirmação sobre o documento (obrigatório)",
      "finding_type": "facto|inferencia|hipotese",
      "severity": "critico|alto|medio|baixo",
      "citations": [
        {
          "doc_id": "id do documento",
          "start_char": 1234,
          "end_char": 1300,
          "page_num": 5,
          "excerpt": "trecho citado (max 200 chars)"
        }
      ],
      "evidence_item_ids": ["item_001", "item_002"],
      "notes": "observações adicionais"
    }
  ],
  "coverage_check": {
    "docs_seen": ["doc_xxx"],
    "pages_seen": [1, 2, 3],
    "coverage_percent": 95.0,
    "notes": "observações sobre cobertura"
  },
  "open_questions": ["pergunta 1", "pergunta 2"]
}

REGRAS CRÍTICAS:
1. OBRIGATÓRIO: evidence_item_ids DEVE conter os item_id exactos do JSON de entrada
2. OBRIGATÓRIO: Copia start_char/end_char/page_num dos items para as citations
3. Cada finding DEVE ter pelo menos 1 citation com offsets do item referenciado
4. severity: critico (bloqueia decisão), alto (afeta significativamente), medio (relevante), baixo (informativo)
5. finding_type: facto (verificável no documento), inferencia (dedução lógica), hipotese (requer verificação)
6. Se um finding se baseia em múltiplos items, lista TODOS os item_ids relevantes
7. Sê crítico e rigoroso - verifica se os items extraídos são precisos e completos
8. CRÍTICO - EXCERPT: O campo "excerpt" nas citations DEVE ser uma cópia LITERAL e EXACTA do texto do documento entre start_char e end_char. NÃO parafrasear, NÃO resumir, NÃO reformular. Copiar carácter por carácter do documento original.
9. Se não conseguires determinar o texto exacto para o excerpt, coloca uma string vazia ("") - NUNCA inventes ou reescrevas o texto."""

    SYSTEM_RELATOR_JSON = """IMPORTANT: You MUST respond with ONLY valid JSON. No text before or after the JSON. No markdown code blocks. Just the raw JSON object starting with { and ending with }.

""" + PROMPT_JUIZ + """

DEVES devolver APENAS um JSON válido com a seguinte estrutura:
{
  "recommendation": "procedente|improcedente|parcialmente_procedente|inconclusivo",
  "decision_points": [
    {
      "point_id": "",
      "conclusion": "Conclusão jurídica (obrigatório)",
      "rationale": "Fundamentação legal (obrigatório)",
      "citations": [
        {
          "doc_id": "id do documento",
          "start_char": 1234,
          "end_char": 1300,
          "page_num": 5,
          "excerpt": "trecho citado"
        }
      ],
      "legal_basis": ["Art. 1022º CC", "DL n.º 6/2006"],
      "risks": ["risco 1"],
      "confidence": 0.85,
      "finding_refs": ["finding_xxx"],
      "is_determinant": true
    }
  ],
  "disagreements": [
    {
      "disagreement_id": "",
      "target_id": "finding_xxx ou point_xxx",
      "target_type": "finding|point",
      "reason": "razão do desacordo",
      "alternative_view": "visão alternativa"
    }
  ],
  "qa_responses": []
}

REGRAS:
1. Cada decision_point DEVE ter citations com offsets
2. Cita sempre artigos específicos da legislação portuguesa em legal_basis
3. confidence: 0.0 a 1.0 indicando certeza na conclusão
4. recommendation: procedente, improcedente, parcialmente_procedente, ou inconclusivo
5. is_determinant: true se o ponto é CRUCIAL para a decisão (ex: prova de facto essencial)
   - IMPORTANTE: pontos determinantes SEM citations serão marcados como SEM_PROVA
6. CRÍTICO - EXCERPT: O campo "excerpt" nas citations DEVE ser uma cópia LITERAL e EXACTA do texto do documento. NÃO parafrasear, NÃO resumir, NÃO reformular. Copiar carácter por carácter.
7. Se não conseguires determinar o texto exacto, coloca uma string vazia ("") no excerpt.

OBRIGATÓRIO - CITATIONS:
- CADA decision_point DEVE ter pelo menos 1 entrada no array "citations". Respostas com citations vazias serão REJEITADAS automaticamente.
- Se não tiveres offsets exactos (start_char/end_char), preenche APENAS page_num e excerpt.
- Formato mínimo aceitável: {"page_num": 5, "excerpt": "texto do documento"}
- NUNCA deixes o array citations vazio: "citations": [] é PROIBIDO."""

    SYSTEM_RELATOR_JSON_QA = """IMPORTANT: You MUST respond with ONLY valid JSON. No text before or after the JSON. No markdown code blocks. Just the raw JSON object starting with { and ending with }.

És um relator especializado em Direito Português.
Com base na análise e auditoria fornecidas, emite um parecer jurídico em formato JSON.

DEVES devolver APENAS um JSON válido com a seguinte estrutura:
{
  "recommendation": "procedente|improcedente|parcialmente_procedente|inconclusivo",
  "decision_points": [...],
  "disagreements": [...],
  "qa_responses": [
    {
      "question": "pergunta original",
      "answer": "resposta fundamentada",
      "citations": [...]
    }
  ]
}

IMPORTANTE: O campo qa_responses DEVE conter respostas a todas as perguntas do utilizador.
Cita sempre artigos específicos da legislação portuguesa."""

    SYSTEM_CONSELHEIRO_JSON = """IMPORTANT: You MUST respond with ONLY valid JSON. No text before or after the JSON. No markdown code blocks. Just the raw JSON object starting with { and ending with }.

""" + PROMPT_CONSELHEIRO_MOR + """

DEVES devolver APENAS um JSON válido com a seguinte estrutura:
{
  "final_answer": "Resposta final completa em texto",
  "decision_type": "procedente|improcedente|parcialmente_procedente|inconclusivo",
  "confidence": 0.9,
  "decision_points_final": [
    {
      "point_id": "",
      "conclusion": "Conclusão consolidada",
      "rationale": "Fundamentação",
      "citations": [...],
      "legal_basis": ["Art. Xº"],
      "confidence": 0.9
    }
  ],
  "proofs": [
    {
      "doc_id": "id",
      "start_char": 100,
      "end_char": 200,
      "page_num": 3,
      "excerpt": "prova citada"
    }
  ],
  "conflicts_resolved": [
    {
      "conflict_id": "",
      "conflicting_ids": ["point_1", "point_2"],
      "resolution": "como foi resolvido",
      "chosen_value": "valor escolhido",
      "reasoning": "razão da escolha"
    }
  ],
  "conflicts_unresolved": [],
  "unreadable_parts": [],
  "qa_final": []
}

REGRAS:
1. decision_type: procedente, improcedente, parcialmente_procedente, ou inconclusivo
2. confidence: 0.0 a 1.0
3. Cada prova em proofs DEVE ter start_char/end_char
4. Resolve conflitos entre relatores em conflicts_resolved
5. CRÍTICO - EXCERPT: O campo "excerpt" em proofs e citations DEVE ser uma cópia LITERAL e EXACTA do texto do documento. NÃO parafrasear. Copiar carácter por carácter."""

    SYSTEM_CONSELHEIRO_JSON_QA = """IMPORTANT: You MUST respond with ONLY valid JSON. No text before or after the JSON. No markdown code blocks. Just the raw JSON object starting with { and ending with }.

És o Conselheiro-Mor da Câmara de Análise, responsável pela verificação final.
Analisa os pareceres e consolida as respostas Q&A em formato JSON.

DEVES devolver APENAS um JSON válido com:
{
  "final_answer": "Parecer final",
  "decision_type": "procedente|improcedente|parcialmente_procedente|inconclusivo",
  "confidence": 0.9,
  "decision_points_final": [...],
  "proofs": [...],
  "conflicts_resolved": [...],
  "qa_final": [
    {
      "question": "pergunta original",
      "final_answer": "resposta consolidada dos 3 relatores",
      "sources": ["J1", "J2", "J3"]
    }
  ]
}

IMPORTANTE: qa_final DEVE consolidar as respostas dos 3 relatores, eliminando contradições."""

    PROMPT_RLM = """Revê mantendo TODOS factos.
PODES: Remover repetições IDÊNTICAS
NÃO PODES: Remover factos únicos
TEXTO: {texto}
REVISTO:"""

    def _aplicar_rlm(self, texto: str, tipo_fase: str) -> str:
        if tipo_fase not in ["auditoria", "relatoria"]:
            return texto
        tokens = len(texto) // 4
        if tokens < 25000:
            logger.info(f"[RLM] {tipo_fase}: {tokens:,} < 25k → SKIP")
            return texto
        logger.info(f"[RLM] Comprimindo {tokens:,} tokens...")
        try:
            resultado = self.llm_client.chat_simple(
                model="openai/gpt-4.1",
                prompt=self.PROMPT_RLM.format(texto=texto),
                max_tokens=tokens,
                temperature=0.0,
                enable_cache=False,
            )
            tokens_depois = len(resultado.content) // 4 if resultado else tokens
            logger.info(f"[RLM] {tokens:,} → {tokens_depois:,}")
            # FIX 2026-02-14: Registar custo do RLM (antes era invisível)
            if hasattr(self, '_cost_controller') and self._cost_controller and resultado:
                try:
                    self._cost_controller.register_usage(
                        phase=f"rlm_{tipo_fase}",
                        model="openai/gpt-4.1",
                        prompt_tokens=resultado.prompt_tokens or (tokens),
                        completion_tokens=resultado.completion_tokens or (tokens_depois),
                        raise_on_exceed=True,
                    )
                except Exception as e:
                    if "Limit" in type(e).__name__ or "Budget" in type(e).__name__:
                        logger.error(f"[CUSTO-BLOQUEIO] Limite excedido em rlm_{tipo_fase}: {e}")
                        raise
                    logger.warning(f"[RLM] Erro ao registar custo rlm_{tipo_fase}: {e}")
            if self._output_dir:
                self._log_to_file(f"rlm_{tipo_fase}.md", resultado.content if resultado else "ERRO")
            return resultado.content if resultado else texto
        except Exception as e:
            logger.error(f"[RLM] ERRO: {e}")
            return texto

    def __init__(
        self,
        extrator_models: List[str] = None,
        auditor_models: List[str] = None,
        relator_models: List[str] = None,
        presidente_model: str = None,
        agregador_model: str = None,
        chefe_model: str = None,
        callback_progresso: Optional[Callable] = None,
    ):
        # v4.0 FIX: deepcopy de TODAS as listas de modelos para thread-safety
        import copy
        self.extrator_models = list(extrator_models) if extrator_models else list(EXTRATOR_MODELS)
        self.auditor_models = list(auditor_models) if auditor_models else list(AUDITOR_MODELS)
        self.relator_models = list(relator_models) if relator_models else list(JUIZ_MODELS)
        self.presidente_model = presidente_model or PRESIDENTE_MODEL
        self.agregador_model = agregador_model or AGREGADOR_MODEL
        self.chefe_model = chefe_model or CHEFE_MODEL
        self.callback_progresso = callback_progresso

        # FIX 2026-02-14: Cópia local de LLM_CONFIGS para thread-safety
        # v5.1: Filtrar extractores com base em extrator_models (Bronze skip E4)
        self._llm_configs = copy.deepcopy(LLM_CONFIGS)
        if extrator_models:
            allowed_models = set(extrator_models)
            self._llm_configs = [
                cfg for cfg in self._llm_configs
                if not cfg["id"].startswith("E") or cfg["model"] in allowed_models
            ]

        self.llm_client = get_llm_client()
        self.legal_verifier = get_legal_verifier()

        self._run_id: Optional[str] = None
        self._output_dir: Optional[Path] = None
        self._titulo: str = ""
        self._integrity_validator: Optional[IntegrityValidator] = None
        self._document_text: str = ""
        self._page_mapper: Optional[CharToPageMapper] = None
        self._unified_result: Optional[UnifiedExtractionResult] = None

    def _reportar_progresso(self, fase: str, progresso: int, mensagem: str):
        """Reporta progresso ao callback."""
        try:
            logger.info(f"[{progresso}%] {fase}: {mensagem}")
            if self.callback_progresso:
                self.callback_progresso(fase, progresso, mensagem)
        except Exception as e:
            logger.debug(f"Erro ao reportar progresso: {e}")

    def _setup_run(self) -> str:
        """Configura uma nova execução."""
        self._run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self._output_dir = OUTPUT_DIR / self._run_id
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Run iniciado: {self._run_id}")
        return self._run_id

    def _log_to_file(self, filename: str, content: str):
        """Guarda conteúdo num ficheiro de log."""
        if self._output_dir:
            filepath = self._output_dir / filename
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

    def _call_llm(
        self,
        model: str,
        prompt: str,
        system_prompt: str,
        role_name: str,
        temperature: float = 0.0,
        max_tokens: int = None,
    ) -> FaseResult:
        """
        Chama um LLM e retorna o resultado formatado com tokens REAIS.

        NOVO: Failover automatico, max_tokens dinamico, adaptive hints,
        quality gates com 2 retries, e performance tracking.
        """
        from src.config import calcular_max_tokens, selecionar_modelo_com_failover
        from src.performance_tracker import (
            check_response_quality, build_retry_prompt, classify_error,
        )

        # Failover automatico: se documento grande, trocar modelo
        doc_chars = len(self._document_text) if hasattr(self, '_document_text') and self._document_text else 0
        modelo_final = selecionar_modelo_com_failover(model, doc_chars, role_name)

        # max_tokens dinamico se nao especificado
        if max_tokens is None:
            max_tokens = calcular_max_tokens(doc_chars, modelo_final, role_name)

        if modelo_final != model:
            logger.info(
                f"[FAILOVER] {role_name}: {model} -> {modelo_final} | "
                f"max_tokens={max_tokens:,}"
            )

        # === ADAPTIVE HINTS ===
        adaptive_hint_text = ""
        adaptive_hints_used = []
        perf_tracker = getattr(self, '_perf_tracker', None)
        if perf_tracker:
            # Normalizar role: "auditor_1_json" -> "A1", etc.
            normalized_role = self._normalize_role_for_perf(role_name)
            hints = perf_tracker.get_adaptive_hints(modelo_final, normalized_role)
            if hints.hint_text:
                adaptive_hint_text = hints.hint_text
                adaptive_hints_used = hints.hints
                logger.info(
                    f"[ADAPTIVE] {role_name}: {len(hints.hints)} hints activos: "
                    f"{hints.hints}"
                )

        # Injectar hints no system prompt
        effective_system = system_prompt
        if adaptive_hint_text:
            effective_system = (
                f"{system_prompt}\n\n"
                f"--- INSTRUCOES DE QUALIDADE (HISTORICO) ---\n"
                f"{adaptive_hint_text}"
            )

        # === CHAMADA LLM ===
        effective_temp = temperature

        # NOTE: Keep in sync with reasoning model list. These models don't support system_prompt or temperature.
        REASONING_MODELS = {"openai/o1-pro", "openai/o1", "openai/o3-pro", "openai/o3-mini", "deepseek/deepseek-reasoner", "deepseek/deepseek-r1"}
        if modelo_final in REASONING_MODELS:
            # Embed system prompt into user prompt for reasoning models
            if effective_system:
                prompt = f"<system_instructions>\n{effective_system}\n</system_instructions>\n\n{prompt}"
                effective_system = None
            effective_temp = None  # Reasoning models don't accept temperature

        response = self.llm_client.chat_simple(
            model=modelo_final,
            prompt=prompt,
            system_prompt=effective_system,
            temperature=effective_temp,
            max_tokens=max_tokens,
        )

        # FIX 2026-02-14: Acumular tokens de TODAS as chamadas (incluindo retries)
        _accumulated_prompt_tokens = response.prompt_tokens or 0
        _accumulated_completion_tokens = response.completion_tokens or 0

        # === QUALITY GATE + RETRIES (por modelo) ===
        # v4.0: Retries controlados por modelo (Opus=0, o1-pro=1, default=2)
        MODEL_MAX_RETRIES = {
            "anthropic/claude-opus-4.6": 2,  # v5.2: J3 precisa de 2 retries para citations
            "openai/o1-pro": 1,
            "openai/gpt-5.2-pro": 1,
            "deepseek/deepseek-reasoner": 1,
            "deepseek/deepseek-r1": 2,       # v5.2: J2 precisa de 2 retries para citations
        }
        MAX_RETRIES = MODEL_MAX_RETRIES.get(modelo_final, 2)

        # v5.2: content_filter → skip retries (Gemini vai recusar outra vez)
        if not response.success and getattr(response, 'finish_reason', '') == 'content_filter':
            logger.warning(
                f"[QUALITY-GATE] {role_name}: CONTENT_FILTER — skip retries "
                f"(modelo {modelo_final} bloqueou conteúdo, suplente assumirá)"
            )
            MAX_RETRIES = 0  # Não fazer retry nenhum

        for retry_num in range(1, MAX_RETRIES + 1):
            if not response.success or not response.content:
                # FIX 2026-02-18: Distinguir output truncado de falha generica
                if getattr(response, 'finish_reason', '') == 'length':
                    quality_issue = {
                        "code": "OUTPUT_TRUNCATED", "critical": True,
                        "msg": "Output truncado por max_tokens (finish_reason=length)"
                    }
                else:
                    quality_issue = {"code": "CALL_FAILED", "critical": True, "msg": "LLM call failed"}
            else:
                quality_issue = check_response_quality(response.content, role_name)

            if not quality_issue or not quality_issue.get("critical"):
                break  # Qualidade OK

            logger.warning(
                f"[QUALITY-GATE] {role_name}: {quality_issue['code']} "
                f"(retry {retry_num}/{MAX_RETRIES}): {quality_issue['msg']}"
            )

            # Registar chamada falhada no tracker
            if perf_tracker:
                perf_tracker.record_call(
                    run_id=getattr(self, '_run_id', ''),
                    model=modelo_final,
                    phase=role_name.split("_")[0],
                    role=self._normalize_role_for_perf(role_name),
                    tier=getattr(self, '_tier', 'bronze'),
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    total_tokens=response.total_tokens,
                    cost_usd=0,
                    latency_ms=response.latency_ms,
                    success=False,
                    error_message=quality_issue['msg'],
                    error_type=quality_issue['code'],
                    was_retry=retry_num > 1,
                    retry_number=retry_num - 1,
                    adaptive_hints_used=adaptive_hints_used,
                )

            # Registar custo do retry falhado no CostController
            if hasattr(self, '_cost_controller') and self._cost_controller:
                retry_pt = response.prompt_tokens or 0
                retry_ct = response.completion_tokens or 0
                if retry_pt or retry_ct:
                    try:
                        self._cost_controller.register_usage(
                            phase=f"{role_name}_retry{retry_num}",
                            model=modelo_final,
                            prompt_tokens=retry_pt,
                            completion_tokens=retry_ct,
                            raise_on_exceed=True,
                        )
                    except Exception as e:
                        if "Limit" in type(e).__name__ or "Budget" in type(e).__name__:
                            logger.error(f"[CUSTO-BLOQUEIO] Limite excedido em {role_name}_retry{retry_num}: {e}")
                            raise
                        logger.warning(f"[CUSTO] Falha ao registar custo de retry {role_name}: {e}")

            # Construir prompt melhorado para retry
            retry_system, retry_temp = build_retry_prompt(
                system_prompt, quality_issue, retry_num, adaptive_hint_text
            )
            effective_temp = retry_temp

            response = self.llm_client.chat_simple(
                model=modelo_final,
                prompt=prompt,
                system_prompt=retry_system,
                temperature=retry_temp,
                max_tokens=max_tokens,
            )
            logger.info(
                f"[QUALITY-GATE] {role_name}: Retry {retry_num} completado "
                f"(success={response.success}, len={len(response.content or '')})"
            )

            # Acumular tokens do retry
            _accumulated_prompt_tokens += response.prompt_tokens or 0
            _accumulated_completion_tokens += response.completion_tokens or 0

        # === TOKENS (usar acumulados para custo total real) ===
        prompt_tokens = response.prompt_tokens
        completion_tokens = response.completion_tokens
        total_tokens = response.total_tokens

        # Se API não retornou tokens, estimar com WARNING
        if total_tokens == 0 and response.content:
            prompt_tokens = len(prompt) // 4
            completion_tokens = len(response.content) // 4
            total_tokens = prompt_tokens + completion_tokens
            logger.warning(
                f"[CUSTO-ESTIMATIVA] {role_name}/{model}: API não retornou usage. "
                f"Estimativa: {prompt_tokens}+{completion_tokens}={total_tokens} tokens"
            )

        # Registar no CostController (se disponível)
        if hasattr(self, '_cost_controller') and self._cost_controller:
            try:
                # FIX 2026-02-14: Usar modelo_final (após failover), não model original
                self._cost_controller.register_usage(
                    phase=role_name,
                    model=modelo_final,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    raise_on_exceed=True,
                )
            except Exception as e:
                if "Limit" in type(e).__name__ or "Budget" in type(e).__name__:
                    logger.error(f"[CUSTO-BLOQUEIO] Limite excedido em {role_name}: {e}")
                    raise  # Propagar para parar o pipeline
                logger.warning(f"[CUSTO] Erro ao registar uso para {role_name}: {e}")

        # === REGISTAR PERFORMANCE (chamada final/bem-sucedida) ===
        if perf_tracker:
            cost_usd = 0.0
            pricing_source = ""
            if hasattr(self, '_cost_controller') and self._cost_controller:
                phases = getattr(self._cost_controller, 'usage', None)
                if phases and hasattr(phases, 'phases') and phases.phases:
                    last_phase = phases.phases[-1]
                    cost_usd = getattr(last_phase, 'cost_usd', 0)
                    pricing_source = getattr(last_phase, 'pricing_source', '')

            perf_tracker.record_call(
                run_id=getattr(self, '_run_id', ''),
                model=modelo_final,
                phase=role_name.split("_")[0],
                role=self._normalize_role_for_perf(role_name),
                tier=getattr(self, '_tier', 'bronze'),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
                pricing_source=pricing_source,
                latency_ms=response.latency_ms,
                success=response.success,
                error_message=response.error,
                error_type=classify_error(response.error) if response.error else None,
                was_retry=False,
                retry_number=0,
                adaptive_hints_used=adaptive_hints_used,
            )

        return FaseResult(
            fase=role_name.split("_")[0],
            modelo=modelo_final,  # NOVO: modelo real usado (pode ser suplente)
            role=role_name,
            conteudo=response.content or "",
            tokens_usados=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latencia_ms=response.latency_ms,
            sucesso=response.success,
            erro=response.error,
        )

    @staticmethod
    def _normalize_role_for_perf(role_name: str) -> str:
        """Normaliza role name para performance tracking."""
        r = role_name.lower()
        if "extrator" in r or "extractor" in r:
            for tag in ("e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8"):
                if tag in r:
                    return tag.upper()
            return "E1"
        if "auditor" in r or "audit" in r:
            for tag in ("a1", "a2", "a3", "a4"):
                if tag in r:
                    return tag.upper()
            # auditor_1 -> A1, auditor_2 -> A2
            for i in range(1, 5):
                if f"_{i}" in r:
                    return f"A{i}"
            return "A1"
        if "relator" in r or "judge" in r or "juiz" in r:
            for tag in ("j1", "j2", "j3"):
                if tag in r:
                    return tag.upper()
            for i in range(1, 4):
                if f"_{i}" in r:
                    return f"J{i}"
            return "J1"
        if "presidente" in r or "conselheiro" in r:
            return "PRESIDENTE"
        if "agregador" in r:
            return "AGREGADOR"
        if "chefe" in r or "consolidador" in r:
            return "CONSOLIDADOR"
        return role_name[:20]

    def _dividir_documento_chunks(self, texto: str, chunk_size: int = 50000, overlap: int = 2500) -> List[str]:
        """
        Divide documento grande em chunks com overlap para processamento.
        VERSÃO LEGACY - retorna apenas strings.

        Args:
            texto: Texto completo do documento
            chunk_size: Tamanho máximo de cada chunk em caracteres (default 50k)
            overlap: Número de caracteres para sobrepor entre chunks (default 2.5k)

        Returns:
            Lista de chunks (strings)
        """
        from src.config import CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS

        # Usar valores do config
        chunk_size = CHUNK_SIZE_CHARS
        overlap = CHUNK_OVERLAP_CHARS

        # Se documento é pequeno, não dividir
        if len(texto) <= chunk_size:
            logger.info(f"Documento pequeno ({len(texto):,} chars), SEM chunking")
            return [texto]

        chunks = []
        inicio = 0
        chunk_num = 0

        while inicio < len(texto):
            fim = min(inicio + chunk_size, len(texto))
            chunk = texto[inicio:fim]
            chunks.append(chunk)
            chunk_num += 1

            logger.info(f"Chunk {chunk_num}: chars {inicio:,}-{fim:,} (tamanho: {len(chunk):,})")

            # Se chegámos ao fim, parar
            if fim >= len(texto):
                break

            # Próximo chunk começa com overlap para não perder contexto
            inicio = fim - overlap

        logger.info(f"✂️ Documento dividido em {len(chunks)} chunk(s)")
        logger.info(f"📏 Chunk size: {chunk_size:,} | Overlap: {overlap:,} chars")

        return chunks

    def _criar_chunks_estruturados(
        self,
        texto: str,
        doc_id: str,
        method: str = "text",
        chunk_size: int = None,
        overlap: int = None
    ) -> List['Chunk']:
        """
        Divide documento em chunks estruturados com offsets rastreáveis.

        NOVA VERSÃO com proveniência completa.

        Args:
            texto: Texto completo do documento
            doc_id: ID único do documento
            method: "text" ou "ocr"
            chunk_size: Tamanho de cada chunk (default: config)
            overlap: Sobreposição entre chunks (default: config)

        Returns:
            Lista de Chunk objects com offsets precisos
        """
        from src.config import CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS
        from src.pipeline.schema_unified import Chunk, ExtractionMethod

        # Usar valores do config se não especificados
        chunk_size = chunk_size or CHUNK_SIZE_CHARS
        overlap = overlap or CHUNK_OVERLAP_CHARS

        total_chars = len(texto)
        step = chunk_size - overlap  # 47500 com defaults

        # Calcular número total de chunks
        if total_chars <= chunk_size:
            total_chunks = 1
        else:
            total_chunks = ((total_chars - chunk_size) // step) + 2

        chunks = []
        inicio = 0
        chunk_index = 0

        while inicio < total_chars:
            fim = min(inicio + chunk_size, total_chars)
            chunk_text = texto[inicio:fim]

            chunk = Chunk(
                doc_id=doc_id,
                chunk_id=f"{doc_id}_c{chunk_index:04d}",
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                start_char=inicio,
                end_char=fim,
                overlap=overlap if chunk_index > 0 else 0,
                text=chunk_text,
                method=ExtractionMethod(method),
            )
            chunks.append(chunk)

            logger.info(
                f"Chunk {chunk_index}: [{inicio:,} - {fim:,}) = {fim - inicio:,} chars "
                f"(overlap: {chunk.overlap})"
            )

            # Se chegámos ao fim, parar
            if fim >= total_chars:
                break

            # Próximo chunk começa com step (overlap já contabilizado)
            inicio += step
            chunk_index += 1

        # Atualizar total_chunks real
        actual_total = len(chunks)
        for c in chunks:
            c.total_chunks = actual_total

        logger.info(f"✂️ Documento dividido em {actual_total} chunk(s) estruturados")
        logger.info(f"📏 Config: chunk_size={chunk_size:,} | overlap={overlap:,} | step={step:,}")

        return chunks

    def _enrich_chunks_with_pages(
        self,
        chunks: List[Chunk],
        page_mapper: Optional[CharToPageMapper]
    ) -> None:
        """
        Preenche page_start e page_end nos chunks usando o page_mapper.

        Args:
            chunks: Lista de chunks a enriquecer
            page_mapper: CharToPageMapper (se None, não faz nada)
        """
        if page_mapper is None:
            return

        for chunk in chunks:
            page_start, page_end = page_mapper.get_page_range(chunk.start_char, chunk.end_char)
            chunk.page_start = page_start
            chunk.page_end = page_end

            if page_start is not None:
                logger.debug(
                    f"Chunk {chunk.chunk_index}: páginas {page_start}-{page_end}"
                )

    def _fase1_extracao_unified(
        self,
        documento: DocumentContent,
        area: str,
        use_provenance: bool = True
    ) -> tuple:
        """
        Fase 1 UNIFICADA: Extração com proveniência completa e cobertura auditável.

        Implementa o schema unificado com:
        - Chunks estruturados com offsets precisos
        - EvidenceItems com source_spans obrigatórios
        - Agregação LOSSLESS sem deduplicação
        - Auditoria de cobertura

        Args:
            documento: DocumentContent carregado
            area: Área do direito
            use_provenance: Se True, usa sistema completo de proveniência

        Returns:
            tuple: (resultados, bruto, consolidado, unified_result)
        """
        import json as json_module
        import hashlib

        logger.info("=== FASE 1 UNIFICADA: Extração com proveniência ===")
        self._reportar_progresso("fase1", 10, "Iniciando extração unificada com proveniência...")

        # 1. Criar metadados do documento
        doc_id = f"doc_{hashlib.md5(documento.filename.encode()).hexdigest()[:8]}"
        doc_meta = DocumentMeta(
            doc_id=doc_id,
            filename=documento.filename,
            file_type=documento.extension,
            total_chars=len(documento.text),
            total_pages=getattr(documento, 'num_pages', None),
        )

        # 1.5 Criar page_mapper para mapeamento char→página
        page_mapper = None
        try:
            page_mapper = CharToPageMapper.from_document_content(documento, doc_id)
            logger.info(f"📄 PageMapper criado: {page_mapper.total_pages} páginas, source={page_mapper.source}")
        except Exception as e:
            logger.warning(f"Não foi possível criar PageMapper: {e}")

        # 2. Criar chunks estruturados
        chunks = self._criar_chunks_estruturados(
            texto=documento.text,
            doc_id=doc_id,
            method="text",
        )
        num_chunks = len(chunks)

        # 2.5 Enriquecer chunks com page_start/page_end
        if page_mapper:
            self._enrich_chunks_with_pages(chunks, page_mapper)
            logger.info(f"📄 Chunks enriquecidos com informação de páginas")

        logger.info(f"Documento: {doc_meta.total_chars:,} chars → {num_chunks} chunk(s)")

        # 2.7 Recolher imagens de páginas escaneadas para análise visual
        scanned_pages = documento.metadata.get("scanned_pages", {}) if documento.metadata else {}
        # Pré-carregar imagens em base64 para não reler ficheiros a cada extrator
        scanned_images_b64 = {}
        if scanned_pages:
            for page_num_str, img_path in scanned_pages.items():
                page_num = int(page_num_str) if isinstance(page_num_str, str) else page_num_str
                img_file = Path(img_path)
                if img_file.exists():
                    img_bytes = img_file.read_bytes()
                    scanned_images_b64[page_num] = base64.b64encode(img_bytes).decode("utf-8")
                    logger.info(f"📸 Imagem página {page_num} carregada ({len(img_bytes):,} bytes)")
            logger.info(
                f"📸 {len(scanned_images_b64)} imagem(ns) de páginas escaneadas prontas "
                f"para envio a TODOS os extratores vision-capable"
            )

        # 3. Configurar extratores
        extractor_configs = [cfg for cfg in self._llm_configs if cfg["id"].startswith("E")]
        logger.info(f"=== {num_chunks} chunk(s) × {len(extractor_configs)} extratores = {num_chunks * len(extractor_configs)} chamadas LLM ===")

        # 4. Estruturas para armazenar resultados
        items_by_extractor = {}  # {extractor_id: [EvidenceItem]}
        extraction_runs = []
        all_unreadable = []
        resultados = []  # FaseResult para compatibilidade

        # 5. Processar cada extrator em todos os chunks (PARALELO com retry)
        def _run_extractor(i, cfg):
            """Executa um extrator em todos os chunks. Thread-safe."""
            extractor_id = cfg["id"]
            model = cfg["model"]
            role = cfg["role"]
            instructions = cfg["instructions"]
            temperature = cfg.get("temperature", 0.0)

            run = ExtractionRun(
                run_id=f"run_{extractor_id}_{doc_id}",
                extractor_id=extractor_id,
                model_name=model,
                method=ExtractionMethod.TEXT,
                status=ExtractionStatus.PENDING,
            )

            extractor_items = []
            extractor_content_parts = []
            chunk_errors = []
            local_unreadable = []
            extractor_total_tokens = 0
            extractor_prompt_tokens = 0
            extractor_completion_tokens = 0

            # v5.1: Todos os extractores usam os mesmos chunks (micro-chunking removido — Nova Pro substituído)
            extractor_chunks = chunks

            for chunk_idx, chunk in enumerate(extractor_chunks):
                chunk_info = f" (chunk {chunk_idx+1}/{len(extractor_chunks)})" if len(extractor_chunks) > 1 else ""

                logger.info(f"=== {extractor_id}{chunk_info} [{chunk.start_char:,}-{chunk.end_char:,}] - {model} ===")

                # Construir prompt unificado com metadados do chunk
                prompt = build_unified_prompt(chunk, area, extractor_id)

                # System prompt combinado — v4.0: visual extractors (E2, E7) use PROMPT_EXTRATOR_VISUAL
                is_visual = cfg.get("visual", False)
                base_prompt = PROMPT_EXTRATOR_VISUAL if is_visual else SYSTEM_EXTRATOR_UNIFIED
                sys_prompt = f"""{base_prompt}

INSTRUÇÕES ESPECÍFICAS DO EXTRATOR {extractor_id} ({role}):
{instructions}"""

                # Determinar se este chunk tem páginas escaneadas E o modelo suporta visão
                chunk_scanned_images = []
                if scanned_images_b64 and model in VISION_CAPABLE_MODELS:
                    for pg_num, b64_img in scanned_images_b64.items():
                        if chunk.page_start is not None and chunk.page_end is not None:
                            if chunk.page_start <= pg_num <= chunk.page_end:
                                chunk_scanned_images.append((pg_num, b64_img))
                        else:
                            chunk_scanned_images.append((pg_num, b64_img))

                # FIX 2026-02-18: Calcular max_tokens adequado ao modelo
                # Extractores produzem JSON extenso (~66K chars para chunk 50K)
                # Cap 65K para modelos com output grande; modelos pequenos usam seu limite real
                from src.config import MODEL_MAX_OUTPUT
                extractor_max_tokens = min(65_536, MODEL_MAX_OUTPUT.get(model, 16_384))
                logger.info(f"[MAX_TOKENS] {extractor_id}: modelo={model} → max_tokens={extractor_max_tokens:,}")

                # Chamar LLM com retry (com ou sem imagens)
                def _do_llm_call(
                    _model=model, _prompt=prompt, _sys=sys_prompt, _temp=temperature,
                    _images=chunk_scanned_images, _eid=extractor_id,
                    _max_tokens=extractor_max_tokens,
                ):
                    if _images:
                        pages_info = ", ".join(str(pg) for pg, _ in _images)
                        vision_note = (
                            f"\n\nNOTA IMPORTANTE: Este documento contém {len(_images)} "
                            f"página(s) digitalizada(s) (página(s) {pages_info}). "
                            f"As imagens dessas páginas estão anexas abaixo. "
                            f"DEVES analisar as imagens e extrair TODO o texto e informação visível: "
                            f"datas, valores, nomes, moradas, referências legais, assinaturas, "
                            f"carimbos, tabelas. Transcreve fielmente o conteúdo das imagens."
                        )
                        content_blocks = [{"type": "text", "text": _prompt + vision_note}]
                        for pg_num, b64_img in _images:
                            content_blocks.append({"type": "text", "text": f"\n--- Imagem da Página {pg_num} ---"})
                            content_blocks.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64_img}"}
                            })
                        messages = [{"role": "user", "content": content_blocks}]
                        logger.info(f"📸 {_eid}: enviando {len(_images)} imagem(ns) para análise visual")
                        return self.llm_client.chat(
                            model=_model, messages=messages,
                            system_prompt=_sys, temperature=_temp,
                            max_tokens=_max_tokens, timeout=120,
                        )
                    else:
                        return self.llm_client.chat_simple(
                            model=_model, prompt=_prompt,
                            system_prompt=_sys, temperature=_temp,
                            max_tokens=_max_tokens, timeout=120,
                        )

                # FIX 2026-02-18: Extractores com timeout 120s, 2 retries, deadline 250s
                response = _call_with_retry(
                    _do_llm_call,
                    func_name=f"{extractor_id}-chunk{chunk_idx}",
                    max_retries=2,
                    backoff_times=[5],
                    deadline=250,
                )

                # v5.1: Suplentes universais — se titular falha, suplente assume TODOS os chunks restantes
                from src.config import EXTRACTOR_SUBSTITUTES
                if response is None or not response.content or not getattr(response, 'success', True):
                    fail_reason = ""
                    if response and hasattr(response, 'finish_reason') and response.finish_reason:
                        fail_reason = f" (finish_reason={response.finish_reason})"
                    elif response and hasattr(response, 'error') and response.error:
                        fail_reason = f" ({response.error[:80]})"

                    # Tentar suplentes universais (gpt-5-mini → gemini-2.5-flash)
                    suplente_ok = False
                    for sub_model in EXTRACTOR_SUBSTITUTES:
                        if sub_model == model:
                            continue  # Não usar o mesmo modelo como suplente
                        logger.warning(
                            f"[SUPLENTE] {extractor_id} chunk {chunk_idx+1}: "
                            f"{model} falhou{fail_reason} → tentando {sub_model}"
                        )
                        sub_max_tokens = min(65_536, MODEL_MAX_OUTPUT.get(sub_model, 16_384))

                        # v5.1: Suplente com suporte a imagens (se visual e modelo capaz)
                        def _do_sub_call(
                            _model=sub_model, _prompt=prompt, _sys=sys_prompt,
                            _temp=temperature, _max_tokens=sub_max_tokens,
                            _images=chunk_scanned_images, _eid=extractor_id,
                        ):
                            if _images and _model in VISION_CAPABLE_MODELS:
                                pages_info = ", ".join(str(pg) for pg, _ in _images)
                                vision_note = (
                                    f"\n\nNOTA IMPORTANTE: Este documento contém {len(_images)} "
                                    f"página(s) digitalizada(s) (página(s) {pages_info}). "
                                    f"Analisa as imagens e extrai TODO o texto e informação visível."
                                )
                                content_blocks = [{"type": "text", "text": _prompt + vision_note}]
                                for pg_num, b64_img in _images:
                                    content_blocks.append({"type": "text", "text": f"\n--- Imagem da Página {pg_num} ---"})
                                    content_blocks.append({
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/png;base64,{b64_img}"}
                                    })
                                messages = [{"role": "user", "content": content_blocks}]
                                logger.info(f"📸 {_eid} [SUPLENTE]: enviando {len(_images)} imagem(ns) para {_model}")
                                return self.llm_client.chat(
                                    model=_model, messages=messages,
                                    system_prompt=_sys, temperature=_temp,
                                    max_tokens=_max_tokens, timeout=120,
                                )
                            else:
                                return self.llm_client.chat_simple(
                                    model=_model, prompt=_prompt,
                                    system_prompt=_sys, temperature=_temp,
                                    max_tokens=_max_tokens, timeout=120,
                                )

                        response = _call_with_retry(
                            _do_sub_call,
                            func_name=f"{extractor_id}-chunk{chunk_idx}-sub-{sub_model.split('/')[-1]}",
                            max_retries=1,
                        )
                        if response and response.content and getattr(response, 'success', True):
                            logger.info(
                                f"[SUPLENTE] {extractor_id} chunk {chunk_idx+1}: "
                                f"{sub_model.split('/')[-1]} OK ({len(response.content):,} chars) "
                                f"→ assume TODOS os chunks restantes"
                            )
                            # TITULAR MORTO: suplente assume todos os chunks restantes
                            model = sub_model
                            run.model_name = sub_model  # FIX: actualizar model_name para custo/tracking
                            suplente_ok = True
                            break
                        else:
                            logger.warning(
                                f"[SUPLENTE] {extractor_id} chunk {chunk_idx+1}: "
                                f"{sub_model.split('/')[-1]} também falhou"
                            )

                    if not suplente_ok:
                        chunk_errors.append(f"{extractor_id} chunk {chunk_idx}: titular + suplentes falharam")
                        logger.error(
                            f"✗ {extractor_id} chunk {chunk_idx+1}: TODOS os modelos falharam "
                            f"(titular={cfg['model']}, suplentes={EXTRACTOR_SUBSTITUTES}) — extrator descartado"
                        )
                        run.status = ExtractionStatus.FAILED
                        run.error_message = "Titular + todos os suplentes falharam"
                        extraction_runs.append(run)
                        return  # Descartar este extrator inteiro

                # Acumular tokens REAIS da resposta
                r_prompt = response.prompt_tokens
                r_completion = response.completion_tokens
                r_total = response.total_tokens
                if r_total == 0 and response.content:
                    r_prompt = len(prompt) // 4
                    r_completion = len(response.content) // 4
                    r_total = r_prompt + r_completion
                    logger.warning(f"[CUSTO-ESTIMATIVA] {extractor_id}-chunk{chunk_idx}: API sem usage")
                extractor_prompt_tokens += r_prompt
                extractor_completion_tokens += r_completion
                extractor_total_tokens += r_total

                # Registar no CostController
                if hasattr(self, '_cost_controller') and self._cost_controller:
                    try:
                        self._cost_controller.register_usage(
                            phase=f"fase1_{extractor_id}_chunk{chunk_idx}",
                            model=model,
                            prompt_tokens=r_prompt,
                            completion_tokens=r_completion,
                            raise_on_exceed=True,
                        )
                    except Exception as e:
                        if "Limit" in type(e).__name__ or "Budget" in type(e).__name__:
                            logger.error(f"[CUSTO-BLOQUEIO] Limite excedido em {extractor_id}-chunk{chunk_idx}: {e}")
                            raise
                        logger.warning(f"[CUSTO] Erro ao registar {extractor_id}-chunk{chunk_idx}: {e}")

                # Parsear output e criar EvidenceItems com source_spans
                items, unreadable, errors = parse_unified_output(
                    output=response.content,
                    chunk=chunk,
                    extractor_id=extractor_id,
                    model_name=model,
                    page_mapper=page_mapper,
                )

                extractor_items.extend(items)
                local_unreadable.extend(unreadable)
                chunk_errors.extend(errors)
                run.chunks_processed += 1

                # Converter para markdown para compatibilidade
                md_content = items_to_markdown(items, include_provenance=True)
                if num_chunks > 1:
                    extractor_content_parts.append(
                        f"### Chunk {chunk_idx+1} [{chunk.start_char:,}-{chunk.end_char:,}]\n{md_content}"
                    )
                else:
                    extractor_content_parts.append(md_content)

                logger.info(
                    f"✓ {extractor_id} chunk {chunk_idx}: {len(items)} items extraídos, "
                    f"{len(unreadable)} secções ilegíveis"
                )

            # Finalizar run deste extrator
            run.items_extracted = len(extractor_items)
            run.errors = chunk_errors
            run.status = ExtractionStatus.SUCCESS if not chunk_errors else ExtractionStatus.PARTIAL
            run.finished_at = datetime.now()

            # Criar FaseResult para compatibilidade (com tokens REAIS)
            full_content = "\n\n".join(extractor_content_parts)
            resultado = FaseResult(
                fase="extrator",
                modelo=model,
                role=f"extrator_{extractor_id}",
                conteudo=full_content,
                tokens_usados=extractor_total_tokens,
                prompt_tokens=extractor_prompt_tokens,
                completion_tokens=extractor_completion_tokens,
                latencia_ms=0,
                sucesso=run.status != ExtractionStatus.FAILED,
            )

            # Guardar ficheiros (thread-safe: cada extrator escreve os seus)
            self._log_to_file(
                f"fase1_extrator_{extractor_id}.md",
                f"# Extrator {extractor_id}: {role}\n## Modelo: {model}\n## Items: {len(extractor_items)}\n\n{full_content}"
            )

            items_json = [item.to_dict() for item in extractor_items]
            json_path = self._output_dir / f"fase1_extractor_{extractor_id}_items.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                json_module.dump(items_json, f, ensure_ascii=False, indent=2)

            logger.info(f"✓ Extrator {extractor_id} completo: {len(extractor_items)} items totais")

            return {
                "extractor_id": extractor_id,
                "items": extractor_items,
                "unreadable": local_unreadable,
                "run": run,
                "resultado": resultado,
            }

        # Executar extratores em PARALELO (timeout de segurança — ABORTA se exceder)
        from src.config import EXTRACTOR_TIMEOUT_PER_CHUNK, EXTRACTOR_TIMEOUT_MIN
        EXTRACTOR_TIMEOUT_SECONDS = max(EXTRACTOR_TIMEOUT_MIN, num_chunks * EXTRACTOR_TIMEOUT_PER_CHUNK)
        logger.info(f"[PARALELO] Lançando {len(extractor_configs)} extratores em paralelo "
                    f"(timeout={EXTRACTOR_TIMEOUT_SECONDS}s = {num_chunks} chunks × {EXTRACTOR_TIMEOUT_PER_CHUNK}s)...")
        _extraction_lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=min(10, len(extractor_configs))) as executor:
            futures = {}
            for i, cfg in enumerate(extractor_configs):
                future = executor.submit(_run_extractor, i, cfg)
                futures[future] = cfg["id"]

            try:
                for future in as_completed(futures, timeout=EXTRACTOR_TIMEOUT_SECONDS):
                    eid = futures[future]
                    try:
                        result = future.result()
                        if result is not None:
                            with _extraction_lock:
                                items_by_extractor[result["extractor_id"]] = result["items"]
                                all_unreadable.extend(result["unreadable"])
                                extraction_runs.append(result["run"])
                                resultados.append(result["resultado"])
                            logger.info(f"[PARALELO] {eid} concluído: {len(result['items'])} items")
                        else:
                            logger.warning(f"[PARALELO] {eid} retornou None - ignorado")
                    except Exception as exc:
                        if "Budget" in type(exc).__name__ or "Limit" in type(exc).__name__:
                            raise  # Re-raise budget/limit errors immediately
                        logger.error(f"[PARALELO] {eid} excepção: {exc}")
            except TimeoutError:
                # v4.1: Continuar se ≥5 extractores terminaram (71%+)
                timed_out = [eid for f, eid in futures.items() if not f.done()]
                for f in futures:
                    if not f.done():
                        f.cancel()
                completed_count = len(items_by_extractor)
                total_count = len(futures)
                min_required = max(2, total_count - 2)  # Permitir até 2 falhas
                if completed_count >= min_required:
                    logger.warning(
                        f"TIMEOUT: {len(timed_out)} extratores não acabaram em "
                        f"{EXTRACTOR_TIMEOUT_SECONDS}s: {timed_out}. "
                        f"Continuando com {completed_count}/{total_count} extractores."
                    )
                else:
                    raise Exception(
                        f"TIMEOUT CRÍTICO: {len(timed_out)} extratores não acabaram em "
                        f"{EXTRACTOR_TIMEOUT_SECONDS}s: {timed_out}. "
                        f"Apenas {completed_count}/{total_count} completaram "
                        f"(mínimo {min_required})."
                    )

        if len(items_by_extractor) < 2:
            raise Exception(
                f"Apenas {len(items_by_extractor)} extratores funcionaram (mínimo 2). "
                f"Pipeline abortado."
            )

        # 6. v4.0: Smart discard + Agregação com deduplicação semântica
        self._reportar_progresso("fase1", 30, "Descarte inteligente de extractores fracos...")

        # v4.0: Filtrar extractores fracos (preservando factos exclusivos)
        items_filtered = validate_and_filter_extractors(items_by_extractor)

        self._reportar_progresso("fase1", 32, "Agregando com deduplicação semântica...")

        union_items, conflicts = aggregate_with_provenance(
            items_by_extractor=items_filtered,
            detect_conflicts=True,
            deduplicate=True,
        )

        logger.info(f"Agregação: {len(union_items)} items unidos, {len(conflicts)} conflitos detetados")

        # 7. Calcular cobertura (chars e páginas)
        coverage_data = calculate_coverage(
            chunks=chunks,
            items=union_items,
            total_chars=doc_meta.total_chars,
            page_mapper=page_mapper,
            total_pages=doc_meta.total_pages,
        )

        # Log de cobertura (chars)
        logger.info(
            f"Cobertura chars: {coverage_data['coverage_percent']:.1f}% | "
            f"Completa: {coverage_data['is_complete']} | "
            f"Gaps: {len(coverage_data['gaps'])}"
        )

        # Log de cobertura (páginas) se disponível
        if 'pages_total' in coverage_data and page_mapper is not None:
            pages_unreadable = coverage_data.get('pages_unreadable', 0)
            pages_missing = coverage_data.get('pages_missing', 0)
            logger.info(
                f"Cobertura páginas: {coverage_data.get('pages_coverage_percent', 0):.1f}% | "
                f"Ilegíveis: {pages_unreadable} | "
                f"Faltam: {pages_missing}"
            )

        # 8. Criar resultado unificado
        unified_result = UnifiedExtractionResult(
            result_id=f"unified_{self._run_id}",
            document_meta=doc_meta,
            chunks=chunks,
            extraction_runs=extraction_runs,
            evidence_items=[item for items in items_by_extractor.values() for item in items],
            union_items=union_items,
            conflicts=[],  # Converter para Conflict objects se necessário
            coverage=None,  # Preencher se necessário
            status=ExtractionStatus.SUCCESS,
        )

        # 9. Validar resultado
        is_valid, validation_errors = unified_result.validate()
        if not is_valid:
            logger.warning(f"Validação do resultado unificado: {validation_errors}")

        # 10. Guardar resultado unificado
        unified_json_path = self._output_dir / "fase1_unified_result.json"
        with open(unified_json_path, 'w', encoding='utf-8') as f:
            json_module.dump(unified_result.to_dict(), f, ensure_ascii=False, indent=2)

        # Guardar relatório de cobertura
        coverage_path = self._output_dir / "fase1_coverage_report.json"
        with open(coverage_path, 'w', encoding='utf-8') as f:
            json_module.dump(coverage_data, f, ensure_ascii=False, indent=2)

        # 11. Criar bruto para compatibilidade
        bruto_parts = ["# EXTRAÇÃO AGREGADA (BRUTO) - MODO UNIFICADO COM PROVENIÊNCIA\n"]
        for i, r in enumerate(resultados):
            cfg = extractor_configs[i] if i < len(extractor_configs) else {"id": f"E{i+1}", "role": "Extrator"}
            bruto_parts.append(f"\n## [EXTRATOR {cfg['id']}: {cfg['role']} - {r.modelo}]\n")
            bruto_parts.append(r.conteudo or "")
            bruto_parts.append("\n---\n")
        bruto = "\n".join(bruto_parts)
        self._log_to_file("fase1_agregado_bruto.md", bruto)

        # 11b. JSON-FIRST: Criar JSON estruturado PRIMEIRO (fonte de verdade)
        # Extrair unreadable_parts das secções ilegíveis detectadas
        unreadable_parts = []
        if page_mapper is not None:
            for page_num in page_mapper.get_unreadable_pages():
                boundary = page_mapper.get_boundary(page_num)
                unreadable_parts.append({
                    "doc_id": doc_meta.doc_id,
                    "page_num": page_num,
                    "start_char": boundary.start_char if boundary else None,
                    "end_char": boundary.end_char if boundary else None,
                    "status": boundary.status if boundary else "UNKNOWN",
                    "reason": f"Page status: {boundary.status}" if boundary else "Unknown",
                })

        # Coletar errors e warnings de extraction_runs
        all_errors = []
        all_warnings = []
        for run in extraction_runs:
            for error in run.errors:
                all_errors.append({
                    "extractor_id": run.extractor_id,
                    "error": error,
                })

        agregado_json = {
            "run_id": self._run_id,
            "timestamp": datetime.now().isoformat(),
            "doc_meta": doc_meta.to_dict(),
            "union_items": [item.to_dict() for item in union_items],
            "union_items_count": len(union_items),
            "items_by_extractor": {
                ext_id: len(items) for ext_id, items in items_by_extractor.items()
            },
            "coverage_report": coverage_data,
            "unreadable_parts": unreadable_parts,
            "conflicts": conflicts,
            "conflicts_count": len(conflicts),
            "extraction_runs": [run.to_dict() for run in extraction_runs],
            "errors": all_errors,
            "warnings": all_warnings,
            "summary": {
                "total_items": len(union_items),
                "coverage_percent": coverage_data.get('coverage_percent', 0),
                "is_complete": coverage_data.get('is_complete', False),
                "pages_total": coverage_data.get('pages_total', 0),
                "pages_unreadable": len(unreadable_parts),
                "extractors_count": len(items_by_extractor),
            },
        }

        # CRÍTICO: Escrever JSON estruturado (fonte de verdade)
        agregado_json_path = self._output_dir / "fase1_agregado_consolidado.json"
        logger.info(f"[JSON-WRITE] Escrevendo fase1_agregado_consolidado.json em: {agregado_json_path.absolute()}")
        try:
            with open(agregado_json_path, 'w', encoding='utf-8') as f:
                json_module.dump(agregado_json, f, ensure_ascii=False, indent=2)
            logger.info(f"✓ Agregado JSON guardado: {agregado_json_path.absolute()} ({len(union_items)} items, {len(unreadable_parts)} ilegíveis)")
        except Exception as e:
            logger.error(f"[JSON-WRITE-ERROR] Falha ao escrever fase1_agregado_consolidado.json: {e}")

        # 11c. DERIVAR Markdown do JSON (JSON é fonte de verdade)
        consolidado = render_agregado_markdown_from_json(agregado_json)
        self._log_to_file("fase1_agregado_consolidado.md", consolidado)
        self._log_to_file("fase1_agregado.md", consolidado)
        logger.info(f"✓ Markdown derivado do JSON (JSON-FIRST)")

        # 12. Chamar Agregador LLM para consolidação semântica (opcional, para compatibilidade)
        self._reportar_progresso("fase1", 35, f"Agregador semântico: {self.agregador_model}")

        prompt_agregador = f"""EXTRAÇÕES DOS {len(extractor_configs)} MODELOS COM PROVENIÊNCIA:

{bruto}

METADADOS:
- Total items: {len(union_items)}
- Cobertura: {coverage_data['coverage_percent']:.1f}%
- Conflitos: {len(conflicts)}

MISSÃO DO AGREGADOR:
Consolida estas extrações numa única extração LOSSLESS.
CRÍTICO: Preservar TODOS os source_spans e proveniência.
Área do Direito: {area}"""

        agregador_result = self._call_llm(
            model=self.agregador_model,
            prompt=prompt_agregador,
            system_prompt=self._build_system_agregador(len(extractor_configs)),
            role_name="agregador",
        )

        consolidado_final = f"# EXTRAÇÃO CONSOLIDADA (AGREGADOR + PROVENIÊNCIA)\n\n"
        consolidado_final += f"## Metadados de Cobertura\n"
        consolidado_final += f"- Items: {len(union_items)} | Cobertura: {coverage_data['coverage_percent']:.1f}% | Conflitos: {len(conflicts)}\n\n"
        consolidado_final += agregador_result.conteudo
        self._log_to_file("fase1_agregado_final.md", consolidado_final)

        logger.info("=== FASE 1 UNIFICADA COMPLETA ===")

        return resultados, bruto, consolidado_final, unified_result

    def _fase1_extracao(self, documento: DocumentContent, area: str) -> tuple:
        """
        Fase 1: 7 Extratores LLM -> Agregador LLM (LOSSLESS).
        NOTA: Extratores são CEGOS a perguntas do utilizador.

        CORREÇÃO #3: Para PDFs com pdf_safe_result, usa batches em vez de string única.

        Returns:
            tuple: (resultados, bruto, consolidado)
        """
        logger.info("Fase 1 - Extratores: perguntas_count=0 (extratores sao cegos a perguntas)")
        self._reportar_progresso("fase1", 10, "Iniciando extracao com 3 LLMs...")

        # CORREÇÃO #3: Verificar se é PDF com pdf_safe_result
        use_batches = (
            hasattr(documento, 'pdf_safe_enabled') and
            documento.pdf_safe_enabled and
            hasattr(documento, 'pdf_safe_result') and
            documento.pdf_safe_result is not None
        )

        if use_batches:
            # Modo BATCH: processar página-a-página
            return self._fase1_extracao_batch(documento, area)

        # Modo TRADICIONAL: string única (para não-PDFs ou PDFs sem pdf_safe)
        # NOVO: Dividir documento em chunks automático se necessário
        chunks = self._dividir_documento_chunks(documento.text)
        num_chunks = len(chunks)
        
        logger.info(f"=== FASE 1: {num_chunks} chunk(s) × {len(self._llm_configs)} extratores = {num_chunks * len(self._llm_configs)} chamadas LLM ===")
        
        extractor_configs = [cfg for cfg in self._llm_configs if cfg["id"].startswith("E")]
        
        resultados = []
        for i, cfg in enumerate(extractor_configs):
            extractor_id = cfg["id"]
            model = cfg["model"]
            role = cfg["role"]
            instructions = cfg["instructions"]
            temperature = cfg.get("temperature", 0.0)
            
            # NOVO: Processar cada chunk deste extrator
            conteudos_chunks = []
            ext_total_tokens = 0
            ext_prompt_tokens = 0
            ext_completion_tokens = 0

            for chunk_idx, chunk in enumerate(chunks):
                chunk_info = f" (chunk {chunk_idx+1}/{num_chunks})" if num_chunks > 1 else ""
                
                self._reportar_progresso(
                    "fase1",
                    10 + (i * num_chunks + chunk_idx) * (20 // (len(extractor_configs) * num_chunks)),
                    f"Extrator {extractor_id}{chunk_info}: {model}"
                )
                
                logger.info(f"=== Extrator {extractor_id}{chunk_info} - {model} ===")
                
                # Prompt com info do chunk
                chunk_header = f"[CHUNK {chunk_idx+1}/{num_chunks}] " if num_chunks > 1 else ""
                prompt_especializado = f"""DOCUMENTO A ANALISAR {chunk_header}:
Ficheiro: {documento.filename}
Tipo: {documento.extension}
Área do Direito: {area}
Total documento completo: {len(documento.text):,} caracteres
{"Este chunk: " + str(len(chunk)) + " caracteres" if num_chunks > 1 else ""}

CONTEÚDO{chunk_header}:
{chunk}

{instructions}
"""
                
                resultado_chunk = self._call_llm(
                    model=model,
                    prompt=prompt_especializado,
                    system_prompt=f"Você é um extrator especializado. {instructions[:200]}",
                    role_name=f"extrator_{extractor_id}_chunk{chunk_idx}",
                    temperature=temperature,
                )
                
                conteudos_chunks.append(resultado_chunk.conteudo)
                ext_total_tokens += resultado_chunk.tokens_usados
                ext_prompt_tokens += resultado_chunk.prompt_tokens
                ext_completion_tokens += resultado_chunk.completion_tokens
                logger.info(f"✓ Chunk {chunk_idx+1} processado: {len(resultado_chunk.conteudo):,} chars extraídos")
            
            # Consolidar chunks deste extrator
            if num_chunks > 1:
                conteudo_final = "\n\n═══[CHUNK SEGUINTE]═══\n\n".join(conteudos_chunks)
                logger.info(f"✓ Extrator {extractor_id}: {num_chunks} chunks consolidados → {len(conteudo_final):,} chars totais")
            else:
                conteudo_final = conteudos_chunks[0]
            
            # Criar FaseResult consolidado para este extrator (tokens REAIS)
            resultado_consolidado = FaseResult(
                fase="extrator",
                modelo=model,
                role=f"extrator_{extractor_id}",
                conteudo=conteudo_final,
                tokens_usados=ext_total_tokens,
                prompt_tokens=ext_prompt_tokens,
                completion_tokens=ext_completion_tokens,
                latencia_ms=0,
                sucesso=True,
                erro=None
            )
            
            resultados.append(resultado_consolidado)
            
            # Log do extrator completo (todos os chunks consolidados)
            self._log_to_file(
                f"fase1_extrator_{extractor_id}.md",
                f"# Extrator {extractor_id}: {role}\n## Modelo: {model}\n## Chunks processados: {num_chunks}\n\n{conteudo_final}"
            )

        # Guard: if all extractors failed, skip aggregation
        successful_extractions = [r for r in resultados if r.sucesso and (r.conteudo or "").strip()]
        if not successful_extractions:
            logger.error("Todas as extrações falharam - impossível agregar")
            raise Exception("Todas as extrações falharam - impossível agregar")

        # Criar agregado BRUTO (concatenação simples de todos os extratores)
        self._reportar_progresso("fase1", 32, f"Criando agregado bruto ({len(extractor_configs)} extratores)...")

        bruto_parts = [f"# EXTRAÇÃO AGREGADA (BRUTO) - {len(extractor_configs)} EXTRATORES\n"]
        for i, r in enumerate(resultados):
            cfg = extractor_configs[i] if i < len(extractor_configs) else {"id": f"E{i+1}", "role": "Extrator"}
            bruto_parts.append(f"\n## [EXTRATOR {cfg['id']}: {cfg['role']} - {r.modelo}]\n")
            bruto_parts.append(r.conteudo or "")
            bruto_parts.append("\n---\n")

        bruto = "\n".join(bruto_parts)
        self._log_to_file("fase1_agregado_bruto.md", bruto)

        # Chamar Agregador LLM para consolidação LOSSLESS
        self._reportar_progresso("fase1", 35, f"Agregador consolidando {len(extractor_configs)} extrações: {self.agregador_model}")

        prompt_agregador = f"""EXTRAÇÕES DOS {len(extractor_configs)} MODELOS ESPECIALIZADOS:

{bruto}

MISSÃO DO AGREGADOR:
Consolida estas {len(extractor_configs)} extrações numa única extração LOSSLESS.
Cada extrator (E1-E{len(extractor_configs)}) trouxe perspectiva diferente com modelos distintos.

CRÍTICO: Preservar TODOS os dados numéricos, datas, valores e referências de TODOS os extratores.
Área do Direito: {area}"""

        agregador_result = self._call_llm(
            model=self.agregador_model,
            prompt=prompt_agregador,
            system_prompt=self._build_system_agregador(len(extractor_configs)),
            role_name="agregador",
        )

        consolidado = f"# EXTRAÇÃO CONSOLIDADA (AGREGADOR: {self.agregador_model})\n\n{agregador_result.conteudo}"
        self._log_to_file("fase1_agregado_consolidado.md", consolidado)

        # Backwards compat: guardar também como fase1_agregado.md
        self._log_to_file("fase1_agregado.md", consolidado)

        # DETETOR INTRA-PÁGINA (modo tradicional): verificar sinais não extraídos
        # Funciona mesmo sem pdf_safe, criando PageRecords a partir do texto
        logger.info("=== DETETOR INTRA-PÁGINA: INICIANDO (modo tradicional) ===")
        logger.info(f"=== DETETOR: output_dir = {self._output_dir} ===")
        logger.info(f"=== DETETOR: documento.text tem {len(documento.text)} chars ===")
        try:
            from src.pipeline.pdf_safe import (
                PageRecord, PageMetrics, verificar_cobertura_sinais,
                REGEX_DATAS_PT, REGEX_VALORES_EURO, REGEX_ARTIGOS_PT
            )
            import json as json_module
            logger.info("=== DETETOR: imports OK ===")

            # Extrair páginas do texto usando marcadores [Página X]
            import re
            page_pattern = re.compile(r'\[Página\s*(\d+)\]\s*\n(.*?)(?=\[Página\s*\d+\]|\Z)', re.DOTALL | re.IGNORECASE)
            matches = page_pattern.findall(documento.text)
            logger.info(f"=== DETETOR: {len(matches)} páginas extraídas do texto ===")

            # DEBUG: mostrar primeiros 200 chars do texto para ver formato
            logger.info(f"=== DETETOR: Primeiros 200 chars do texto: {documento.text[:200]!r} ===")

            if matches:
                # Criar PageRecords simplificados para cada página
                pages = []
                for page_num_str, page_text in matches:
                    page_num = int(page_num_str)
                    text_clean = page_text.strip()

                    # Detetar sinais nesta página
                    dates = REGEX_DATAS_PT.findall(text_clean)
                    dates_detected = [d[0] or d[1] for d in dates if d[0] or d[1]]
                    values_detected = REGEX_VALORES_EURO.findall(text_clean)
                    legal_refs_detected = REGEX_ARTIGOS_PT.findall(text_clean)

                    metrics = PageMetrics(
                        chars_raw=len(text_clean),
                        chars_clean=len(text_clean),
                        dates_detected=dates_detected,
                        values_detected=values_detected,
                        legal_refs_detected=legal_refs_detected,
                    )

                    page_record = PageRecord(
                        page_num=page_num,
                        text_raw=text_clean,
                        text_clean=text_clean,
                        metrics=metrics,
                        status_inicial="OK",
                        status_final="OK",
                    )
                    pages.append(page_record)

                logger.info(f"=== DETETOR: {len(pages)} PageRecords criados ===")

                if pages:
                    # Verificar cobertura de sinais pelos extratores
                    extractor_outputs = {f"E{i+1}": (r.conteudo or "") for i, r in enumerate(resultados)}
                    signal_report = verificar_cobertura_sinais(pages, extractor_outputs)
                    logger.info(f"=== DETETOR: verificar_cobertura_sinais executado ===")

                    # Guardar relatório de sinais
                    signal_report_path = self._output_dir / "signals_coverage_report.json"
                    logger.info(f"=== DETETOR: A guardar em {signal_report_path} ===")
                    with open(signal_report_path, 'w', encoding='utf-8') as f:
                        json_module.dump(signal_report, f, ensure_ascii=False, indent=2)
                    logger.info(f"=== DETETOR: Relatório GUARDADO com sucesso em {signal_report_path} ===")

                    # Log de sinais não cobertos
                    if signal_report["uncovered_signals"]:
                        logger.warning(f"ALERTA: {len(signal_report['uncovered_signals'])} página(s) com sinais não extraídos")
                        for s in signal_report["uncovered_signals"][:5]:
                            logger.warning(f"  Página {s['page_num']}: {len(s['uncovered'])} sinal(ais) em falta")
                    else:
                        logger.info("Detetor intra-página: todos os sinais foram cobertos")
            else:
                logger.warning("=== DETETOR: NENHUMA página extraída! Texto não tem marcadores [Página X] ===")

        except Exception as e:
            logger.error(f"=== DETETOR FALHOU: {e} ===", exc_info=True)

        return resultados, bruto, consolidado

    def _fase1_extracao_batch(self, documento: DocumentContent, area: str) -> tuple:
        """
        Fase 1 em modo BATCH: processa páginas em lotes de 50k chars.

        CORREÇÃO #3: Não junta tudo numa string única - processa por batches.

        Returns:
            tuple: (resultados, bruto, consolidado)
        """
        from src.pipeline.pdf_safe import batch_pages, CoverageMatrix, detetor_intra_pagina
        from src.pipeline.extractor_json import (
            SYSTEM_EXTRATOR_JSON,
            build_extractor_input,
            parse_extractor_output,
            extractions_to_markdown,
            merge_extractor_results,
        )
        from src.config import MAX_SIGNAL_RETRIES, RECALL_MIN_THRESHOLD

        pdf_result = documento.pdf_safe_result
        pages = pdf_result.pages

        logger.info(f"Fase 1 BATCH: {len(pages)} páginas, PDF Seguro ativado")

        # Dividir páginas em batches
        batches = batch_pages(pages, max_chars=CHUNK_SIZE_CHARS)
        logger.info(f"Dividido em {len(batches)} batch(es)")

        # Processar cada extrator em todos os batches
        # USAR TODOS OS EXTRATORES (E1-E7) COM PROMPT UNIVERSAL
        extractor_configs = [cfg for cfg in self._llm_configs if cfg["id"].startswith("E")]
        logger.info(f"=== FASE 1 BATCH: Usando {len(extractor_configs)} extratores especializados ===")

        all_extractor_results = []  # Lista de resultados por extrator
        resultados = []  # FaseResult para compatibilidade

        for i, cfg in enumerate(extractor_configs):
            extractor_id = cfg["id"]
            model = cfg["model"]
            role = cfg["role"]
            instructions = cfg["instructions"]
            temperature = cfg.get("temperature", 0.0)

            self._reportar_progresso("fase1", 10 + i * 4, f"Extrator {extractor_id} ({role}): {model} ({len(batches)} batches)")
            logger.info(f"=== Extrator {extractor_id}: {role} - {model} ===")

            extractor_content_parts = []
            extractor_json_results = []

            for batch_idx, batch in enumerate(batches):
                # Construir input JSON para este batch
                json_input = build_extractor_input(batch)
                valid_page_nums = [p["page_num"] for p in batch]

                original_prompt = f"""DOCUMENTO A ANALISAR (Batch {batch_idx + 1}/{len(batches)}):
Ficheiro: {documento.filename}
Área do Direito: {area}
Total de páginas no documento: {len(pages)}

PÁGINAS NESTE BATCH (JSON):
{json_input}

INSTRUÇÕES ESPECÍFICAS DO EXTRATOR {extractor_id} ({role}):
{instructions}

Extrai informação de CADA página no formato JSON especificado.
IMPORTANTE: Só usa page_num que existam no batch acima."""

                # Retry loop para sinais em falta
                prompt = original_prompt
                # v4.0: visual extractors (E2, E7) use PROMPT_EXTRATOR_VISUAL
                batch_sys_prompt = PROMPT_EXTRATOR_VISUAL if cfg.get("visual", False) else SYSTEM_EXTRATOR_JSON
                for attempt in range(MAX_SIGNAL_RETRIES + 1):
                    # Chamar LLM com temperature específica
                    response = self.llm_client.chat_simple(
                        model=model,
                        prompt=prompt,
                        system_prompt=batch_sys_prompt,
                        temperature=temperature,
                    )

                    # Registar tokens REAIS no CostController
                    if hasattr(self, '_cost_controller') and self._cost_controller:
                        r_pt = response.prompt_tokens
                        r_ct = response.completion_tokens
                        if response.total_tokens == 0 and response.content:
                            r_pt = len(prompt) // 4
                            r_ct = len(response.content) // 4
                        try:
                            self._cost_controller.register_usage(
                                phase=f"fase1_{extractor_id}_batch{batch_idx}" + (f"_retry{attempt}" if attempt > 0 else ""),
                                model=model,
                                prompt_tokens=r_pt,
                                completion_tokens=r_ct,
                                raise_on_exceed=True,
                            )
                        except Exception as e:
                            if "Limit" in type(e).__name__ or "Budget" in type(e).__name__:
                                logger.error(f"[CUSTO-BLOQUEIO] Limite excedido em {extractor_id}-batch{batch_idx}: {e}")
                                raise
                            logger.warning(f"[CUSTO] Erro ao registar {extractor_id}-batch{batch_idx}: {e}")

                    # Parsear e validar output
                    parsed = parse_extractor_output(response.content, valid_page_nums, extractor_id)

                    # Verificar sinais em falta (retry se necessário)
                    if attempt < MAX_SIGNAL_RETRIES:
                        try:
                            batch_page_records = [p for p in pages if p.page_num in valid_page_nums]
                            md_content = extractions_to_markdown(parsed["extractions"], extractor_id)
                            missing = detetor_intra_pagina(batch_page_records, md_content, extractor_id)
                            total_missing = sum(s["total_missing"] for s in missing)

                            if total_missing == 0:
                                break  # Cobertura completa

                            # Construir prompt de retry com sinais em falta
                            missing_list = []
                            for s in missing:
                                for sig in s["missing_signals"]:
                                    missing_list.append(f"- Página {s['page_num']}: {sig['signal_type']} = \"{sig['detected']}\"")

                            prompt = f"""RETRY {attempt + 1}: A tua extração anterior OMITIU os seguintes sinais críticos:

{chr(10).join(missing_list)}

DOCUMENTO ORIGINAL:
{json_input}

INSTRUÇÕES: Re-extrai incluindo TODOS os sinais acima. Não omitas nenhum.
Devolve o JSON completo no mesmo formato."""

                            logger.warning(f"  RETRY {attempt+1} para {extractor_id} batch {batch_idx+1}: "
                                          f"{total_missing} sinais em falta")
                        except Exception as e:
                            logger.debug(f"Erro na verificação de sinais para retry: {e}")
                            break  # Não bloquear o pipeline
                    # End retry loop

                extractor_json_results.append(parsed)

                # Converter para markdown para compatibilidade
                md_content = extractions_to_markdown(parsed["extractions"], extractor_id)
                extractor_content_parts.append(f"## Batch {batch_idx + 1}\n{md_content}")

            # Combinar todos os batches deste extrator
            full_content = "\n\n".join(extractor_content_parts)

            # Criar FaseResult para compatibilidade
            resultado = FaseResult(
                fase="extrator",
                modelo=model,
                role=f"extrator_{extractor_id}",
                conteudo=full_content,
                tokens_usados=sum(r.get("tokens", 0) for r in extractor_json_results),
                latencia_ms=0,
                sucesso=True,
            )
            resultados.append(resultado)
            all_extractor_results.append((extractor_id, extractor_json_results))

            # Log individual
            self._log_to_file(f"fase1_extrator_{extractor_id}.md", f"# Extrator {extractor_id}: {role}\n## Modelo: {model}\n\n{full_content}")

            # Guardar JSON para auditoria
            import json as json_module
            json_path = self._output_dir / f"fase1_extractor_{extractor_id}.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                json_module.dump(extractor_json_results, f, ensure_ascii=False, indent=2)

        # Criar matriz de cobertura
        coverage = CoverageMatrix()
        for ext_id, ext_results in all_extractor_results:
            for batch_result in ext_results:
                coverage.add_extraction(ext_id, batch_result.get("pages_covered", []))
                for ur in batch_result.get("pages_unreadable", []):
                    coverage.add_unreadable(ext_id, ur["page_num"], ur.get("reason", ""))

        coverage.finalize(len(pages))
        coverage.save(self._output_dir)

        # DETETOR INTRA-PÁGINA: verificar sinais não extraídos
        from src.pipeline.pdf_safe import verificar_cobertura_sinais
        # Usar IDs dos extratores (E1-E5)
        extractor_outputs = {
            (extractor_configs[i]["id"] if i < len(extractor_configs) else f"E{i+1}"): (r.conteudo or "")
            for i, r in enumerate(resultados)
        }
        signal_report = verificar_cobertura_sinais(pages, extractor_outputs)

        # Guardar relatório de sinais
        import json as json_module
        signal_report_path = self._output_dir / "signals_coverage_report.json"
        with open(signal_report_path, 'w', encoding='utf-8') as f:
            json_module.dump(signal_report, f, ensure_ascii=False, indent=2)

        # Log de sinais não cobertos
        if signal_report["uncovered_signals"]:
            logger.warning(f"ALERTA: {len(signal_report['uncovered_signals'])} página(s) com sinais não extraídos")
            for s in signal_report["uncovered_signals"][:5]:  # Primeiras 5
                logger.warning(f"  Página {s['page_num']}: {len(s['uncovered'])} sinal(ais) em falta")

        # Calcular recall score
        total_signals = signal_report.get("total_signals_detected", 0)
        total_uncovered = sum(len(s["uncovered"]) for s in signal_report.get("uncovered_signals", []))
        if total_signals > 0:
            recall_score = (total_signals - total_uncovered) / total_signals
        else:
            recall_score = 1.0
        signal_report["recall_score"] = round(recall_score, 4)
        logger.info(f"RECALL SCORE: {recall_score:.2%} ({total_signals - total_uncovered}/{total_signals} sinais)")
        if recall_score < RECALL_MIN_THRESHOLD:
            logger.warning(f"ALERTA: Recall {recall_score:.2%} < threshold {RECALL_MIN_THRESHOLD:.0%}")

        # Guard: if all extractors failed, skip aggregation
        successful_extractions = [r for r in resultados if r.sucesso and (r.conteudo or "").strip()]
        if not successful_extractions:
            logger.error("Todas as extrações falharam - impossível agregar")
            raise Exception("Todas as extrações falharam - impossível agregar")

        # Criar agregado BRUTO (concatenação simples de todos os extratores)
        self._reportar_progresso("fase1", 32, f"Criando agregado bruto ({len(extractor_configs)} extratores)...")

        bruto_parts = [f"# EXTRAÇÃO AGREGADA (BRUTO) - MODO BATCH - {len(extractor_configs)} EXTRATORES\n"]
        for i, r in enumerate(resultados):
            cfg = extractor_configs[i] if i < len(extractor_configs) else {"id": f"E{i+1}", "role": "Extrator"}
            bruto_parts.append(f"\n## [EXTRATOR {cfg['id']}: {cfg['role']} - {r.modelo}]\n")
            bruto_parts.append(r.conteudo or "")
            bruto_parts.append("\n---\n")

        bruto = "\n".join(bruto_parts)
        self._log_to_file("fase1_agregado_bruto.md", bruto)

        # CORREÇÃO CRÍTICA #1: Agregação HIERÁRQUICA por batch
        # Em vez de truncar, agregar cada batch separadamente e depois consolidar
        self._reportar_progresso("fase1", 35, f"Agregador consolidando {len(extractor_configs)} extrações: {self.agregador_model}")

        if len(bruto) <= 60000:
            # Caso simples: cabe numa única chamada
            prompt_agregador = f"""EXTRAÇÕES DOS {len(extractor_configs)} MODELOS ESPECIALIZADOS (MODO BATCH - {len(pages)} páginas):

{bruto}

MISSÃO DO AGREGADOR:
Consolida estas {len(extractor_configs)} extrações numa única extração LOSSLESS.
Cada extrator (E1-E{len(extractor_configs)}) trouxe perspectiva diferente com modelos distintos.

CRÍTICO: Preservar TODOS os dados numéricos, datas, valores e referências de TODOS os extratores.
Área do Direito: {area}

NOTA: As extrações foram feitas por página. Mantém referências de página quando relevante."""

            agregador_result = self._call_llm(
                model=self.agregador_model,
                prompt=prompt_agregador,
                system_prompt=self._build_system_agregador(len(extractor_configs)),
                role_name="agregador",
            )
            consolidado = f"# EXTRAÇÃO CONSOLIDADA (AGREGADOR: {self.agregador_model})\n\n{agregador_result.conteudo}"

        else:
            # AGREGAÇÃO HIERÁRQUICA: processar batches separadamente
            logger.info(f"Agregação hierárquica necessária: {len(bruto)} chars > 60000")
            self._reportar_progresso("fase1", 31, "Agregação hierárquica por batches...")

            # Dividir os resultados dos extratores por batch
            batch_consolidados = []

            for batch_idx, batch in enumerate(batches):
                batch_pages = [p["page_num"] for p in batch]
                self._reportar_progresso(
                    "fase1",
                    32 + (batch_idx * 5 // len(batches)),
                    f"Agregando batch {batch_idx + 1}/{len(batches)} (pgs {batch_pages[0]}-{batch_pages[-1]})"
                )

                # Construir bruto deste batch
                batch_bruto_parts = [f"# BATCH {batch_idx + 1} (Páginas {batch_pages[0]}-{batch_pages[-1]})\n"]

                for i, r in enumerate(resultados):
                    # Filtrar conteúdo deste batch do extrator
                    batch_marker = f"## Batch {batch_idx + 1}"
                    content = r.conteudo

                    # Procurar secção deste batch
                    if batch_marker in content:
                        start = content.find(batch_marker)
                        end = content.find("## Batch ", start + len(batch_marker))
                        if end == -1:
                            batch_content = content[start:]
                        else:
                            batch_content = content[start:end]
                    else:
                        # Fallback: usar todo conteúdo (para batch único)
                        batch_content = content

                    batch_bruto_parts.append(f"\n### [EXTRATOR {i+1}: {r.modelo}]\n")
                    batch_bruto_parts.append(batch_content)
                    batch_bruto_parts.append("\n---\n")

                batch_bruto = "\n".join(batch_bruto_parts)

                # Agregar este batch
                prompt_batch = f"""EXTRAÇÕES DOS {len(extractor_configs)} MODELOS - BATCH {batch_idx + 1}/{len(batches)}
Páginas: {batch_pages[0]} a {batch_pages[-1]}

{batch_bruto}

Consolida estas extrações do BATCH {batch_idx + 1} numa extração LOSSLESS.
Área do Direito: {area}

IMPORTANTE: Mantém referências de página específicas."""

                batch_result = self._call_llm(
                    model=self.agregador_model,
                    prompt=prompt_batch,
                    system_prompt=self._build_system_agregador(len(extractor_configs)),
                    role_name=f"agregador_batch_{batch_idx + 1}",
                )

                batch_consolidados.append({
                    "batch": batch_idx + 1,
                    "pages": f"{batch_pages[0]}-{batch_pages[-1]}",
                    "consolidado": batch_result.conteudo,
                })

                # Log do batch
                self._log_to_file(
                    f"fase1_agregado_batch_{batch_idx + 1}.md",
                    f"# BATCH {batch_idx + 1} (Páginas {batch_pages[0]}-{batch_pages[-1]})\n\n{batch_result.conteudo}"
                )

            # AGREGAÇÃO FINAL: consolidar todos os batches
            self._reportar_progresso("fase1", 38, "Consolidação final de todos os batches...")

            batches_concat = "\n\n".join([
                f"## BATCH {b['batch']} (Páginas {b['pages']})\n\n{b['consolidado']}\n---"
                for b in batch_consolidados
            ])

            prompt_final = f"""CONSOLIDAÇÃO FINAL DE TODOS OS BATCHES
Total de batches: {len(batch_consolidados)}
Total de páginas: {len(pages)}
Área do Direito: {area}

{batches_concat}

TAREFA: Consolida TODOS os batches numa extração FINAL LOSSLESS.
- Mantém TODA informação única de cada batch
- Remove apenas duplicados EXATOS
- Preserva referências de página"""

            final_result = self._call_llm(
                model=self.agregador_model,
                prompt=prompt_final,
                system_prompt=self._build_system_agregador(len(extractor_configs)),
                role_name="agregador_final",
            )

            consolidado = f"# EXTRAÇÃO CONSOLIDADA (AGREGADOR HIERÁRQUICO: {self.agregador_model})\n"
            consolidado += f"## Processado em {len(batch_consolidados)} batches\n\n"
            consolidado += final_result.conteudo

        self._log_to_file("fase1_agregado_consolidado.md", consolidado)
        self._log_to_file("fase1_agregado.md", consolidado)

        return resultados, bruto, consolidado

    def _fase2_auditoria(self, agregado_fase1: str, area: str) -> tuple:
        """
        Fase 2: 4 Auditores LLM -> Consolidador LLM (LOSSLESS).
        NOTA: Auditores são CEGOS a perguntas do utilizador.

        Returns:
            tuple: (resultados, bruto, consolidado)
        """
        n_auditores = len(self.auditor_models)
        logger.info(f"Fase 2 - {n_auditores} Auditores: perguntas_count=0 (auditores sao cegos a perguntas)")
        self._reportar_progresso("fase2", 35, f"Iniciando auditoria com {n_auditores} LLMs...")

        # Carregar informação de cobertura se disponível
        coverage_info = ""
        if self._output_dir and USE_UNIFIED_PROVENANCE:
            coverage_path = self._output_dir / "fase1_coverage_report.json"
            if coverage_path.exists():
                try:
                    import json as _json
                    with open(coverage_path, 'r', encoding='utf-8') as f:
                        coverage_data = _json.load(f)

                    coverage_info = f"""

## RELATÓRIO DE COBERTURA DA EXTRAÇÃO
- **Total chars documento:** {coverage_data.get('total_chars', 0):,}
- **Chars cobertos:** {coverage_data.get('covered_chars', 0):,}
- **Cobertura:** {coverage_data.get('coverage_percent', 0):.1f}%
- **Completa:** {'SIM' if coverage_data.get('is_complete') else 'NÃO - VERIFICAR GAPS'}
- **Items extraídos:** {coverage_data.get('items_count', 0)}
"""
                    # Adicionar gaps se existirem
                    gaps = coverage_data.get('gaps', [])
                    if gaps:
                        coverage_info += "\n### GAPS NÃO COBERTOS (requer atenção!):\n"
                        for gap in gaps[:10]:  # Limitar a 10 gaps
                            coverage_info += f"- Chars [{gap['start']:,} - {gap['end']:,}] ({gap['length']:,} chars)\n"
                        if len(gaps) > 10:
                            coverage_info += f"- ... e mais {len(gaps) - 10} gaps\n"
                        coverage_info += "\n**INSTRUÇÕES ESPECIAIS:** Verifica se informação crítica pode estar nos gaps não cobertos.\n"

                    logger.info(f"Cobertura carregada: {coverage_data.get('coverage_percent', 0):.1f}%")
                except Exception as e:
                    logger.warning(f"Erro ao carregar cobertura: {e}")

        prompt_base = f"""EXTRAÇÃO A AUDITAR:
Área do Direito: {area}
{coverage_info}
{agregado_fase1}

Audita a extração acima, verificando completude, precisão e relevância jurídica.
{("ATENÇÃO: A cobertura não é 100%. Verifica os gaps reportados acima." if "NÃO - VERIFICAR GAPS" in coverage_info else "")}"""

        resultados = []
        for i, model in enumerate(self.auditor_models):
            auditor_id = f"A{i+1}"
            self._reportar_progresso("fase2", 40 + i * 5, f"Auditor {i+1}: {model}")
            # v4.0: A4 uses Devil's Advocate prompt
            aud_sys_prompt = self.SYSTEM_ADVOGADO_DIABO if auditor_id == "A4" else self.SYSTEM_AUDITOR

            # v5.0: Fallback para substitutos se primário falhar
            models_to_try = [model] + AUDITOR_SUBSTITUTES.get(auditor_id, [])
            resultado = None
            used_model = model
            for attempt_idx, try_model in enumerate(models_to_try):
                resultado = self._call_llm(
                    model=try_model,
                    prompt=prompt_base,
                    system_prompt=aud_sys_prompt,
                    role_name=f"auditor_{i+1}",
                )
                if resultado is not None and resultado.conteudo:
                    used_model = try_model
                    if attempt_idx > 0:
                        logger.warning(
                            f"[FALLBACK] {auditor_id} substituto {attempt_idx} ({try_model}) "
                            f"assumiu após falha do primário ({model})"
                        )
                    break
                else:
                    logger.warning(f"[FALLBACK] {auditor_id} modelo {try_model} falhou")
                    resultado = None

            if resultado is not None:
                resultados.append(resultado)
                self._log_to_file(f"fase2_auditor_{i+1}.md", f"# Auditor {i+1}: {used_model}\n\n{resultado.conteudo}")
            else:
                logger.error(f"✗ {auditor_id} falhou todos os {len(models_to_try)} modelos — ignorado")

        # Criar auditorias BRUTAS (concatenação simples)
        self._reportar_progresso("fase2", 53, "Criando auditorias brutas...")

        bruto_parts = ["# AUDITORIAS AGREGADAS (BRUTO)\n"]
        for i, r in enumerate(resultados):
            bruto_parts.append(f"\n## [AUDITOR {i+1}: {r.modelo}]\n")
            bruto_parts.append(r.conteudo)
            bruto_parts.append("\n---\n")

        bruto = "\n".join(bruto_parts)
        self._log_to_file("fase2_auditorias_brutas.md", bruto)

        # Chamar Consolidador LLM para consolidação LOSSLESS
        self._reportar_progresso("fase2", 55, f"Consolidador consolidando {n_auditores} auditorias: {self.chefe_model}")

        prompt_chefe = f"""AUDITORIAS DOS {n_auditores} MODELOS:

{bruto}

Consolida estas {n_auditores} auditorias numa única auditoria LOSSLESS.
Área do Direito: {area}"""

        # v5.0: Consolidador com fallback para substitutos
        models_to_try = [self.chefe_model] + CONSOLIDADOR_SUBSTITUTES
        chefe_result = None
        consolidador_used = self.chefe_model
        for attempt_idx, try_model in enumerate(models_to_try):
            chefe_result = self._call_llm(
                model=try_model,
                prompt=prompt_chefe,
                system_prompt=self.SYSTEM_CONSOLIDADOR,
                role_name="consolidador",
            )
            if chefe_result is not None and chefe_result.conteudo and chefe_result.conteudo.strip():
                consolidador_used = try_model
                if attempt_idx > 0:
                    logger.warning(
                        f"[FALLBACK] Consolidador substituto {attempt_idx} ({try_model}) "
                        f"assumiu após falha do primário ({self.chefe_model})"
                    )
                break
            else:
                logger.warning(f"[FALLBACK] Consolidador modelo {try_model} falhou")
                chefe_result = None

        if chefe_result is None or not chefe_result.conteudo:
            raise Exception("Consolidador falhou após todos os modelos. Pipeline abortado.")

        consolidado = f"# AUDITORIA CONSOLIDADA (CONSOLIDADOR: {consolidador_used})\n\n{chefe_result.conteudo}"
        self._log_to_file("fase2_consolidador_consolidado.md", consolidado)

        # Backwards compat: guardar também como fase2_chefe.md
        self._log_to_file("fase2_chefe.md", consolidado)

        return resultados, bruto, consolidado

    def _fase2_auditoria_unified(
        self,
        agregado_fase1: str,
        area: str,
        run_id: str,
        unified_result: Optional[Any] = None
    ) -> tuple:
        """
        Fase 2 UNIFICADA: 4 Auditores -> JSON estruturado com proveniência.

        Args:
            agregado_fase1: Markdown do agregado (para contexto)
            area: Área do direito
            run_id: ID do run
            unified_result: UnifiedExtractionResult estruturado (se disponível)

        Returns:
            tuple: (audit_reports: List[AuditReport], bruto_md, consolidado_md, chefe_report)
        """
        import json as json_module

        n_auditores = len(self.auditor_models)
        logger.info(f"[FASE2-UNIFIED] INICIANDO Fase 2 UNIFIED com {n_auditores} auditores")
        logger.info(f"[FASE2-UNIFIED] Output dir: {self._output_dir.absolute() if self._output_dir else 'None'}")
        self._reportar_progresso("fase2", 35, f"Auditoria JSON com {n_auditores} LLMs...")

        # NOVO: Carregar JSON estruturado da Fase 1 (fonte de verdade)
        agregado_json = None
        union_items_json = "[]"
        if self._output_dir and USE_UNIFIED_PROVENANCE:
            agregado_json_path = self._output_dir / "fase1_agregado_consolidado.json"
            if agregado_json_path.exists():
                try:
                    with open(agregado_json_path, 'r', encoding='utf-8') as f:
                        agregado_json = json_module.load(f)
                    # Extrair union_items para o prompt
                    union_items = agregado_json.get("union_items", [])
                    # Criar versão compacta para o prompt (apenas campos essenciais)
                    items_compact = []
                    for item in union_items:
                        items_compact.append({
                            "item_id": item.get("item_id"),
                            "item_type": item.get("item_type"),
                            "value": item.get("value_normalized"),
                            "page": item.get("source_spans", [{}])[0].get("page_num") if item.get("source_spans") else None,
                            "start_char": item.get("source_spans", [{}])[0].get("start_char") if item.get("source_spans") else None,
                            "end_char": item.get("source_spans", [{}])[0].get("end_char") if item.get("source_spans") else None,
                        })
                    union_items_json = json_module.dumps(items_compact, ensure_ascii=False, indent=2)
                    logger.info(f"[FASE2-UNIFIED] Carregados {len(union_items)} items estruturados da Fase 1")
                except Exception as e:
                    logger.warning(f"Erro ao carregar agregado JSON: {e}")

        # Carregar cobertura para contexto
        coverage_info = ""
        coverage_data = agregado_json.get("coverage_report", {}) if agregado_json else {}
        if coverage_data:
            coverage_info = f"""
## COBERTURA DA EXTRAÇÃO (do JSON estruturado)
- Total chars: {coverage_data.get('total_chars', 0):,}
- Cobertura: {coverage_data.get('coverage_percent', 0):.1f}%
- Páginas total: {coverage_data.get('pages_total', 'N/A')}
- Páginas ilegíveis: {coverage_data.get('pages_unreadable', 0)}
"""
        elif self._output_dir:
            # Fallback: carregar de ficheiro separado
            coverage_path = self._output_dir / "fase1_coverage_report.json"
            if coverage_path.exists():
                try:
                    with open(coverage_path, 'r', encoding='utf-8') as f:
                        coverage_data = json_module.load(f)
                    coverage_info = f"""
## COBERTURA DA EXTRAÇÃO
- Total chars: {coverage_data.get('total_chars', 0):,}
- Cobertura: {coverage_data.get('coverage_percent', 0):.1f}%
- Páginas total: {coverage_data.get('pages_total', 'N/A')}
- Páginas ilegíveis: {coverage_data.get('pages_unreadable', 0)}
"""
                except Exception as e:
                    logger.warning(f"Erro ao carregar cobertura: {e}")

        # Gerar canonical doc_id para rastreabilidade
        canonical_doc_id = ""
        if hasattr(self, '_documento') and self._documento:
            _fhash = getattr(self._documento, 'file_hash', '') or ''
            if _fhash:
                canonical_doc_id = f"doc_{_fhash[:12]}"

        # NOVO: Prompt inclui dados estruturados (evidence_item_ids)
        doc_id_line = f"\nDoc ID Canónico: {canonical_doc_id}" if canonical_doc_id else ""
        prompt_base = f"""EXTRAÇÃO A AUDITAR:
Área do Direito: {area}{doc_id_line}
{coverage_info}

## ITEMS EXTRAÍDOS (JSON ESTRUTURADO - usar evidence_item_ids nas citations!)
```json
{union_items_json}
```

## EXTRAÇÃO EM MARKDOWN (para contexto adicional)
{agregado_fase1}

INSTRUÇÕES:
1. Audita a extração acima
2. Para cada finding, CITA os evidence_item_ids relevantes do JSON
3. Inclui start_char/end_char/page_num nas citations
4. Retorna APENAS JSON no formato especificado."""

        # Processar cada auditor (PARALELO com retry)
        audit_reports: List[AuditReport] = []
        bruto_parts = ["# AUDITORIAS JSON AGREGADAS (BRUTO)\n"]

        # v4.0: A4-specific Devil's Advocate JSON prompt
        SYSTEM_A4_ADVOGADO_DIABO_JSON = """IMPORTANT: You MUST respond with ONLY valid JSON. No text before or after the JSON. No markdown code blocks. Just the raw JSON object starting with { and ending with }.

""" + PROMPT_ADVOGADO_DIABO + """

OUTPUT: Same JSON format as other auditors, plus include a "devils_advocate_conclusion" field ("errors_found" or "audit_clean") and a "challenges" array."""

        def _run_auditor(i, model):
            """Executa um auditor com fallback para substitutos. Thread-safe.

            v5.0: Se o modelo primário falha após retries, tenta substitutos:
              Sub 1 (mesma empresa) → Sub 2 (outra empresa)
            """
            auditor_id = f"A{i+1}"
            # v4.0: A4 uses Devil's Advocate prompt
            sys_prompt = SYSTEM_A4_ADVOGADO_DIABO_JSON if auditor_id == "A4" else self.SYSTEM_AUDITOR_JSON

            # v5.0: Lista de modelos a tentar (primário + substitutos)
            models_to_try = [model] + AUDITOR_SUBSTITUTES.get(auditor_id, [])

            resultado = None
            used_model = model
            for attempt_idx, try_model in enumerate(models_to_try):
                label = "primário" if attempt_idx == 0 else f"substituto {attempt_idx}"

                def _do_audit(_m=try_model):
                    return self._call_llm(
                        model=_m,
                        prompt=prompt_base,
                        system_prompt=sys_prompt,
                        role_name=f"auditor_{i+1}_json",
                    )

                resultado = _call_with_retry(_do_audit, func_name=f"Auditor-{auditor_id}")

                if resultado is not None and resultado.conteudo:
                    used_model = try_model
                    if attempt_idx > 0:
                        logger.warning(
                            f"[FALLBACK] {auditor_id} {label} ({try_model}) assumiu "
                            f"após falha do primário ({model})"
                        )
                    break
                else:
                    logger.warning(
                        f"[FALLBACK] {auditor_id} {label} ({try_model}) falhou — "
                        f"tentando próximo substituto..." if attempt_idx < len(models_to_try) - 1
                        else f"[FALLBACK] {auditor_id} todos os modelos falharam ({len(models_to_try)} tentativas)"
                    )
                    resultado = None

            if resultado is None or not resultado.conteudo:
                logger.error(f"✗ Auditor {auditor_id} falhou após {len(models_to_try)} modelos (primário + substitutos)")
                return None

            # Parsear JSON com fallback
            report = parse_audit_report(
                output=resultado.conteudo,
                auditor_id=auditor_id,
                model_name=used_model,
                run_id=run_id,
            )

            # Validação de integridade (se validator disponível)
            if hasattr(self, '_integrity_validator') and self._integrity_validator:
                report = self._integrity_validator.validate_and_annotate_audit(report)

            # Guardar JSON do auditor individual
            json_path = self._output_dir / f"fase2_auditor_{i+1}.json"
            try:
                with open(json_path, 'w', encoding='utf-8') as f:
                    json_module.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
                logger.info(f"[JSON-WRITE] Auditor {auditor_id} JSON guardado: {json_path.absolute()}")
            except Exception as e:
                logger.error(f"[JSON-WRITE-ERROR] Falha ao escrever auditor {auditor_id} JSON: {e}")

            # Guardar Markdown (renderizado do JSON)
            md_content = report.to_markdown()
            self._log_to_file(f"fase2_auditor_{i+1}.md", md_content)

            # FIX 2026-02-14: Só contar erros reais no log (não INTEGRITY_WARNING)
            n_real_errs = len([e for e in report.errors if not str(e).startswith("INTEGRITY_WARNING:")])
            n_integrity = len([e for e in report.errors if str(e).startswith("INTEGRITY_WARNING:")])
            logger.info(
                f"✓ Auditor {auditor_id}: {len(report.findings)} findings, "
                f"{n_real_errs} erros" + (f", {n_integrity} integrity_warnings" if n_integrity else "")
            )

            return {
                "auditor_id": auditor_id,
                "index": i,
                "model": used_model,
                "report": report,
                "md_content": md_content,
                "tokens_usados": resultado.tokens_usados if resultado else 0,
                "prompt_tokens": resultado.prompt_tokens if resultado else 0,
                "completion_tokens": resultado.completion_tokens if resultado else 0,
            }

        logger.info(f"[PARALELO] Lançando {n_auditores} auditores em paralelo...")
        _audit_lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=min(4, n_auditores)) as executor:
            futures = {}
            for i, model in enumerate(self.auditor_models):
                future = executor.submit(_run_auditor, i, model)
                futures[future] = f"A{i+1}"

            auditor_results = []
            for future in as_completed(futures):
                aid = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        with _audit_lock:
                            auditor_results.append(result)
                        logger.info(f"[PARALELO] {aid} concluído: {len(result['report'].findings)} findings")
                    else:
                        logger.warning(f"[PARALELO] {aid} falhou - ignorado")
                except Exception as exc:
                    if "Budget" in type(exc).__name__ or "Limit" in type(exc).__name__:
                        raise  # Re-raise budget/limit errors immediately
                    logger.error(f"[PARALELO] {aid} excepção: {exc}")

        # Ordenar por índice original para manter ordem determinística
        auditor_results.sort(key=lambda r: r["index"])

        for r in auditor_results:
            audit_reports.append(r["report"])
            bruto_parts.append(f"\n## [AUDITOR {r['auditor_id']}: {r['model']}]\n")
            bruto_parts.append(r["md_content"])
            bruto_parts.append("\n---\n")

        if len(audit_reports) < 2:
            raise Exception(
                f"Apenas {len(audit_reports)} auditores funcionaram (mínimo 2). "
                f"Pipeline abortado."
            )

        # ===== v4.0: A5 OPUS — AUDITOR SÉNIOR (APENAS ELITE) =====
        use_a5 = getattr(self, '_use_a5_opus', False)
        if use_a5 and len(audit_reports) >= 2:
            self._reportar_progresso("fase2", 50, "A5 Opus: Auditor Sénior (ELITE)...")
            logger.info("[ELITE] Executando A5 Opus — Auditor Sénior")

            # Preparar input com resultados dos A1-A4
            a1_a4_summary = "\n\n".join([
                f"## Auditor {r.auditor_id} ({r.model_name}):\n"
                f"Findings: {len(r.findings)}\n"
                f"{r.to_markdown()[:3000]}\n---"
                for r in audit_reports
            ])

            a5_prompt = f"""EVIDENCE MAP (from Phase 1):
{prompt_base}

AUDITOR FINDINGS (A1-A4):
{a1_a4_summary}

Review the other auditors' work. Validate their findings. Catch what they missed.
You are the final quality gate before the Judges."""

            a5_sys_prompt = """IMPORTANT: You MUST respond with ONLY valid JSON. No text before or after the JSON.

""" + PROMPT_AUDITOR_SENIOR

            try:
                a5_result = self._call_llm(
                    model="anthropic/claude-opus-4.6",
                    prompt=a5_prompt,
                    system_prompt=a5_sys_prompt,
                    role_name="auditor_5_json_senior",
                )

                if a5_result and a5_result.conteudo:
                    a5_report = parse_audit_report(
                        output=a5_result.conteudo,
                        auditor_id="A5",
                        model_name="anthropic/claude-opus-4.6",
                        run_id=run_id,
                    )
                    if hasattr(self, '_integrity_validator') and self._integrity_validator:
                        a5_report = self._integrity_validator.validate_and_annotate_audit(a5_report)

                    audit_reports.append(a5_report)
                    bruto_parts.append(f"\n## [AUDITOR A5 (SÉNIOR): anthropic/claude-opus-4.6]\n")
                    bruto_parts.append(a5_report.to_markdown())
                    bruto_parts.append("\n---\n")

                    # Guardar JSON A5
                    a5_json_path = self._output_dir / "fase2_auditor_5_senior.json"
                    with open(a5_json_path, 'w', encoding='utf-8') as f:
                        json_module.dump(a5_report.to_dict(), f, ensure_ascii=False, indent=2)

                    logger.info(f"[ELITE] A5 Opus: {len(a5_report.findings)} findings")
                else:
                    logger.warning("[ELITE] A5 Opus retornou vazio — ignorado")
            except Exception as e:
                if "Budget" in type(e).__name__ or "Limit" in type(e).__name__:
                    raise  # Re-raise budget/limit errors immediately
                logger.error(f"[ELITE] A5 Opus falhou: {e} — continuando sem A5")

        bruto = "\n".join(bruto_parts)
        self._log_to_file("fase2_auditorias_brutas.md", bruto)

        # Consolidar auditorias (Consolidador JSON)
        self._reportar_progresso("fase2", 55, f"Consolidador consolidando (JSON): {self.chefe_model}")

        # Preparar JSON dos auditores para o Consolidador
        auditors_json_str = json_module.dumps(
            [r.to_dict() for r in audit_reports],
            ensure_ascii=False,
            indent=2
        )

        prompt_chefe_json = f"""AUDITORIAS DOS {n_auditores} AUDITORES (JSON):

```json
{auditors_json_str}
```

AUDITORIAS EM MARKDOWN (para contexto):

{bruto}

Consolida estas {n_auditores} auditorias num ÚNICO relatório JSON LOSSLESS.
Área do Direito: {area}

CRITICAL: Respond with ONLY the JSON object. Do NOT include any text, explanation, or markdown before or after the JSON. Start your response with {{ and end with }}."""

        # v5.0: Consolidador com fallback para substitutos
        models_to_try = [self.chefe_model] + CONSOLIDADOR_SUBSTITUTES
        chefe_result = None
        consolidador_used_model = self.chefe_model
        for attempt_idx, try_model in enumerate(models_to_try):
            label = "primário" if attempt_idx == 0 else f"substituto {attempt_idx}"
            chefe_result = self._call_llm(
                model=try_model,
                prompt=prompt_chefe_json,
                system_prompt=self.SYSTEM_CONSOLIDADOR_JSON,
                role_name="consolidador_json",
            )
            if chefe_result is not None and chefe_result.conteudo and chefe_result.conteudo.strip():
                consolidador_used_model = try_model
                if attempt_idx > 0:
                    logger.warning(
                        f"[FALLBACK] Consolidador {label} ({try_model}) assumiu "
                        f"após falha do primário ({self.chefe_model})"
                    )
                break
            else:
                logger.warning(
                    f"[FALLBACK] Consolidador {label} ({try_model}) falhou"
                    + (" — tentando próximo substituto..." if attempt_idx < len(models_to_try) - 1 else "")
                )
                chefe_result = None

        if chefe_result is None or not chefe_result.conteudo:
            raise Exception(
                f"Consolidador falhou após {len(models_to_try)} modelos (primário + substitutos). "
                f"Pipeline abortado."
            )

        # Parsear JSON do Consolidador com soft-fail
        chefe_report = parse_chefe_report(
            output=chefe_result.conteudo,
            model_name=consolidador_used_model,
            run_id=run_id,
        )

        # CRITICO: Guardar JSON do Consolidador (fonte de verdade)
        chefe_json_path = self._output_dir / "fase2_consolidador_consolidado.json"
        logger.info(f"[JSON-WRITE] Escrevendo fase2_consolidador_consolidado.json em: {chefe_json_path.absolute()}")
        try:
            with open(chefe_json_path, 'w', encoding='utf-8') as f:
                json_module.dump(chefe_report.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"✓ Consolidador JSON guardado: {chefe_json_path.absolute()}")
        except Exception as e:
            logger.error(f"[JSON-WRITE-ERROR] Falha ao escrever fase2_consolidador_consolidado.json: {e}")

        # Gerar Markdown (derivado do JSON)
        consolidado_md = chefe_report.to_markdown()
        self._log_to_file("fase2_consolidador_consolidado.md", consolidado_md)
        self._log_to_file("fase2_chefe.md", consolidado_md)

        # Para compatibilidade com Fase 3, criar string consolidada
        consolidado = f"# AUDITORIA CONSOLIDADA (CONSOLIDADOR: {consolidador_used_model})\n\n"
        consolidado += consolidado_md

        logger.info(
            f"✓ Consolidador consolidou: {len(chefe_report.consolidated_findings)} findings, "
            f"{len(chefe_report.divergences)} divergências, {len(chefe_report.errors)} erros"
        )

        # Guardar todos os reports JSON num ficheiro
        all_reports_path = self._output_dir / "fase2_all_audit_reports.json"
        with open(all_reports_path, 'w', encoding='utf-8') as f:
            json_module.dump(
                [r.to_dict() for r in audit_reports],
                f, ensure_ascii=False, indent=2
            )

        # =====================================================================
        # CONSENSUS ENGINE: Validação determinística + consenso adaptativo
        # =====================================================================
        try:
            from src.pipeline.consensus_engine import run_consensus_engine

            # Construir page_texts e page_offsets a partir dos dados disponíveis
            page_texts_map = {}
            page_offsets_map = {}
            canonical_text = getattr(self, '_document_text', '') or ''
            file_hash = ""

            if hasattr(self, '_page_mapper') and self._page_mapper:
                # Usar page_mapper boundaries para obter textos por página
                for boundary in self._page_mapper.boundaries:
                    page_texts_map[boundary.page_num] = canonical_text[boundary.start_char:boundary.end_char]
                    page_offsets_map[boundary.page_num] = boundary.start_char

            # Tentar obter file_hash do documento
            if hasattr(self, '_documento') and self._documento:
                file_hash = getattr(self._documento, 'file_hash', '') or ''

            if canonical_text and page_texts_map:
                self._reportar_progresso("fase2", 58, "Consensus Engine: validação determinística...")
                consensus_result = run_consensus_engine(
                    audit_reports=audit_reports,
                    canonical_text=canonical_text,
                    page_texts=page_texts_map,
                    page_offsets=page_offsets_map,
                    file_hash=file_hash,
                    output_dir=self._output_dir,
                )
                logger.info(
                    f"[CONSENSUS] Completo: "
                    f"Citations {consensus_result['citation_validation']['valid']}/{consensus_result['citation_validation']['total']} válidas | "
                    f"Fases activas: A={'SIM'} B={'SIM' if consensus_result['phases_active']['B'] else 'NÃO'} C={'SIM' if consensus_result['phases_active']['C'] else 'NÃO'}"
                )
            else:
                logger.warning("[CONSENSUS] Dados insuficientes para consensus engine (sem texto canónico ou page map)")

        except Exception as e:
            logger.error(f"[CONSENSUS] Erro no consensus engine (não-bloqueante): {e}")
            import traceback
            traceback.print_exc()

        consolidado = self._aplicar_rlm(consolidado, "auditoria")

        return audit_reports, bruto, consolidado, chefe_report

    def _fase3_relatoria_unified(
        self,
        chefe_fase2: str,
        area: str,
        perguntas: List[str],
        run_id: str
    ) -> tuple:
        """
        Fase 3 UNIFICADA: 3 Relatores -> JSON estruturado.

        Returns:
            tuple: (judge_opinions: List[JudgeOpinion], respostas_qa: List[Dict])
        """
        import json as json_module

        n_perguntas = len(perguntas)
        logger.info(f"Fase 3 UNIFIED - 3 Relatores JSON, {n_perguntas} perguntas")
        self._reportar_progresso("fase3", 60, f"Relatoria JSON com 3 LLMs...")

        # Bloco Q&A se houver perguntas
        bloco_qa = ""
        if perguntas:
            perguntas_formatadas = "\n".join([f"{i+1}. {p}" for i, p in enumerate(perguntas)])
            bloco_qa = f"""

## PERGUNTAS DO UTILIZADOR (incluir em qa_responses)

{perguntas_formatadas}
"""

        prompt_base = f"""ANÁLISE AUDITADA:
Área: {area}

{chefe_fase2}

Emite parecer jurídico em JSON.{bloco_qa}

CRITICAL: Respond with ONLY the JSON object. Do NOT include any text, explanation, or markdown before or after the JSON. Start your response with {{ and end with }}."""

        # Escolher prompt
        system_prompt = self.SYSTEM_RELATOR_JSON_QA if perguntas else self.SYSTEM_RELATOR_JSON

        judge_opinions: List[JudgeOpinion] = []
        respostas_qa = []

        def _run_judge(i, model):
            """Executa um relator com fallback para substitutos. Thread-safe.

            v5.0: Se o modelo primário falha após retries, tenta substitutos:
              Sub 1 (mesma empresa) → Sub 2 (outra empresa)
            """
            judge_id = f"J{i+1}"

            # v5.0: Lista de modelos a tentar (primário + substitutos)
            models_to_try = [model] + JUDGE_SUBSTITUTES.get(judge_id, [])

            resultado = None
            used_model = model
            for attempt_idx, try_model in enumerate(models_to_try):
                label = "primário" if attempt_idx == 0 else f"substituto {attempt_idx}"

                def _do_judge(_m=try_model):
                    return self._call_llm(
                        model=_m,
                        prompt=prompt_base,
                        system_prompt=system_prompt,
                        role_name=f"relator_{i+1}_json",
                    )

                resultado = _call_with_retry(_do_judge, func_name=f"Relator-{judge_id}")

                if resultado is not None and resultado.conteudo:
                    used_model = try_model
                    if attempt_idx > 0:
                        logger.warning(
                            f"[FALLBACK] {judge_id} {label} ({try_model}) assumiu "
                            f"após falha do primário ({model})"
                        )
                    break
                else:
                    logger.warning(
                        f"[FALLBACK] {judge_id} {label} ({try_model}) falhou — "
                        f"tentando próximo substituto..." if attempt_idx < len(models_to_try) - 1
                        else f"[FALLBACK] {judge_id} todos os modelos falharam ({len(models_to_try)} tentativas)"
                    )
                    resultado = None

            if resultado is None or not resultado.conteudo:
                logger.error(f"✗ Relator {judge_id} falhou após {len(models_to_try)} modelos (primário + substitutos)")
                return None

            # Parsear JSON
            opinion = parse_judge_opinion(
                output=resultado.conteudo,
                judge_id=judge_id,
                model_name=used_model,
                run_id=run_id,
            )

            # Validação de integridade (se validator disponível)
            if hasattr(self, '_integrity_validator') and self._integrity_validator:
                opinion = self._integrity_validator.validate_and_annotate_judge(opinion)

            # Guardar JSON
            json_path = self._output_dir / f"fase3_relator_{i+1}.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                json_module.dump(opinion.to_dict(), f, ensure_ascii=False, indent=2)

            # Guardar Markdown
            md_content = opinion.to_markdown()
            self._log_to_file(f"fase3_relator_{i+1}.md", md_content)

            # FIX 2026-02-14: Só contar erros reais no log (não INTEGRITY_WARNING)
            n_real_errs_j = len([e for e in opinion.errors if not str(e).startswith("INTEGRITY_WARNING:")])
            n_integrity_j = len([e for e in opinion.errors if str(e).startswith("INTEGRITY_WARNING:")])
            logger.info(
                f"✓ Relator {judge_id}: {opinion.recommendation.value}, "
                f"{len(opinion.decision_points)} pontos, {n_real_errs_j} erros"
                + (f", {n_integrity_j} integrity_warnings" if n_integrity_j else "")
            )

            return {
                "judge_id": judge_id,
                "index": i,
                "model": used_model,
                "opinion": opinion,
                "md_content": md_content,
                "tokens_usados": resultado.tokens_usados if resultado else 0,
                "prompt_tokens": resultado.prompt_tokens if resultado else 0,
                "completion_tokens": resultado.completion_tokens if resultado else 0,
            }

        logger.info(f"[PARALELO] Lançando {len(self.relator_models)} relatores em paralelo...")
        _judge_lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=min(3, len(self.relator_models))) as executor:
            futures = {}
            for i, model in enumerate(self.relator_models):
                future = executor.submit(_run_judge, i, model)
                futures[future] = f"J{i+1}"

            judge_results = []
            for future in as_completed(futures):
                jid = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        with _judge_lock:
                            judge_results.append(result)
                        logger.info(f"[PARALELO] {jid} concluído: {result['opinion'].recommendation.value}")
                    else:
                        logger.warning(f"[PARALELO] {jid} falhou - ignorado")
                except Exception as exc:
                    logger.error(f"[PARALELO] {jid} excepção: {exc}")

        # Ordenar por índice original para manter ordem determinística
        judge_results.sort(key=lambda r: r["index"])

        for r in judge_results:
            judge_opinions.append(r["opinion"])
            respostas_qa.append({
                "juiz": r["index"] + 1,
                "modelo": r["model"],
                "opinion": r["opinion"].to_dict(),
                "resposta": r["md_content"],
            })

        if len(judge_opinions) < 1:
            raise Exception("Nenhum relator funcionou (mínimo 1). Pipeline abortado.")

        # Guardar Q&A se houver perguntas
        if perguntas:
            qa_content = self._gerar_qa_juizes(perguntas, respostas_qa)
            self._log_to_file("fase3_qa_respostas.md", qa_content)

        # Guardar todos os opinions JSON
        all_opinions_path = self._output_dir / "fase3_all_judge_opinions.json"
        with open(all_opinions_path, 'w', encoding='utf-8') as f:
            json_module.dump(
                [o.to_dict() for o in judge_opinions],
                f, ensure_ascii=False, indent=2
            )

        return judge_opinions, respostas_qa

    def _fase4_presidente_unified(
        self,
        judge_opinions: List[JudgeOpinion],
        perguntas: List[str],
        respostas_qa: List[Dict],
        run_id: str
    ) -> FinalDecision:
        """
        Fase 4 UNIFICADA: Conselheiro-Mor -> JSON FinalDecision.

        Returns:
            FinalDecision com parecer e Q&A consolidado
        """
        import json as json_module

        n_perguntas = len(perguntas)
        logger.info(f"Fase 4 UNIFIED - Conselheiro-Mor JSON, {n_perguntas} perguntas")
        self._reportar_progresso("fase4", 80, f"Conselheiro-Mor JSON: {self.presidente_model}")

        # Concatenar pareceres
        pareceres_concat = "\n\n".join([
            f"## RELATOR {i+1} ({o.model_name})\n"
            f"Recomendação: {o.recommendation.value}\n"
            f"Confiança média: {sum(float(p.confidence) for p in o.decision_points) / len(o.decision_points) if o.decision_points else 0:.0%}\n"
            f"{o.to_markdown()}\n---"
            for i, o in enumerate(judge_opinions)
        ])

        # Bloco Q&A
        bloco_qa = ""
        if perguntas:
            perguntas_fmt = "\n".join([f"{i+1}. {p}" for i, p in enumerate(perguntas)])
            respostas_fmt = "\n\n".join([
                f"### Relator {r['juiz']} ({r['modelo']}):\n{r.get('opinion', {})}"
                for r in respostas_qa if r.get('opinion')
            ])
            bloco_qa = f"""

## Q&A PARA CONSOLIDAR

### Perguntas:
{perguntas_fmt}

### Respostas dos Relatores:
{respostas_fmt}
"""

        prompt = f"""PARECERES DOS 3 RELATORES:

{pareceres_concat}

Emite PARECER FINAL em JSON.{bloco_qa}

CRITICAL: Respond with ONLY the JSON object. Do NOT include any text, explanation, or markdown before or after the JSON. Start your response with {{ and end with }}."""

        system_prompt = self.SYSTEM_CONSELHEIRO_JSON_QA if perguntas else self.SYSTEM_CONSELHEIRO_JSON

        # v5.0: Presidente com fallback para substitutos (indexados pelo modelo)
        models_to_try = [self.presidente_model] + PRESIDENTE_SUBSTITUTES.get(self.presidente_model, [])
        resultado = None
        modelo_usado = self.presidente_model
        for attempt_idx, try_model in enumerate(models_to_try):
            label = "primário" if attempt_idx == 0 else f"substituto {attempt_idx}"
            resultado = self._call_llm(
                model=try_model,
                prompt=prompt,
                system_prompt=system_prompt,
                role_name="presidente_json" if attempt_idx == 0 else f"presidente_json_fallback_{attempt_idx}",
            )
            if resultado is not None and resultado.conteudo and resultado.conteudo.strip():
                modelo_usado = try_model
                if attempt_idx > 0:
                    logger.warning(
                        f"[PRESIDENTE-FALLBACK] {label} ({try_model}) assumiu "
                        f"após falha do primário ({self.presidente_model})"
                    )
                break
            else:
                logger.warning(
                    f"[PRESIDENTE-FALLBACK] {label} ({try_model}) falhou"
                    + (" — tentando próximo substituto..." if attempt_idx < len(models_to_try) - 1 else "")
                )
                resultado = None

        if resultado is None or not resultado.conteudo:
            raise Exception(
                f"Presidente falhou após {len(models_to_try)} modelos. Pipeline abortado."
            )

        # Parsear JSON
        decision = parse_final_decision(
            output=resultado.conteudo,
            model_name=modelo_usado,
            run_id=run_id,
        )

        # Validação de integridade (se validator disponível)
        if hasattr(self, '_integrity_validator') and self._integrity_validator:
            decision = self._integrity_validator.validate_and_annotate_decision(decision)

            # Guardar relatório de integridade
            try:
                self._integrity_validator.save_report(self._output_dir)
                logger.info("✓ Relatório de integridade guardado")
            except Exception as e:
                logger.warning(f"Erro ao guardar relatório de integridade: {e}")

        # Adicionar info de consulta
        decision.judges_consulted = [f"J{i+1}" for i in range(len(judge_opinions))]
        decision.auditors_consulted = [f"A{i+1}" for i in range(len(self.auditor_models))]

        # Guardar JSON (fonte de verdade da Fase 4)
        json_path = self._output_dir / "fase4_decisao_final.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json_module.dump(decision.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"[JSON-WRITE] fase4_decisao_final.json guardado: {json_path.absolute()}")

        # Gerar e guardar Markdown
        md_content = decision.generate_markdown()
        self._log_to_file("fase4_conselheiro.md", md_content)

        # Q&A final
        if perguntas:
            qa_final = self._gerar_qa_final(perguntas, md_content)
            self._log_to_file("fase4_qa_final.md", qa_final)

        logger.info(
            f"✓ Conselheiro-Mor: {decision.decision_type.value}, "
            f"confiança {decision.confidence:.0%}, {len(decision.errors)} erros"
        )

        return decision

    def _fase3_relatoria(self, chefe_fase2: str, area: str, perguntas: List[str]) -> tuple:
        """
        Fase 3: 3 Relatores LLM -> Parecer + Q&A.
        NOTA: Relatores RECEBEM as perguntas do utilizador.
        """
        n_perguntas = len(perguntas)
        logger.info(f"Fase 3 - Relatores: perguntas_count={n_perguntas}")
        self._reportar_progresso("fase3", 60, f"Iniciando relatoria com 3 LLMs... ({n_perguntas} perguntas)")

        # Construir bloco de perguntas se houver
        bloco_qa = ""
        if perguntas:
            perguntas_formatadas = "\n".join([f"{i+1}. {p}" for i, p in enumerate(perguntas)])
            bloco_qa = f"""

## PERGUNTAS DO UTILIZADOR

{perguntas_formatadas}

**Instruções para Q&A:**
- Responda a cada pergunta numerada
- Base-se nos documentos e legislação portuguesa
- Marque claramente cada resposta por número
- Se não tiver certeza, marque como "não confirmado"
"""

        prompt_base = f"""ANÁLISE AUDITADA DO CASO:
Área do Direito: {area}

{chefe_fase2}

Com base na análise acima, emite o teu parecer jurídico fundamentado.{bloco_qa}"""

        # Escolher system prompt apropriado
        system_prompt = self.SYSTEM_RELATOR_QA if perguntas else self.SYSTEM_RELATOR

        resultados = []
        respostas_qa = []

        for i, model in enumerate(self.relator_models):
            judge_id = f"J{i+1}"
            self._reportar_progresso("fase3", 65 + i * 5, f"Relator {i+1}: {model}")

            # v5.0: Fallback para substitutos se primário falhar
            models_to_try = [model] + JUDGE_SUBSTITUTES.get(judge_id, [])
            resultado = None
            used_model = model
            for attempt_idx, try_model in enumerate(models_to_try):
                resultado = self._call_llm(
                    model=try_model,
                    prompt=prompt_base,
                    system_prompt=system_prompt,
                    role_name=f"relator_{i+1}",
                )
                if resultado is not None and resultado.conteudo:
                    used_model = try_model
                    if attempt_idx > 0:
                        logger.warning(
                            f"[FALLBACK] {judge_id} substituto {attempt_idx} ({try_model}) "
                            f"assumiu após falha do primário ({model})"
                        )
                    break
                else:
                    logger.warning(f"[FALLBACK] {judge_id} modelo {try_model} falhou")
                    resultado = None

            if resultado is not None:
                resultados.append(resultado)
                respostas_qa.append({
                    "juiz": i + 1,
                    "modelo": used_model,
                    "resposta": resultado.conteudo
                })
                self._log_to_file(f"fase3_relator_{i+1}.md", f"# Relator {i+1}: {used_model}\n\n{resultado.conteudo}")
            else:
                logger.error(f"✗ {judge_id} falhou todos os {len(models_to_try)} modelos — ignorado")

        # Guardar ficheiro Q&A dos relatores (se houver perguntas)
        if perguntas:
            qa_content = self._gerar_qa_juizes(perguntas, respostas_qa)
            self._log_to_file("fase3_qa_respostas.md", qa_content)

        return resultados, respostas_qa

    def _fase4_presidente(self, pareceres: List[FaseResult], perguntas: List[str], respostas_qa: List[Dict]) -> str:
        """
        Fase 4: Conselheiro-Mor verifica + consolida Q&A.
        NOTA: Conselheiro-Mor RECEBE as perguntas e respostas dos relatores.
        """
        n_perguntas = len(perguntas)
        logger.info(f"Fase 4 - Conselheiro-Mor: perguntas_count={n_perguntas}")
        self._reportar_progresso("fase4", 80, f"Conselheiro-Mor verificando: {self.presidente_model}")

        # Concatenar pareceres
        pareceres_concat = "\n\n".join([
            f"## [RELATOR {i+1}: {r.modelo}]\n{r.conteudo}\n---"
            for i, r in enumerate(pareceres)
        ])

        # Construir bloco Q&A para conselheiro-mor
        bloco_qa = ""
        if perguntas:
            perguntas_formatadas = "\n".join([f"{i+1}. {p}" for i, p in enumerate(perguntas)])
            respostas_formatadas = "\n\n".join([
                f"### Relator {r['juiz']} ({r['modelo']}):\n{r['resposta']}"
                for r in respostas_qa
            ])
            bloco_qa = f"""

## CONSOLIDAÇÃO DE RESPOSTAS Q&A

### PERGUNTAS ORIGINAIS:
{perguntas_formatadas}

### RESPOSTAS DOS 3 RELATORES:
{respostas_formatadas}

**Instruções para consolidação Q&A:**
- Para cada pergunta, consolide as 3 respostas
- Elimine contradições
- Forneça resposta final clara e fundamentada
- Numere as respostas finais
"""

        prompt_presidente = f"""PARECERES DOS RELATORES:

{pareceres_concat}

Analisa os pareceres, verifica as citações legais, e emite o PARECER FINAL.{bloco_qa}"""

        # Escolher system prompt apropriado
        system_prompt = self.SYSTEM_CONSELHEIRO_QA if perguntas else self.SYSTEM_CONSELHEIRO

        # v5.0: Presidente com fallback para substitutos (indexados pelo modelo)
        models_to_try = [self.presidente_model] + PRESIDENTE_SUBSTITUTES.get(self.presidente_model, [])
        presidente_result = None
        modelo_usado = self.presidente_model
        for attempt_idx, try_model in enumerate(models_to_try):
            presidente_result = self._call_llm(
                model=try_model,
                prompt=prompt_presidente,
                system_prompt=system_prompt,
                role_name="presidente" if attempt_idx == 0 else f"presidente_fallback_{attempt_idx}",
            )
            if presidente_result is not None and presidente_result.conteudo and presidente_result.conteudo.strip():
                modelo_usado = try_model
                if attempt_idx > 0:
                    logger.warning(
                        f"[PRESIDENTE-FALLBACK] substituto {attempt_idx} ({try_model}) "
                        f"assumiu após falha do primário ({self.presidente_model})"
                    )
                break
            else:
                logger.warning(f"[PRESIDENTE-FALLBACK] modelo {try_model} falhou")
                presidente_result = None

        if presidente_result is None or not presidente_result.conteudo:
            raise Exception("Presidente falhou após todos os modelos. Pipeline abortado.")

        self._log_to_file("fase4_conselheiro.md", f"# CONSELHEIRO-MOR: {modelo_usado}\n\n{presidente_result.conteudo}")

        # Guardar ficheiro Q&A final (se houver perguntas)
        if perguntas:
            qa_final = self._gerar_qa_final(perguntas, presidente_result.conteudo)
            self._log_to_file("fase4_qa_final.md", qa_final)

        return presidente_result.conteudo

    def _gerar_qa_juizes(self, perguntas: List[str], respostas_qa: List[Dict]) -> str:
        """Gera ficheiro markdown com respostas Q&A dos relatores."""
        linhas = [
            "# RESPOSTAS Q&A DOS RELATORES",
            "",
            "## Perguntas do Utilizador",
            "",
        ]

        for i, p in enumerate(perguntas, 1):
            linhas.append(f"{i}. {p}")

        linhas.append("")
        linhas.append("---")
        linhas.append("")

        for r in respostas_qa:
            linhas.append(f"## Relator {r['juiz']} ({r['modelo']})")
            linhas.append("")
            linhas.append(r['resposta'])
            linhas.append("")
            linhas.append("---")
            linhas.append("")

        return "\n".join(linhas)

    def _gerar_qa_final(self, perguntas: List[str], resposta_presidente: str) -> str:
        """Gera ficheiro markdown com Q&A consolidado pelo Conselheiro-Mor."""
        linhas = [
            "# RESPOSTAS FINAIS (CONSOLIDADO CONSELHEIRO-MOR)",
            "",
            "## Perguntas",
            "",
        ]

        for i, p in enumerate(perguntas, 1):
            linhas.append(f"{i}. {p}")

        linhas.append("")
        linhas.append("---")
        linhas.append("")
        linhas.append("## Respostas Consolidadas")
        linhas.append("")
        linhas.append(resposta_presidente)

        return "\n".join(linhas)

    # Meses em português → número (para extracção de data dos factos)
    _MESES_PT = {
        "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
        "abril": 4, "maio": 5, "junho": 6, "julho": 7,
        "agosto": 8, "setembro": 9, "outubro": 10,
        "novembro": 11, "dezembro": 12,
        # Abreviaturas comuns em documentos legais
        "jan": 1, "fev": 2, "mar": 3, "abr": 4,
        "mai": 5, "jun": 6, "jul": 7, "ago": 8,
        "set": 9, "out": 10, "nov": 11, "dez": 12,
    }

    # Palavras-chave que indicam uma data dos factos (vs data de legislação)
    _CONTEXTO_DATA_FACTOS = re.compile(
        r"(?:data\s+dos\s+factos|datado\s+de|celebrado\s+em|"
        r"ocorrido\s+em|no\s+dia|em\s+data\s+de|"
        r"outorgado\s+em|assinado\s+em|praticado\s+em|"
        r"aconteceu\s+em|sucedeu\s+em|verificou.se\s+em)",
        re.IGNORECASE,
    )

    def _extrair_data_factos(self, texto: str) -> Optional[datetime]:
        """
        Extrai automaticamente a data dos factos de um documento legal português.

        Estratégia em duas fases:
        1. Procura datas com contexto explícito ("celebrado em", "data dos factos", etc.)
        2. Se não encontrar, usa todas as datas do documento mas filtra datas de legislação

        Se nenhuma data: retorna None (verificar só versão actual).
        """
        current_year = datetime.now().year

        def _parse_date_ddmmyyyy(match) -> Optional[datetime]:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if 1980 <= year <= current_year:
                try:
                    return datetime(year, month, day)
                except ValueError:
                    pass
            return None

        def _parse_date_nome_mes(match) -> Optional[datetime]:
            day = int(match.group(1))
            month_name = match.group(2).lower().rstrip(".")
            year = int(match.group(3))
            month = self._MESES_PT.get(month_name)
            if month and 1980 <= year <= current_year:
                try:
                    return datetime(year, month, day)
                except ValueError:
                    pass
            return None

        # --- Fase 1: Datas com contexto explícito ---
        datas_contextuais: List[datetime] = []
        for ctx_match in self._CONTEXTO_DATA_FACTOS.finditer(texto):
            # Procurar data nos 80 caracteres após a palavra-chave
            pos = ctx_match.end()
            trecho = texto[pos:pos + 80]

            # DD/MM/YYYY ou DD-MM-YYYY
            m = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', trecho)
            if m:
                dt = _parse_date_ddmmyyyy(m)
                if dt:
                    datas_contextuais.append(dt)
                    continue

            # DD de Mês de YYYY
            m = re.search(r'(\d{1,2})\s+de\s+(\w+?)\.?\s+de\s+(\d{4})', trecho, re.IGNORECASE)
            if m:
                dt = _parse_date_nome_mes(m)
                if dt:
                    datas_contextuais.append(dt)

        if datas_contextuais:
            # Preferir datas com contexto explícito (mais fiável)
            data_factos = min(datas_contextuais)
            logger.info(
                f"[LEGAL] Data dos factos extraída (contexto): {data_factos.strftime('%d/%m/%Y')} "
                f"(de {len(datas_contextuais)} datas contextuais)"
            )
            return data_factos

        # --- Fase 2: Todas as datas, filtradas ---
        datas_encontradas: List[datetime] = []

        # Padrão 1: DD/MM/YYYY ou DD-MM-YYYY
        for m in re.finditer(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b', texto):
            dt = _parse_date_ddmmyyyy(m)
            if dt:
                datas_encontradas.append(dt)

        # Padrão 2: DD de Mês de YYYY
        for m in re.finditer(
            r'\b(\d{1,2})\s+de\s+(\w+?)\.?\s+de\s+(\d{4})\b',
            texto, re.IGNORECASE,
        ):
            dt = _parse_date_nome_mes(m)
            if dt:
                datas_encontradas.append(dt)

        if not datas_encontradas:
            logger.info("[LEGAL] Data dos factos não detectada no documento")
            return None

        # Filtrar datas que parecem ser de legislação (antes de 2000 são suspeitas)
        datas_recentes = [d for d in datas_encontradas if d.year >= 2000]
        if datas_recentes:
            datas_encontradas = datas_recentes

        # Heurística: a data mais antiga entre as filtradas
        data_factos = min(datas_encontradas)
        logger.info(
            f"[LEGAL] Data dos factos extraída: {data_factos.strftime('%d/%m/%Y')} "
            f"(de {len(datas_encontradas)} datas encontradas)"
        )
        return data_factos

    def _verificar_legislacao(self, texto: str) -> List[VerificacaoLegal]:
        """Verifica todas as citações legais no texto."""
        self._reportar_progresso("verificacao", 90, "Verificando citacoes legais...")

        citacoes, verificacoes = self.legal_verifier.verificar_texto(texto)

        # Gerar relatório
        relatorio = self.legal_verifier.gerar_relatorio(verificacoes)
        self._log_to_file("verificacao_legal.md", relatorio)

        return verificacoes

    def _determinar_parecer(self, texto_presidente: str) -> tuple:
        """Extrai o parecer final do texto do Conselheiro-Mor."""
        texto_upper = texto_presidente.upper()

        if "PROCEDENTE" in texto_upper and "IMPROCEDENTE" not in texto_upper:
            if "PARCIALMENTE" in texto_upper:
                return "PARCIALMENTE PROCEDENTE", SIMBOLOS_VERIFICACAO["atencao"], "atencao"
            return "PROCEDENTE", SIMBOLOS_VERIFICACAO["aprovado"], "aprovado"
        elif "IMPROCEDENTE" in texto_upper:
            return "IMPROCEDENTE", SIMBOLOS_VERIFICACAO["rejeitado"], "rejeitado"
        else:
            return "INCONCLUSIVO", SIMBOLOS_VERIFICACAO["atencao"], "atencao"

    def _quality_gate_curador(self, texto: str) -> List[str]:
        """Verifica quality gate do Curador Sénior. Retorna lista de falhas."""
        falhas = []

        # Q3: Zero IDs técnicos
        if re.search(r'finding_\w+|dp_\w+|nid=|ref_\w+|\bitem_id\b', texto):
            falhas.append("Q3: IDs técnicos encontrados (finding_*, dp_*, nid=, ref_*, item_id)")

        # Q4: Zero timestamps de sistema (formato ISO)
        if re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', texto):
            falhas.append("Q4: Timestamps de sistema encontrados (formato ISO)")

        # Q5: Zero nomes de modelos de IA
        if re.search(r'gpt-|claude-|gemini-|llama-|deepseek|\bopus\b|\bsonnet\b|\bhaiku\b', texto, re.IGNORECASE):
            falhas.append("Q5: Nomes de modelos de IA encontrados")

        # Q6: Zero custos de processamento
        if re.search(r'\$\d+\.\d+|\bUSD\b|\btokens\b', texto, re.IGNORECASE):
            falhas.append("Q6: Referências a custos/tokens encontradas")

        # Q7: Zero referências a fases do pipeline
        if re.search(r'[Ff]ase\s+\d|[Aa]uditor|[Rr]elator|\bpipeline\b|\bagente\b|\bextrat(?:or|ores)\b', texto, re.IGNORECASE):
            falhas.append("Q7: Referências a fases/papéis do pipeline encontradas")

        # Q10: Sumário ≤ 8 linhas
        match_sumario = re.search(
            r'##\s*1\.\s*SUM[ÁA]RIO.*?\n(.*?)(?=##\s*2\.)',
            texto, re.DOTALL | re.IGNORECASE
        )
        if match_sumario:
            linhas_sumario = [l for l in match_sumario.group(1).strip().split('\n') if l.strip()]
            if len(linhas_sumario) > 8:
                falhas.append(f"Q10: Sumário tem {len(linhas_sumario)} linhas (máximo 8)")

        # Q12: Disclaimer presente
        if not re.search(r'disclaimer|não substitui|nao substitui', texto, re.IGNORECASE):
            falhas.append("Q12: Disclaimer não encontrado")

        return falhas

    def _fase5_curadoria(
        self,
        final_decision,
        verificacoes: list,
        area_direito: str,
        fase0_triage,
        perguntas: list,
        documento,
        presidente_texto: str = "",
    ) -> str:
        """
        Fase 5: Curador Sénior — transforma o output técnico num Parecer Jurídico profissional.

        Returns:
            str: Markdown do relatório profissional, ou string vazia se falhou.
        """
        import json as _json

        # 1. Construir input JSON para o Curador
        curador_input = {
            "documento_contexto": {
                "area_direito": area_direito,
                "dominio_triage": fase0_triage.domain if fase0_triage else None,
                "filename": getattr(documento, 'filename', ''),
                "num_chars": getattr(documento, 'num_chars', 0),
                "num_pages": getattr(documento, 'num_pages', None),
            },
            "perguntas_utilizador": perguntas or [],
            "verificacoes_legais": [v.to_dict() for v in verificacoes] if verificacoes else [],
        }

        # Adicionar decisão final estruturada (modo unified)
        if final_decision:
            curador_input["decisao_final"] = {
                "decision_type": final_decision.decision_type.value if hasattr(final_decision.decision_type, 'value') else str(final_decision.decision_type),
                "confidence": final_decision.confidence,
                "final_answer": final_decision.final_answer,
                "decision_points": [dp.to_dict() for dp in final_decision.decision_points_final] if final_decision.decision_points_final else [],
                "proofs": [p.to_dict() for p in final_decision.proofs] if final_decision.proofs else [],
                "conflicts_resolved": [c.to_dict() for c in final_decision.conflicts_resolved] if final_decision.conflicts_resolved else [],
                "conflicts_unresolved": final_decision.conflicts_unresolved or [],
                "qa_final": final_decision.qa_final or [],
            }

        # Incluir o texto completo do presidente como base
        curador_input["relatorio_presidente_markdown"] = presidente_texto

        input_json = _json.dumps(curador_input, ensure_ascii=False, indent=2, default=str)

        # 2. Chamar o LLM (mesmo modelo do presidente)
        user_prompt = (
            f"INPUT JSON DO PIPELINE:\n\n{input_json}\n\n"
            "---\n\n"
            "Gera o Relatório de Análise Final completo em Markdown, seguindo TODAS as instruções do sistema."
        )

        # v5.0: Curador com fallback para substitutos (mesmos do presidente)
        models_to_try = [self.presidente_model] + PRESIDENTE_SUBSTITUTES.get(self.presidente_model, [])
        resultado = None
        curador_used_model = self.presidente_model
        for attempt_idx, try_model in enumerate(models_to_try):
            label = "primário" if attempt_idx == 0 else f"substituto {attempt_idx}"
            resultado = self._call_llm(
                model=try_model,
                prompt=user_prompt,
                system_prompt=PROMPT_CURADOR_SENIOR,
                role_name="curador_senior" if attempt_idx == 0 else f"curador_senior_fallback_{attempt_idx}",
                temperature=0.0,
                max_tokens=32768,
            )
            if resultado is not None and resultado.sucesso and resultado.conteudo and resultado.conteudo.strip():
                curador_used_model = try_model
                if attempt_idx > 0:
                    logger.warning(
                        f"[CURADOR-FALLBACK] {label} ({try_model}) assumiu "
                        f"após falha do primário ({self.presidente_model})"
                    )
                break
            else:
                logger.warning(
                    f"[CURADOR-FALLBACK] {label} ({try_model}) falhou"
                    + (" — tentando próximo substituto..." if attempt_idx < len(models_to_try) - 1 else "")
                )
                resultado = None

        if resultado is None or not resultado.conteudo or not resultado.conteudo.strip():
            logger.warning("[CURADOR] Todos os modelos falharam")
            return ""

        relatorio = resultado.conteudo

        # 3. Quality Gate — verificações regex pós-geração + até 2 re-iterações
        max_iteracoes = 2
        for iteracao in range(max_iteracoes):
            falhas = self._quality_gate_curador(relatorio)
            if not falhas:
                logger.info(f"[CURADOR] Quality gate passed (iteração {iteracao})")
                break

            logger.warning(f"[CURADOR] Quality gate falhou (iteração {iteracao + 1}/{max_iteracoes}): {falhas}")

            # Re-enviar ao Curador com feedback
            feedback_prompt = (
                "O teu relatório falhou nas seguintes verificações:\n"
                + "\n".join(f"- {f}" for f in falhas)
                + "\n\nCorrige APENAS estes problemas e devolve o relatório completo corrigido."
                + f"\n\nRELATÓRIO ACTUAL:\n\n{relatorio}"
            )

            resultado_corr = self._call_llm(
                model=curador_used_model,
                prompt=feedback_prompt,
                system_prompt=PROMPT_CURADOR_SENIOR,
                role_name="curador_senior_correcao",
                temperature=0.0,
                max_tokens=32768,
            )

            if resultado_corr.sucesso and resultado_corr.conteudo.strip():
                relatorio = resultado_corr.conteudo
            else:
                logger.warning("[CURADOR] Correcção falhou, mantendo versão anterior")
                break
        else:
            # Loop completou sem break = todas as iterações usadas
            falhas_finais = self._quality_gate_curador(relatorio)
            if falhas_finais:
                relatorio = "[REVISÃO MANUAL RECOMENDADA]\n\n" + relatorio
                logger.warning(f"[CURADOR] Quality gate falhou após {max_iteracoes} iterações: {falhas_finais}")

        self._log_to_file("fase5_curador_senior.md", relatorio)
        return relatorio

    def processar(
        self,
        documento: DocumentContent,
        area_direito: str,
        perguntas_raw: str = "",
        titulo: str = "",  # ← NOVO!
    ) -> PipelineResult:
        """
        Executa o pipeline completo.

        Args:
            documento: Documento carregado
            area_direito: Área do direito
            perguntas_raw: Texto bruto com perguntas (separadas por ---)
            titulo: Título do projeto (opcional)

        Returns:
            PipelineResult com todos os resultados
        """
        run_id = self._setup_run()
        timestamp_inicio = datetime.now()

        # Defense-in-depth: sanitize filename to prevent prompt injection
        import re as _re
        if hasattr(documento, 'filename') and documento.filename:
            documento.filename = _re.sub(r'[^a-zA-Z0-9._\-\s]', '_', documento.filename)[:255]

        # Parse e validação de perguntas
        perguntas = parse_perguntas(perguntas_raw)
        if perguntas:
            pode_continuar, msg = validar_perguntas(perguntas)
            if not pode_continuar:
                raise ValueError(f"Perguntas invalidas: {msg}")
            logger.info(f"Processando {len(perguntas)} pergunta(s) do utilizador")
        else:
            logger.info("Sem perguntas do utilizador")
        
        # ← NOVO: Gerar título automático se não fornecido
        if not titulo:
            titulo = gerar_titulo_automatico(documento.filename, area_direito)
        
        self._titulo = titulo  # ← NOVO: Guardar para usar em _guardar_resultado

        # NOVO: Guardar texto do documento para calculos de failover e max_tokens
        self._document_text = documento.text or ""
        logger.info(f"[DOCUMENTO] Tamanho: {len(self._document_text):,} chars")
        # NOVO C5: Verificar se documento precisa processamento por zonas
        if should_use_zones(self._document_text):
            zone_plan = create_zone_plan(self._document_text, getattr(self, '_page_mapper', None))
            log_zone_plan(zone_plan, self._output_dir)
            logger.info(f"[ZONAS] Documento GRANDE detectado: {len(zone_plan.zones)} zonas planeadas")
        else:
            logger.info("[ZONAS] Documento dentro do limite — processamento normal")
        result = PipelineResult(
            run_id=run_id,
            documento=documento,
            area_direito=area_direito,
            perguntas_utilizador=perguntas,
            timestamp_inicio=timestamp_inicio,
        )

        # Inicializar CostController para rastrear custos REAIS
        # FIX 2026-02-14: Budget maior para Elite (gpt-5.2-pro com reasoning tokens é caro)
        tier_name = getattr(self, '_tier', 'bronze')
        default_budget = 10.0
        if tier_name == 'gold':
            default_budget = 25.0  # gpt-5.2-pro reasoning tokens podem custar $3-7/call
        elif tier_name == 'silver':
            default_budget = 15.0
        budget = float(os.getenv("MAX_BUDGET_USD", str(default_budget)))

        # Token limit DINÂMICO baseado no tamanho do documento
        # Fórmula: num_extractores × num_chunks × ~20K tokens/chunk + margem fases 2-4
        doc_chars = len(self._document_text)
        num_extractors = len([c for c in self._llm_configs if c["id"].startswith("E")])
        num_chunks = max(1, doc_chars // CHUNK_SIZE_CHARS + 1)
        # Fase 1: extractores (bulk dos tokens)
        fase1_estimate = num_extractors * num_chunks * 25_000
        # Fases 2-4: auditores + relatores + presidente (~200K margem)
        fases_2_4_estimate = 200_000
        token_limit = fase1_estimate + fases_2_4_estimate
        # Mínimo 500K, sem máximo
        token_limit = max(500_000, token_limit)

        self._cost_controller = CostController(
            run_id=run_id,
            budget_limit_usd=budget,
            token_limit=token_limit,
        )
        logger.info(
            f"[CUSTO] CostController inicializado: run={run_id}, tier={tier_name}, "
            f"budget=${budget:.2f}, token_limit={token_limit:,} "
            f"(doc={doc_chars:,} chars, {num_extractors} extractores, {num_chunks} chunks)"
        )

        # Inicializar PerformanceTracker para feedback adaptativo
        self._perf_tracker = None
        try:
            from src.performance_tracker import PerformanceTracker
            from auth_service import get_supabase_admin
            self._perf_tracker = PerformanceTracker.get_instance(get_supabase_admin())
            self._perf_tracker.refresh_cache()
            logger.info("[PERF] PerformanceTracker inicializado e cache carregado")
        except Exception as e:
            logger.warning(f"[PERF] PerformanceTracker indisponivel: {e}")

        # Guardar run_id e tier para o performance tracker
        self._run_id = run_id
        self._tier = getattr(self, '_tier', 'bronze')

        # Inicializar variáveis usadas após o try/except principal
        final_decision = None
        judge_opinions = None

        try:
            # ===== FASE 0: TRIAGEM (v4.0 Handover) =====
            fase0_triage = None
            try:
                from src.pipeline.triage import TriageProcessor, inject_page_markers
                triage_proc = TriageProcessor(
                    llm_client=self.llm_client,
                    cost_controller=self._cost_controller,
                )
                self._reportar_progresso("fase0", 2, "Triagem: classificando domínio jurídico...")
                import time as _time_triage
                _triage_start = _time_triage.time()
                fase0_triage = triage_proc.run(
                    text=documento.text,
                    filename=documento.filename,
                    num_pages=getattr(documento, 'num_pages', 0),
                )
                _triage_ms = (_time_triage.time() - _triage_start) * 1000

                # v5.1: Logging explícito da Fase 0 (sempre visível nos logs)
                logger.info(
                    f"[FASE0] Triagem concluída em {_triage_ms:.0f}ms — "
                    f"domínio='{fase0_triage.domain}' "
                    f"(confiança={fase0_triage.domain_confidence:.0%}, "
                    f"consenso={fase0_triage.consensus})"
                )
                logger.info(
                    f"[FASE0] Votos: {fase0_triage.votes} | "
                    f"Fotos estimadas: {fase0_triage.photo_estimate} | "
                    f"User input: '{area_direito}'"
                )

                # Se triagem detectou domínio com confiança, usar em vez do user input
                if fase0_triage.domain_confidence >= 0.75 and area_direito in ("Civil", ""):
                    logger.info(
                        f"[FASE0] Triagem sugere domínio '{fase0_triage.domain}' "
                        f"(confiança={fase0_triage.domain_confidence:.0%}), "
                        f"user input='{area_direito}'"
                    )
                # Injectar marcadores de página se necessário
                documento_text_orig = documento.text
                documento.text = inject_page_markers(documento.text)
                if documento.text != documento_text_orig:
                    logger.info("[FASE0] Marcadores [Pág_X] injectados no texto")
                # Photo warning
                if fase0_triage.photo_warning == "queue_mode":
                    logger.warning(
                        f"[FASE0] ALERTA: {fase0_triage.photo_estimate} fotos estimadas — "
                        f"modo fila recomendado"
                    )
                self._reportar_progresso("fase0", 8, f"Triagem concluída: {fase0_triage.domain}")
            except Exception as e:
                logger.warning(f"[FASE0] Triagem falhou (non-blocking): {e}")

            # Fase 1: Extração (SEM perguntas) + Agregador LOSSLESS
            unified_result = None
            if USE_UNIFIED_PROVENANCE:
                # NOVO: Modo unificado com proveniência e cobertura
                logger.info("Modo UNIFICADO de proveniência ativado")
                extracoes, bruto_f1, consolidado_f1, unified_result = self._fase1_extracao_unified(
                    documento, area_direito
                )

                # Verificar cobertura mínima
                if unified_result:
                    coverage_path = self._output_dir / "fase1_coverage_report.json"
                    if coverage_path.exists():
                        import json as _json
                        with open(coverage_path, 'r', encoding='utf-8') as f:
                            coverage_data = _json.load(f)
                        if coverage_data.get('coverage_percent', 0) < COVERAGE_MIN_THRESHOLD:
                            logger.warning(
                                f"ALERTA: Cobertura {coverage_data['coverage_percent']:.1f}% "
                                f"< {COVERAGE_MIN_THRESHOLD}%"
                            )
            else:
                # Modo legacy
                extracoes, bruto_f1, consolidado_f1 = self._fase1_extracao(documento, area_direito)

            # v4.0: Guardar resultado da triagem
            if fase0_triage:
                result.fase0_triage = fase0_triage.to_dict()

            result.fase1_extracoes = extracoes
            result.fase1_agregado_bruto = bruto_f1
            result.fase1_agregado_consolidado = consolidado_f1
            result.fase1_agregado = consolidado_f1  # Backwards compat

            # Guardar referência ao documento para consensus engine
            self._documento = documento

            # Inicializar IntegrityValidator para validações nas fases 2-4
            if USE_UNIFIED_PROVENANCE:
                self._document_text = documento.text
                self._unified_result = unified_result

                # Criar page_mapper se PDFSafe disponível
                if documento.pdf_safe_result:
                    self._page_mapper = CharToPageMapper.from_pdf_safe_result(
                        documento.pdf_safe_result, f"doc_{run_id[:8]}"
                    )
                elif documento.text:
                    self._page_mapper = CharToPageMapper.from_text_markers(
                        documento.text, f"doc_{run_id[:8]}"
                    )

                self._integrity_validator = IntegrityValidator(
                    run_id=run_id,
                    document_text=documento.text,
                    total_chars=documento.num_chars,
                    page_mapper=self._page_mapper,
                    unified_result=unified_result,
                )
                logger.info("✓ IntegrityValidator inicializado")

            # Fase 2: Auditoria (SEM perguntas) + Consolidador LOSSLESS
            audit_reports = None
            chefe_report = None
            if USE_UNIFIED_PROVENANCE:
                # MODO UNIFIED: JSON estruturado com proveniência
                audit_reports, bruto_f2, consolidado_f2, chefe_report = self._fase2_auditoria_unified(
                    consolidado_f1, area_direito, run_id
                )
                # Criar FaseResult para compatibilidade
                # Tokens reais vêm do CostController (registados em _call_llm)
                auditorias = []
                for r in audit_reports:
                    # Procurar tokens reais no CostController
                    fase_tokens = 0
                    fase_prompt = 0
                    fase_completion = 0
                    # auditor_id = "A1" → número = "1", phase = "auditor_1_json"
                    aid_num = r.auditor_id.replace("A", "").replace("a", "")
                    if hasattr(self, '_cost_controller') and self._cost_controller:
                        for pu in self._cost_controller.usage.phases:
                            if f"auditor_{aid_num}" in pu.phase:
                                fase_tokens += pu.total_tokens
                                fase_prompt += pu.prompt_tokens
                                fase_completion += pu.completion_tokens
                    if fase_tokens == 0:
                        fase_tokens = len(r.to_markdown()) // 3  # fallback
                    # FIX 2026-02-14: Só contar erros reais (não INTEGRITY_WARNING) para sucesso
                    real_errors = [e for e in r.errors if not str(e).startswith("INTEGRITY_WARNING:")]
                    auditorias.append(FaseResult(
                        fase="auditoria",
                        modelo=r.model_name,
                        role=f"auditor_{r.auditor_id}",
                        conteudo=r.to_markdown(),
                        tokens_usados=fase_tokens,
                        prompt_tokens=fase_prompt,
                        completion_tokens=fase_completion,
                        latencia_ms=0,
                        sucesso=len(real_errors) == 0,
                    ))
            else:
                auditorias, bruto_f2, consolidado_f2 = self._fase2_auditoria(consolidado_f1, area_direito)

            result.fase2_auditorias = auditorias
            result.fase2_auditorias_brutas = bruto_f2
            result.fase2_chefe_consolidado = consolidado_f2
            result.fase2_chefe = consolidado_f2  # Backwards compat

            # FALLBACK: Se Consolidador produziu 0 findings, usar auditorias individuais
            if chefe_report and hasattr(chefe_report, 'consolidated_findings'):
                if not chefe_report.consolidated_findings or len(chefe_report.consolidated_findings) == 0:
                    logger.warning(
                        "Consolidador Auditor com 0 findings consolidados - "
                        "usando auditorias individuais (bruto) como input para Fase 3"
                    )
                    consolidado_f2 = bruto_f2
                    if audit_reports:
                        # Tentar reconstruir markdown dos auditores individuais
                        partes = []
                        for r in audit_reports:
                            md = r.to_markdown() if hasattr(r, 'to_markdown') else str(r)
                            partes.append(md)
                        if partes:
                            consolidado_f2 = (
                                f"# AUDITORIAS INDIVIDUAIS (fallback - Consolidador com 0 findings)\n\n"
                                + "\n\n---\n\n".join(partes)
                            )
                            logger.info(
                                f"Fallback: {len(audit_reports)} auditorias individuais "
                                f"usadas como input para Fase 3"
                            )

            # Fase 3: Relatoria (COM perguntas)
            judge_opinions = None
            if USE_UNIFIED_PROVENANCE:
                # MODO UNIFIED: JSON estruturado
                judge_opinions, respostas_qa = self._fase3_relatoria_unified(
                    consolidado_f2, area_direito, perguntas, run_id
                )
                # Criar FaseResult para compatibilidade
                # Tokens reais vêm do CostController (registados em _call_llm)
                pareceres = []
                for o in judge_opinions:
                    fase_tokens = 0
                    fase_prompt = 0
                    fase_completion = 0
                    # judge_id = "J1" -> numero = "1", phase = "relator_1_json"
                    jid_num = o.judge_id.replace("J", "").replace("j", "")
                    if hasattr(self, '_cost_controller') and self._cost_controller:
                        for pu in self._cost_controller.usage.phases:
                            if f"relator_{jid_num}" in pu.phase:
                                fase_tokens += pu.total_tokens
                                fase_prompt += pu.prompt_tokens
                                fase_completion += pu.completion_tokens
                    if fase_tokens == 0:
                        fase_tokens = len(o.to_markdown()) // 3  # fallback
                    # FIX 2026-02-14: Só contar erros reais (não INTEGRITY_WARNING) para sucesso
                    real_errors_j = [e for e in o.errors if not str(e).startswith("INTEGRITY_WARNING:")]
                    pareceres.append(FaseResult(
                        fase="relatoria",
                        modelo=o.model_name,
                        role=f"relator_{o.judge_id}",
                        conteudo=o.to_markdown(),
                        tokens_usados=fase_tokens,
                        prompt_tokens=fase_prompt,
                        completion_tokens=fase_completion,
                        latencia_ms=0,
                        sucesso=len(real_errors_j) == 0,
                    ))
            else:
                pareceres, respostas_qa = self._fase3_relatoria(consolidado_f2, area_direito, perguntas)

            result.fase3_pareceres = pareceres
            result.respostas_juizes_qa = respostas_qa

            # Fase 4: Conselheiro-Mor (COM perguntas)
            final_decision = None
            if USE_UNIFIED_PROVENANCE and judge_opinions:
                # MODO UNIFIED: JSON estruturado
                final_decision = self._fase4_presidente_unified(
                    judge_opinions, perguntas, respostas_qa, run_id
                )
                presidente = final_decision.output_markdown
            else:
                presidente = self._fase4_presidente(pareceres, perguntas, respostas_qa)

            result.fase3_presidente = presidente
            result.respostas_finais_qa = presidente if perguntas else ""

            # Verificação Legal (com detecção temporal automática)
            data_factos = self._extrair_data_factos(self._document_text or "")
            try:
                if data_factos:
                    self.legal_verifier.set_data_factos(data_factos)
                    logger.info(f"[LEGAL] Data dos factos: {data_factos.strftime('%d/%m/%Y')}")
                verificacoes = self._verificar_legislacao(presidente)
                result.verificacoes_legais = verificacoes
            finally:
                # Limpar SEMPRE (mesmo em excepção) para não contaminar próximas runs
                self.legal_verifier.set_data_factos(None)

            # Determinar parecer — preferir decision_type do JSON (mais fiável)
            if final_decision and hasattr(final_decision, 'decision_type'):
                dt = final_decision.decision_type
                veredicto_map = {
                    DecisionType.PROCEDENTE: ("PROCEDENTE", SIMBOLOS_VERIFICACAO["aprovado"], "aprovado"),
                    DecisionType.IMPROCEDENTE: ("IMPROCEDENTE", SIMBOLOS_VERIFICACAO["rejeitado"], "rejeitado"),
                    DecisionType.PARCIALMENTE_PROCEDENTE: ("PARCIALMENTE PROCEDENTE", SIMBOLOS_VERIFICACAO["atencao"], "atencao"),
                    DecisionType.INCONCLUSIVO: ("INCONCLUSIVO", SIMBOLOS_VERIFICACAO["atencao"], "atencao"),
                }
                veredicto, simbolo, status = veredicto_map.get(
                    dt, ("INCONCLUSIVO", SIMBOLOS_VERIFICACAO["atencao"], "atencao")
                )
                logger.info(f"Parecer extraído do JSON: {dt.value} → {veredicto}")
            else:
                veredicto, simbolo, status = self._determinar_parecer(presidente)
            result.veredicto_final = veredicto
            result.simbolo_final = simbolo
            result.status_final = status

            # FIX 2026-02-14: Construir resumo estruturado por IA (para frontend)
            resumo_ia = {}
            if audit_reports:
                for r in audit_reports:
                    real_errs = [e for e in r.errors if not str(e).startswith("INTEGRITY_WARNING:")]
                    integrity_warns = [e for e in r.errors if str(e).startswith("INTEGRITY_WARNING:")]
                    resumo_ia[f"auditor_{r.auditor_id}"] = {
                        "tipo": "auditor",
                        "modelo": r.model_name,
                        "findings": len(r.findings),
                        "erros": len(real_errs),
                        "integrity_warnings": len(integrity_warns),
                        "sucesso": len(real_errs) == 0,
                    }
            if judge_opinions:
                for o in judge_opinions:
                    real_errs = [e for e in o.errors if not str(e).startswith("INTEGRITY_WARNING:")]
                    integrity_warns = [e for e in o.errors if str(e).startswith("INTEGRITY_WARNING:")]
                    resumo_ia[f"relator_{o.judge_id}"] = {
                        "tipo": "relator",
                        "modelo": o.model_name,
                        "recommendation": o.recommendation.value if hasattr(o.recommendation, 'value') else str(o.recommendation),
                        "decision_points": len(o.decision_points),
                        "erros": len(real_errs),
                        "integrity_warnings": len(integrity_warns),
                        "sucesso": len(real_errs) == 0,
                    }
            if final_decision:
                real_errs = [e for e in final_decision.errors if not str(e).startswith("INTEGRITY_WARNING:")]
                resumo_ia["presidente"] = {
                    "tipo": "presidente",
                    "modelo": final_decision.model_name,
                    "decision_type": final_decision.decision_type.value if hasattr(final_decision.decision_type, 'value') else str(final_decision.decision_type),
                    "confidence": round(final_decision.confidence, 4),
                    "erros": len(real_errs),
                    "sucesso": len(real_errs) == 0,
                }
            result.resumo_por_ia = resumo_ia

            # ===== FASE 5: CURADORIA SÉNIOR =====
            self._reportar_progresso("fase5", 92, "Curador Sénior: redigindo parecer final...")
            raw_presidente = result.fase3_presidente  # Guardar output bruto do presidente
            try:
                relatorio_curador = self._fase5_curadoria(
                    final_decision=final_decision,
                    verificacoes=result.verificacoes_legais,
                    area_direito=area_direito,
                    fase0_triage=fase0_triage,
                    perguntas=perguntas,
                    documento=documento,
                    presidente_texto=raw_presidente,
                )
                if relatorio_curador and relatorio_curador.strip():
                    result.fase3_presidente = relatorio_curador  # Substituir com relatório profissional
                    logger.info(f"[CURADOR] Relatório profissional: {len(relatorio_curador)} chars")
                    resumo_ia["curador_senior"] = {
                        "tipo": "curador_senior",
                        "modelo": self.presidente_model,
                        "sucesso": True,
                    }
                else:
                    logger.warning("[CURADOR] Output vazio, mantendo output do presidente")
            except Exception as e:
                logger.warning(f"[CURADOR] Falhou (non-blocking): {e}. Mantendo output do presidente.")

            # Guardar output raw do presidente para debug
            self._log_to_file("fase4_presidente_raw.md", raw_presidente)
            self._reportar_progresso("fase5", 95, "Parecer finalizado")

            # Calcular totais via CostController (tokens REAIS das APIs)
            if self._cost_controller:
                run_usage = self._cost_controller.finalize()
                result.total_tokens = run_usage.total_tokens

                MARGEM = 2.0  # FIX 2026-02-14: 100% margem (era 1.40/40%, deve ser 2.0/100% como wallet_manager)
                custo_por_fase = self._cost_controller.get_cost_by_phase()
                pricing_info = self._cost_controller.get_pricing_info()
                result.custos = {
                    "custo_total_usd": round(run_usage.total_cost_usd, 4),
                    "custo_cliente_usd": round(run_usage.total_cost_usd * MARGEM, 4),
                    "margem_percentagem": round((MARGEM - 1.0) * 100, 1),
                    "total_prompt_tokens": run_usage.total_prompt_tokens,
                    "total_completion_tokens": run_usage.total_completion_tokens,
                    "total_tokens": run_usage.total_tokens,
                    "por_fase": custo_por_fase,
                    "detalhado": [pu.to_dict() for pu in run_usage.phases],
                    "budget_limit_usd": run_usage.budget_limit_usd,
                    "budget_restante_usd": round(self._cost_controller.get_remaining_budget(), 4),
                    # Informação sobre preços dinâmicos
                    "precos_fonte": pricing_info["fonte"],
                    "precos_timestamp": pricing_info["timestamp"],
                    "precos_por_modelo": pricing_info["precos_por_modelo"],
                }
                logger.info(
                    f"[CUSTO-FINAL] API: ${run_usage.total_cost_usd:.4f} | "
                    f"Cliente (100% margem): ${run_usage.total_cost_usd * MARGEM:.4f} | "
                    f"Tokens: {run_usage.total_tokens:,} | "
                    f"Chamadas: {len(run_usage.phases)} | "
                    f"Preços: {pricing_info['fonte']}"
                )
            else:
                # Fallback se CostController não disponível
                todos_resultados = extracoes + auditorias + pareceres
                result.total_tokens = sum(r.tokens_usados for r in todos_resultados)

            # Latência real = tempo decorrido desde o início do pipeline
            result.total_latencia_ms = (datetime.now() - result.timestamp_inicio).total_seconds() * 1000

            result.sucesso = True
            self._reportar_progresso("concluido", 100, "Pipeline concluido!")

        except (BudgetExceededError, InsufficientCreditsError) as e:
            logger.error(f"Erro de créditos no pipeline: {e}")
            result.sucesso = False
            result.erro = str(e)
            raise  # Re-raise budget/credit errors - must not be swallowed
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error(f"Erro no pipeline: {e}")
            result.sucesso = False
            result.erro = str(e)

        result.timestamp_fim = datetime.now()

        # Guardar IntegrityReport se validator ativo
        if USE_UNIFIED_PROVENANCE and hasattr(self, '_integrity_validator') and self._integrity_validator:
            try:
                self._integrity_validator.save_report(self._output_dir)
                logger.info("✓ IntegrityReport guardado")
            except Exception as e:
                logger.warning(f"Erro ao guardar IntegrityReport: {e}")

        # Executar MetaIntegrity Validation
        if USE_META_INTEGRITY or ALWAYS_GENERATE_META_REPORT:
            try:
                # Obter doc_id do documento
                loaded_doc_ids = set()
                if hasattr(self, '_unified_result') and self._unified_result:
                    doc_meta = getattr(self._unified_result, 'document_meta', None)
                    if doc_meta:
                        loaded_doc_ids.add(getattr(doc_meta, 'doc_id', None) or f"doc_{run_id[:8]}")
                else:
                    loaded_doc_ids.add(f"doc_{run_id[:8]}")

                # Configurar MetaIntegrity
                meta_config = MetaIntegrityConfig(
                    timestamp_tolerance_minutes=META_INTEGRITY_TIMESTAMP_TOLERANCE,
                    pages_tolerance_percent=META_INTEGRITY_PAGES_TOLERANCE_PERCENT,
                    citation_count_tolerance=META_INTEGRITY_CITATION_COUNT_TOLERANCE,
                )

                # Executar validação
                meta_report = validate_run_meta_integrity(
                    run_id=run_id,
                    output_dir=self._output_dir,
                    run_start=timestamp_inicio,
                    loaded_doc_ids=loaded_doc_ids,
                    document_num_pages=getattr(documento, 'num_pages', None),
                    config=meta_config,
                )

                # Guardar relatório
                meta_report.save(self._output_dir)
                logger.info(
                    f"✓ MetaIntegrityReport guardado: "
                    f"is_consistent={meta_report.is_consistent}, "
                    f"errors={meta_report.error_count}, warnings={meta_report.warning_count}"
                )

                # Aplicar Confidence Policy se habilitado
                if APPLY_CONFIDENCE_POLICY and final_decision and hasattr(final_decision, 'confidence'):
                    # Calcular penalty
                    integrity_report_path = self._output_dir / "integrity_report.json"
                    coverage_report_path = self._output_dir / "fase1_coverage_report.json"

                    integrity_data = None
                    coverage_data = None

                    if integrity_report_path.exists():
                        with open(integrity_report_path, 'r', encoding='utf-8') as f:
                            integrity_data = json.load(f)

                    if coverage_report_path.exists():
                        with open(coverage_report_path, 'r', encoding='utf-8') as f:
                            coverage_data = json.load(f)

                    # Coletar erros das fases
                    all_errors = []
                    if final_decision and hasattr(final_decision, 'errors'):
                        all_errors.extend(final_decision.errors)

                    # Calcular penalty
                    penalty_result = compute_penalty(
                        integrity_report=integrity_data,
                        coverage_report=coverage_data,
                        errors_list=all_errors,
                        original_confidence=final_decision.confidence,
                    )

                    # Aplicar se houver penalidade
                    if penalty_result.total_penalty > 0:
                        old_confidence = final_decision.confidence
                        final_decision.confidence = penalty_result.adjusted_confidence
                        logger.info(
                            f"Confidence ajustada: {old_confidence:.2f} → {final_decision.confidence:.2f} "
                            f"(penalty={penalty_result.total_penalty:.2f})"
                        )

                        # Guardar penalty info
                        penalty_path = self._output_dir / "confidence_penalty.json"
                        with open(penalty_path, 'w', encoding='utf-8') as f:
                            json.dump(penalty_result.to_dict(), f, ensure_ascii=False, indent=2)

            except Exception as e:
                logger.warning(f"Erro na validação MetaIntegrity: {e}")
                import traceback
                traceback.print_exc()

        # Guardar resultado completo
        self._guardar_resultado(result)

        return result

    def _guardar_resultado(self, result: PipelineResult):
        """Guarda o resultado completo em JSON."""
        if self._output_dir:
            try:
                # JSON completo
                json_path = self._output_dir / "resultado.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

                # Markdown resumido
                md_path = self._output_dir / "RESUMO.md"
                md_content = self._gerar_resumo_md(result)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)

                # Copiar para histórico
                historico_path = HISTORICO_DIR / f"{result.run_id}.json"
                with open(historico_path, "w", encoding="utf-8") as f:
                    json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
            except OSError as e:
                logger.error(f"Erro ao guardar resultado em disco: {e}")
            
            # ← NOVO: Guardar metadata (título, descrição, etc.)
            guardar_metadata(
                run_id=result.run_id,
                output_dir=OUTPUT_DIR,
                titulo=self._titulo,
                descricao="",
                area_direito=result.area_direito,
                num_documentos=1 if result.documento else 0
            )

            logger.info(f"Resultados guardados em: {self._output_dir}")

    def _gerar_resumo_md(self, result: PipelineResult) -> str:
        """Gera um resumo em Markdown."""
        linhas = [
            f"# TRIBUNAL SAAS - RESULTADO",
            f"",
            f"**Run ID:** {result.run_id}",
            f"**Data:** {result.timestamp_inicio.strftime('%d/%m/%Y %H:%M')}",
            f"**Documento:** {result.documento.filename if result.documento else 'N/A'}",
            f"**Area:** {result.area_direito}",
            f"**Perguntas Q&A:** {len(result.perguntas_utilizador)}",
            f"",
            f"---",
            f"",
            f"## {result.simbolo_final} PARECER FINAL: {result.veredicto_final}",
            f"",
            f"---",
            f"",
            f"## Estatisticas",
            f"- Total de tokens: {result.total_tokens:,}",
            f"- Latencia total: {result.total_latencia_ms:.0f}ms",
            f"- Custo total: ${result.custos['custo_total_usd']:.4f}" if result.custos else "- Custo total: N/A",
            f"- Custo cliente: ${result.custos['custo_cliente_usd']:.4f}" if result.custos else "",
            f"- Citacoes legais verificadas: {len(result.verificacoes_legais)}",
            f"",
            f"---",
            f"",
            f"## Ficheiros de Output",
            f"",
            f"### Fase 1: Extracao",
            f"- `fase1_extrator_1.md` - Extrator 1",
            f"- `fase1_extrator_2.md` - Extrator 2",
            f"- `fase1_extrator_3.md` - Extrator 3",
            f"- `fase1_agregado_bruto.md` - 3 extracoes concatenadas",
            f"- `fase1_agregado_consolidado.md` - **Extracao LOSSLESS (Agregador)**",
            f"",
            f"### Fase 2: Auditoria",
            f"- `fase2_auditor_1.md` - Auditor 1 (GPT-5.2)",
            f"- `fase2_auditor_2.md` - Auditor 2 (Claude Opus 4.5)",
            f"- `fase2_auditor_3.md` - Auditor 3 (Gemini 3 Pro)",
            f"- `fase2_auditor_4.md` - Auditor 4 (Grok 4.1 Fast)",
            f"- `fase2_auditorias_brutas.md` - 4 auditorias concatenadas",
            f"- `fase2_consolidador.md` - **Auditoria LOSSLESS (Consolidador)**",
            f"",
            f"### Fase 3: Relatoria",
            f"- `fase3_relator_1.md` - Relator 1",
            f"- `fase3_relator_2.md` - Relator 2",
            f"- `fase3_relator_3.md` - Relator 3",
            f"",
            f"### Fase 4: Conselheiro-Mor",
            f"- `fase4_conselheiro.md` - Decisao final",
            f"- `verificacao_legal.md` - Relatorio de verificacao DRE",
            f"",
            f"---",
            f"",
            f"## Verificacoes Legais",
        ]

        for v in result.verificacoes_legais:
            linhas.append(f"- {v.simbolo} {v.citacao.texto_normalizado}")

        # Adicionar perguntas Q&A se houver
        if result.perguntas_utilizador:
            linhas.extend([
                f"",
                f"---",
                f"",
                f"## Perguntas do Utilizador",
                f"",
            ])
            for i, p in enumerate(result.perguntas_utilizador, 1):
                linhas.append(f"{i}. {p}")

        linhas.extend([
            f"",
            f"---",
            f"",
            f"## Decisao do Conselheiro-Mor",
            f"",
            result.fase3_presidente,
        ])

        return "\n".join(linhas)

    def processar_texto(self, texto: str, area_direito: str, perguntas_raw: str = "") -> PipelineResult:
        """Processa texto diretamente (sem ficheiro)."""
        documento = DocumentContent(
            filename="texto_direto.txt",
            extension=".txt",
            text=texto,
            num_chars=len(texto),
            num_words=len(texto.split()),
            success=True,
        )
        return self.processar(documento, area_direito, perguntas_raw)

    def listar_runs(self) -> List[Dict]:
        """Lista todas as execuções no histórico."""
        runs = []
        for filepath in HISTORICO_DIR.glob("*.json"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    runs.append({
                        "run_id": data.get("run_id"),
                        "timestamp": data.get("timestamp_inicio"),
                        "documento": data.get("documento", {}).get("filename"),
                        "veredicto": data.get("veredicto_final"),
                        "simbolo": data.get("simbolo_final"),
                        "perguntas": len(data.get("perguntas_utilizador", [])),
                    })
            except Exception as e:
                logger.warning(f"Erro ao ler {filepath}: {e}")

        return sorted(runs, key=lambda x: x.get("timestamp", ""), reverse=True)

    def carregar_run(self, run_id: str) -> Optional[Dict]:
        """Carrega os detalhes de uma execução."""
        filepath = HISTORICO_DIR / f"{run_id}.json"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None


# Backward compatibility alias
TribunalProcessor = LexForumProcessor

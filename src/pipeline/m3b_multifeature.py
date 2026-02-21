# ============================================================================
# Pipeline v4.2 — M3B: Multi-Feature via Eden AI
# ============================================================================
# Extracção de features adicionais usando os mesmos file_ids do M3:
#   - NER (Named Entity Recognition)
#   - Extracção de Tabelas
#   - Analisador Financeiro (condicional)
# ============================================================================

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import requests as http_requests

from src.config import EDEN_AI_API_KEY

logger = logging.getLogger(__name__)

EDEN_AI_UNIVERSAL_URL = "https://api.edenai.run/v3/universal-ai"

# NER providers
NER_PROVIDER = "google"

# Table extraction
TABLE_PROVIDER = "google"


@dataclass
class NamedEntity:
    """Entidade nomeada detectada por NER."""
    entity_type: str    # ORG, PERSON, GPE, DATE, MONEY, etc.
    value: str          # texto da entidade
    start: int          # posição inicial no texto
    end: int            # posição final no texto
    page_num: int       # página onde foi detectada
    confidence: float = 0.0
    source: str = "ner"


@dataclass
class ExtractedTable:
    """Tabela extraída de uma página."""
    page_num: int
    headers: list[str]
    rows: list[list[str]]
    raw_text: str           # representação textual da tabela
    start_char: int = 0     # posição aproximada no texto da página
    end_char: int = 0


@dataclass
class MultiFeatureResult:
    """Resultado da extracção multi-feature."""
    entities: list[NamedEntity] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    financial_data: Optional[dict] = None
    api_cost_usd: float = 0.0       # custo real reportado pelo Eden AI
    errors: list[str] = field(default_factory=list)


def extract_features(
    ocr_result,  # OCRDocumentResult from m3_ocr_engine
    api_key: Optional[str] = None,
    extract_tables: bool = True,
    extract_financial: bool = False,
) -> MultiFeatureResult:
    """
    M3B: Extracção de features multi-modal.

    Usa texto OCR para NER e file_ids para extracção de tabelas.

    Args:
        ocr_result: resultado do M3 (OCRDocumentResult)
        api_key: Eden AI API key
        extract_tables: extrair tabelas
        extract_financial: extrair dados financeiros (se documento financeiro)

    Returns:
        MultiFeatureResult com entidades, tabelas, dados financeiros
    """
    key = api_key or EDEN_AI_API_KEY
    if not key:
        logger.warning("[M3B] EDEN_AI_API_KEY não configurada, a saltar features")
        return MultiFeatureResult()

    result = MultiFeatureResult()
    futures = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        # NER: usa texto completo
        if ocr_result.full_text.strip():
            futures["ner"] = executor.submit(
                _extract_ner,
                ocr_result.full_text,
                ocr_result.pages,
                key,
            )

        # Tabelas: usa file_ids das páginas
        if extract_tables and ocr_result.file_ids:
            futures["tables"] = executor.submit(
                _extract_tables,
                ocr_result.file_ids,
                key,
            )

        # Financeiro: usa file_ids
        if extract_financial and ocr_result.file_ids:
            futures["financial"] = executor.submit(
                _extract_financial,
                ocr_result.file_ids,
                key,
            )

        # Recolher resultados
        for name, future in futures.items():
            try:
                res = future.result(timeout=120)
                if name == "ner":
                    entities, cost = res
                    result.entities = entities
                    result.api_cost_usd += cost
                elif name == "tables":
                    tables, cost = res
                    result.tables = tables
                    result.api_cost_usd += cost
                elif name == "financial":
                    fin_data, cost = res
                    result.financial_data = fin_data
                    result.api_cost_usd += cost
            except Exception as e:
                error_msg = f"{name}: {e}"
                result.errors.append(error_msg)
                logger.warning(f"[M3B] Feature {name} falhou: {e}")

    logger.info(
        f"[M3B] Features extraídas: "
        f"{len(result.entities)} entidades, "
        f"{len(result.tables)} tabelas, "
        f"custo Eden AI ${result.api_cost_usd:.4f}"
    )

    return result


def _extract_ner(
    full_text: str,
    pages: list,  # list[OCRPageResult]
    api_key: str,
) -> tuple[list[NamedEntity], float]:
    """Extrair entidades nomeadas via Eden AI NER.

    Returns:
        (entities, cost_usd)
    """
    # Limitar texto a 5000 chars para NER (limite API)
    text = full_text[:5000] if len(full_text) > 5000 else full_text

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": f"text/ner/{NER_PROVIDER}",
        "input": {
            "text": text,
        },
    }

    try:
        response = http_requests.post(
            EDEN_AI_UNIVERSAL_URL,
            headers=headers,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        # Capturar custo real
        cost_usd = 0.0
        try:
            cost_usd = float(data.get("cost", 0))
        except (ValueError, TypeError):
            pass

        entities = []
        items = data.get("output", {}).get("items", [])

        for item in items:
            entity_type = item.get("entity", "UNKNOWN")
            value = item.get("value", "")
            start = item.get("start", 0)
            end = item.get("end", 0)

            # Determinar página
            page_num = _char_to_page(start, pages)

            entities.append(NamedEntity(
                entity_type=entity_type,
                value=value,
                start=start,
                end=end,
                page_num=page_num,
                source="ner_edenai",
            ))

        logger.info(f"[M3B] NER: {len(entities)} entidades detectadas, ${cost_usd:.4f}")
        return entities, cost_usd

    except Exception as e:
        logger.error(f"[M3B] NER falhou: {e}")
        return [], 0.0


def _extract_tables(
    file_ids: dict[int, str],  # {page_num: file_id}
    api_key: str,
) -> tuple[list[ExtractedTable], float]:
    """Extrair tabelas das páginas via Eden AI.

    Returns:
        (tables, cost_usd)
    """
    tables = []
    total_cost = 0.0
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for page_num, file_id in file_ids.items():
        try:
            payload = {
                "model": f"ocr/custom_document_parsing/{TABLE_PROVIDER}",
                "input": {
                    "file": file_id,
                },
            }

            response = http_requests.post(
                EDEN_AI_UNIVERSAL_URL,
                headers=headers,
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()

            # Capturar custo real
            try:
                total_cost += float(data.get("cost", 0))
            except (ValueError, TypeError):
                pass

            output = data.get("output", {})
            # Parse table data from response
            extracted_tables = _parse_table_output(output, page_num)
            tables.extend(extracted_tables)

        except Exception as e:
            logger.warning(f"[M3B] Extracção de tabela falhou página {page_num}: {e}")

    logger.info(f"[M3B] Tabelas: {len(tables)} detectadas, ${total_cost:.4f}")
    return tables, total_cost


def _extract_financial(
    file_ids: dict[int, str],
    api_key: str,
) -> tuple[Optional[dict], float]:
    """Extrair dados financeiros via Eden AI financial parser.

    Returns:
        (financial_data, cost_usd)
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Usar apenas a primeira página (ou página principal)
    first_page = min(file_ids.keys())
    file_id = file_ids[first_page]

    try:
        payload = {
            "model": f"ocr/financial_parser/{TABLE_PROVIDER}",
            "input": {
                "file": file_id,
            },
        }

        response = http_requests.post(
            EDEN_AI_UNIVERSAL_URL,
            headers=headers,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        cost_usd = 0.0
        try:
            cost_usd = float(data.get("cost", 0))
        except (ValueError, TypeError):
            pass

        return data.get("output", {}), cost_usd

    except Exception as e:
        logger.warning(f"[M3B] Análise financeira falhou: {e}")
        return None, 0.0


def _parse_table_output(output: dict, page_num: int) -> list[ExtractedTable]:
    """Parse output de extracção de tabelas do Eden AI."""
    tables = []

    # O output pode variar por provider - tratar casos comuns
    if isinstance(output, dict):
        # Tentar extrair texto estruturado
        text = output.get("text", "")
        if text:
            # Heurística: se contém tabulações ou pipes, é provavelmente uma tabela
            lines = text.strip().split("\n")
            if len(lines) >= 2:
                # Tentar detectar tabelas simples
                table_lines = [l for l in lines if "|" in l or "\t" in l]
                if table_lines:
                    headers_line = table_lines[0]
                    sep = "|" if "|" in headers_line else "\t"
                    headers = [h.strip() for h in headers_line.split(sep) if h.strip()]
                    rows = []
                    for line in table_lines[1:]:
                        # Saltar linhas separadoras
                        if all(c in "-|+ \t" for c in line):
                            continue
                        cells = [c.strip() for c in line.split(sep) if c.strip()]
                        if cells:
                            rows.append(cells)

                    if headers or rows:
                        tables.append(ExtractedTable(
                            page_num=page_num,
                            headers=headers,
                            rows=rows,
                            raw_text=text,
                        ))

    return tables


def _char_to_page(char_pos: int, pages: list) -> int:
    """Mapear posição de carácter para número de página."""
    cumulative = 0
    for page in pages:
        page_len = len(page.consensus_text) + 2  # +2 for \n\n separator
        if char_pos < cumulative + page_len:
            return page.page_num
        cumulative += page_len
    return pages[-1].page_num if pages else 1

# -*- coding: utf-8 -*-
"""
Extração JSON estruturada por página - Anti-alucinação.

Os extratores recebem input JSON com páginas numeradas e devem
devolver JSON estrito com page_num validado.
"""

import json
import logging
import re
from typing import List, Dict

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None


logger = logging.getLogger(__name__)


def _repair_truncated_json(text: str) -> dict | list | None:
    """
    Tenta reparar JSON truncado (cortado pelo max_output tokens).
    Adiciona ] e } em falta para fechar o JSON.
    """
    text = text.strip().rstrip(',')  # Remover trailing comma
    # Contar braces/brackets abertos vs fechados
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')

    if open_braces <= 0 and open_brackets <= 0:
        return None  # Não está truncado

    # Tentar fechar: primeiro ] depois }
    repaired = text
    # Remover última propriedade incompleta (ex: "value": "texto cortad)
    # Procurar último item completo
    last_complete = max(repaired.rfind('}'), repaired.rfind(']'))
    if last_complete > 0:
        repaired = repaired[:last_complete + 1]
        # Recalcular
        open_braces = repaired.count('{') - repaired.count('}')
        open_brackets = repaired.count('[') - repaired.count(']')

    repaired += ']' * open_brackets + '}' * open_braces

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    return None


def _try_json_repair(text: str) -> dict | list | None:
    """Tenta reparar JSON usando a biblioteca json_repair como fallback."""
    if repair_json is None:
        return None
    try:
        repaired = repair_json(text, return_objects=True)
        if repaired:
            logger.info(f"[JSON-REPAIR] json_repair recuperou dados ({type(repaired).__name__})")
            return repaired
    except Exception as e:
        logger.debug(f"[JSON-REPAIR] json_repair falhou: {e}")
    return None


def extract_json_from_text(text: str) -> dict | list | None:
    """
    Tenta extrair JSON válido de texto que pode conter markdown,
    texto explicativo antes/depois, etc.
    Suporta tanto objectos {...} como arrays [...].
    """
    if not text or not text.strip():
        logger.warning(f"[JSON-EXTRACT] Input vazio ou None: {text!r}")
        return None

    text = text.strip()

    logger.debug(f"[JSON-EXTRACT] Input length: {len(text)} chars")
    logger.debug(f"[JSON-EXTRACT] First 500 chars: {text[:500]!r}")
    logger.debug(f"[JSON-EXTRACT] Last 200 chars: {text[-200:]!r}")

    # 1. Tentar parse directo
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.debug(f"[JSON-EXTRACT] Estratégia 1 falhou: {e}")

    # 2. Extrair de bloco markdown ```json ... ```
    md_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if md_match:
        body = md_match.group(1).strip()
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            logger.debug(f"[JSON-EXTRACT] Estratégia 2 falhou: {e}")
            # Tentar json_repair no body do markdown
            repaired = _try_json_repair(body)
            if repaired is not None:
                logger.info(f"[JSON-EXTRACT] Estratégia 2+repair: reparou JSON de markdown")
                return repaired
    else:
        logger.debug(f"[JSON-EXTRACT] Estratégia 2: nenhum bloco markdown encontrado")

    # 2b. Markdown ```json SEM ``` final (JSON truncado pelo max_output)
    md_open = re.search(r'```(?:json)?\s*\n?', text)
    if md_open and not md_match:
        json_body = text[md_open.end():]
        # Tentar repair directo primeiro (funciona com arrays e objectos)
        repaired = _repair_truncated_json(json_body)
        if repaired is not None:
            logger.info(f"[JSON-EXTRACT] Estratégia 2b: reparou JSON truncado de markdown")
            return repaired
        # Tentar json_repair library
        repaired = _try_json_repair(json_body)
        if repaired is not None:
            logger.info(f"[JSON-EXTRACT] Estratégia 2b+repair: json_repair reparou markdown truncado")
            return repaired

    # 3. Encontrar primeiro { e último } (objectos)
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError as e:
            logger.debug(f"[JSON-EXTRACT] Estratégia 3 falhou: {e}")

    # 3b. JSON truncado — { encontrado mas sem } final válido
    if first_brace != -1:
        repaired = _repair_truncated_json(text[first_brace:])
        if repaired is not None:
            logger.info(f"[JSON-EXTRACT] Estratégia 3b: reparou JSON truncado (objecto)")
            return repaired

    # 4. Encontrar primeiro [ e último ] (arrays)
    first_bracket = text.find('[')
    last_bracket = text.rfind(']')
    if first_bracket != -1 and last_bracket > first_bracket:
        try:
            return json.loads(text[first_bracket:last_bracket + 1])
        except json.JSONDecodeError as e:
            logger.debug(f"[JSON-EXTRACT] Estratégia 4 falhou: {e}")

    # 4b. Array truncado — [ encontrado mas sem ] final válido
    if first_bracket != -1:
        repaired = _repair_truncated_json(text[first_bracket:])
        if repaired is not None:
            logger.info(f"[JSON-EXTRACT] Estratégia 4b: reparou JSON truncado (array)")
            return repaired

    if first_brace == -1 and first_bracket == -1:
        logger.debug(f"[JSON-EXTRACT] Estratégia 3+4: nenhum par {{}} ou [] encontrado")

    # 5. Remover prefixo comum e tentar de novo
    for prefix in ["Here is", "Here's", "Below is", "The following", "json\n", "JSON\n"]:
        idx = text.lower().find(prefix.lower())
        if idx != -1:
            remaining = text[idx + len(prefix):].strip()
            try:
                return json.loads(remaining)
            except json.JSONDecodeError as e:
                logger.debug(f"[JSON-EXTRACT] Estratégia 5 falhou (prefix={prefix!r}): {e}")

    # 6. Último recurso: json_repair no texto inteiro
    repaired = _try_json_repair(text)
    if repaired is not None:
        logger.info(f"[JSON-EXTRACT] Estratégia 6: json_repair recuperou do texto inteiro")
        return repaired

    logger.warning(f"[JSON-EXTRACT] TODAS AS ESTRATÉGIAS FALHARAM para input de {len(text)} chars")
    logger.warning(f"[JSON-EXTRACT] Tipo de chars no início: {[c for c in text[:20]]}")

    return None


# ============================================================================
# PROMPT PARA EXTRATORES (JSON ESTRUTURADO)
# ============================================================================

SYSTEM_EXTRATOR_JSON = """És um extrator de informação jurídica especializado em Direito Português.
RECEBES um JSON com lista de páginas numeradas de um documento.
DEVES devolver um JSON ESTRITO no formato especificado.

REGRAS ANTI-ALUCINAÇÃO:
1. APENAS usa page_num que existam no input (verifica a lista)
2. NUNCA inventes páginas que não recebeste
3. Se não encontrares informação numa página, NÃO a incluas nas extractions
4. Se uma página for ilegível/ruído/tabela, coloca-a em pages_unreadable

FORMATO DE OUTPUT OBRIGATÓRIO (JSON):
{
  "extractions": [
    {
      "page_num": 1,
      "facts": ["facto curto e objetivo", "..."],
      "dates": ["YYYY-MM-DD/forma original", "..."],
      "amounts": ["€X.XXX,XX/descrição", "..."],
      "legal_refs": ["DL n.º X/AAAA", "Art. Xº do CC", "..."],
      "visual_mentions": ["assinatura", "carimbo", "tabela", "..."],
      "page_notes": "continuação da página anterior / só assinatura / etc"
    }
  ],
  "pages_unreadable": [
    {"page_num": 5, "reason": "texto ilegível/ruído OCR"}
  ],
  "summary": "resumo geral do documento em 2-3 frases"
}

IMPORTANTE:
- facts: apenas factos relevantes, curtos (máx 100 chars cada)
- dates: formato ISO + forma original entre /
- amounts: valor + descrição do que representa
- legal_refs: referências completas (diploma + artigo)
- visual_mentions: elementos visuais importantes (assinaturas, carimbos, tabelas)
- page_notes: contexto especial da página

REGRA FORENSE: A omissão de datas, valores monetários ou referências legais é erro crítico.
Inclui TODAS as entidades numéricas e legais de cada página, mesmo que pareçam redundantes.
"""


def build_extractor_input(pages_batch: List[Dict]) -> str:
    """
    Constrói input JSON para os extratores.

    Args:
        pages_batch: Lista de dicts com page_num, text, prev_tail, next_head

    Returns:
        String JSON formatada para o prompt
    """
    input_data = {
        "total_pages_in_batch": len(pages_batch),
        "valid_page_nums": [p["page_num"] for p in pages_batch],
        "pages": []
    }

    for page in pages_batch:
        page_entry = {
            "page_num": page["page_num"],
            "text": page["text"][:8000],  # Truncar se muito longo
            "status": page.get("status", "OK"),
        }

        # Adicionar contexto se existir
        if page.get("prev_tail"):
            page_entry["context_previous"] = page["prev_tail"]
        if page.get("next_head"):
            page_entry["context_next"] = page["next_head"]

        input_data["pages"].append(page_entry)

    return json.dumps(input_data, ensure_ascii=False, indent=2)


def parse_extractor_output(
    output: str,
    valid_page_nums: List[int],
    extractor_id: str
) -> Dict:
    """
    Parseia e valida output JSON do extrator.

    Args:
        output: String de output do LLM
        valid_page_nums: Lista de page_nums válidos (do input)
        extractor_id: ID do extrator (E1, E2, E3)

    Returns:
        Dict com extractions validadas e erros
    """
    result = {
        "extractor_id": extractor_id,
        "extractions": [],
        "pages_unreadable": [],
        "pages_covered": [],
        "validation_errors": [],
        "raw_output": output,
    }

    # Tentar extrair JSON do output (robusto: markdown, texto antes/depois, etc.)
    json_data = extract_json_from_text(output)

    if not json_data:
        result["validation_errors"].append("Não foi possível extrair JSON válido do output")
        # Tentar converter markdown para estrutura básica
        result["extractions"] = _fallback_parse_markdown(output, valid_page_nums)
        return result

    # Validar extractions
    extractions = json_data.get("extractions", [])
    for ext in extractions:
        page_num = ext.get("page_num")

        # Validar page_num
        if page_num is None:
            result["validation_errors"].append(f"Extraction sem page_num: {ext}")
            continue

        if page_num not in valid_page_nums:
            result["validation_errors"].append(f"page_num inválido (não existe no input): {page_num}")
            continue

        # Extraction válida
        result["extractions"].append(ext)
        result["pages_covered"].append(page_num)

    # Validar pages_unreadable
    unreadable = json_data.get("pages_unreadable", [])
    for ur in unreadable:
        page_num = ur.get("page_num")
        if page_num in valid_page_nums:
            result["pages_unreadable"].append(ur)

    result["summary"] = json_data.get("summary", "")

    logger.info(f"{extractor_id}: {len(result['extractions'])} extrações válidas, "
                f"{len(result['pages_unreadable'])} ilegíveis, "
                f"{len(result['validation_errors'])} erros")

    return result


def _fallback_parse_markdown(output: str, valid_page_nums: List[int]) -> List[Dict]:
    """
    Fallback: tenta extrair informação de output markdown tradicional.

    Procura padrões como [Página X] ou "Página X:" no texto.
    """
    extractions = []
    current_page = None
    current_content = []

    for line in output.split('\n'):
        # Detetar marcadores de página
        page_match = re.search(r'\[?[Pp]ágina\s*(\d+)\]?:?', line)
        if page_match:
            # Guardar página anterior
            if current_page and current_page in valid_page_nums:
                extractions.append({
                    "page_num": current_page,
                    "facts": current_content,
                    "dates": [],
                    "amounts": [],
                    "legal_refs": [],
                    "visual_mentions": [],
                    "page_notes": "extraído de fallback markdown"
                })

            current_page = int(page_match.group(1))
            current_content = []
        elif current_page and line.strip():
            current_content.append(line.strip()[:100])

    # Guardar última página
    if current_page and current_page in valid_page_nums:
        extractions.append({
            "page_num": current_page,
            "facts": current_content,
            "dates": [],
            "amounts": [],
            "legal_refs": [],
            "visual_mentions": [],
            "page_notes": "extraído de fallback markdown"
        })

    return extractions


def extractions_to_markdown(extractions: List[Dict], extractor_id: str) -> str:
    """
    Converte extrações JSON para formato markdown tradicional.

    Mantém compatibilidade com o pipeline existente.
    """
    lines = [f"# Extração {extractor_id}\n"]

    for ext in extractions:
        lines.append(f"\n## [Página {ext['page_num']}]\n")

        if ext.get("facts"):
            lines.append("### Factos:")
            for fact in ext["facts"]:
                lines.append(f"- {fact}")

        if ext.get("dates"):
            lines.append("\n### Datas:")
            for date in ext["dates"]:
                lines.append(f"- {date}")

        if ext.get("amounts"):
            lines.append("\n### Valores:")
            for amount in ext["amounts"]:
                lines.append(f"- {amount}")

        if ext.get("legal_refs"):
            lines.append("\n### Referências Legais:")
            for ref in ext["legal_refs"]:
                lines.append(f"- {ref}")

        if ext.get("visual_mentions"):
            lines.append("\n### Elementos Visuais:")
            for visual in ext["visual_mentions"]:
                lines.append(f"- {visual}")

        if ext.get("page_notes"):
            lines.append(f"\n*Nota: {ext['page_notes']}*")

        lines.append("\n---")

    return "\n".join(lines)


def merge_extractor_results(results: List[Dict]) -> Dict:
    """
    Combina resultados de múltiplos extratores.

    Args:
        results: Lista de resultados de parse_extractor_output

    Returns:
        Dict com extrações combinadas por página
    """
    merged = {
        "by_page": {},  # page_num -> {E1: ext, E2: ext, ...}
        "all_pages_covered": set(),
        "pages_unreadable_by": {},  # page_num -> {E1: reason, ...}
    }

    for result in results:
        ext_id = result["extractor_id"]

        for ext in result["extractions"]:
            pn = ext["page_num"]
            if pn not in merged["by_page"]:
                merged["by_page"][pn] = {}
            merged["by_page"][pn][ext_id] = ext
            merged["all_pages_covered"].add(pn)

        for ur in result["pages_unreadable"]:
            pn = ur["page_num"]
            if pn not in merged["pages_unreadable_by"]:
                merged["pages_unreadable_by"][pn] = {}
            merged["pages_unreadable_by"][pn][ext_id] = ur.get("reason", "")

    merged["all_pages_covered"] = sorted(merged["all_pages_covered"])

    return merged


def validate_coverage_against_signals(
    page_num: int,
    extractions: Dict,  # {E1: ext, E2: ext, ...}
    detected_signals: Dict  # {dates: [...], values: [...], legal_refs: [...]}
) -> List[str]:
    """
    Valida se os sinais detetados por regex foram extraídos pelos LLMs.

    Args:
        page_num: Número da página
        extractions: Extrações dos LLMs para esta página
        detected_signals: Sinais detetados por regex

    Returns:
        Lista de flags de suspeita
    """
    flags = []

    # Combinar todas as extrações
    all_dates = []
    all_amounts = []
    all_legal_refs = []

    for ext in extractions.values():
        all_dates.extend(ext.get("dates", []))
        all_amounts.extend(ext.get("amounts", []))
        all_legal_refs.extend(ext.get("legal_refs", []))

    # Verificar datas
    if detected_signals.get("dates") and not all_dates:
        flags.append("SUSPEITA_DATAS")

    # Verificar valores
    if detected_signals.get("values") and not all_amounts:
        flags.append("SUSPEITA_VALORES")

    # Verificar referências legais
    if detected_signals.get("legal_refs") and not all_legal_refs:
        flags.append("SUSPEITA_ARTIGOS")

    return flags

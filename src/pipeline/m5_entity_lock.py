# ============================================================================
# Pipeline v4.2 — M5: Travamento de Entidades
# ============================================================================
# Cria um registo imutável de entidades jurídicas encontradas no texto:
# - Datas (regex português)
# - Valores monetários (Euro)
# - Referências legais (artigos, decretos-lei, leis)
# - Números de processo
# - Entidades nomeadas (NER do M3B: pessoas, organizações, locais)
#
# Entidades "travadas" não podem ser alteradas pelo processamento downstream.
# ============================================================================

import re
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================================
# Regex patterns para entidades jurídicas portuguesas
# (reutilizados de pdf_safe.py com extensões)
# ============================================================================

# Datas em formato português
REGEX_DATAS_PT = re.compile(
    r'\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\b'
    r'|'
    r'\b(\d{1,2}\s+de\s+(?:janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+de\s+\d{4})\b',
    re.IGNORECASE
)

# Valores em Euro
REGEX_VALORES_EURO = re.compile(
    r'€\s*[\d\.,]+|\bEUR\s*[\d\.,]+|[\d\.,]+\s*€|[\d\.,]+\s*euros?\b',
    re.IGNORECASE
)

# Artigos legais portugueses
REGEX_ARTIGOS_PT = re.compile(
    r'\b(?:art(?:igo)?\.?\s*\d+[º°]?(?:\s*,?\s*n\.?[º°]?\s*\d+)?)'
    r'|'
    r'\b(?:DL|D\.?L\.?|Decreto[- ]Lei)\s*n\.?[º°]?\s*\d+[/-]\d+'
    r'|'
    r'\b(?:Lei)\s*n\.?[º°]?\s*\d+[/-]\d+'
    r'|'
    r'\bC(?:ódigo)?\s*(?:Civil|Penal|Trabalho|Processo)',
    re.IGNORECASE
)

# Números de processo judicial (requer prefixo "Proc" ou sufixo tribunal como TBXXX)
REGEX_PROCESSO = re.compile(
    r'\b(?:Proc(?:esso)?\.?\s*(?:n\.?[º°]?\s*))'
    r'(\d{1,5}[/\.]\d{2,4}(?:\.\d+)?(?:[A-Z]{2,6})?)'
    r'|'
    r'\b(\d{1,5}/\d{2,4}\.\d+[A-Z]{2,6})\b',
    re.IGNORECASE
)

# CPF/NIF/NIPC portugueses (9 dígitos)
REGEX_NIF = re.compile(
    r'\b(?:NIF|NIPC|CPF|CC)\s*(?:n\.?[º°]?\s*)?(\d{9})\b',
    re.IGNORECASE
)


@dataclass
class LockedEntity:
    """Uma entidade travada (imutável)."""
    entity_id: str
    entity_type: str    # date, amount, legal_ref, process_number, person, org, location, nif
    text: str           # texto exacto como aparece
    normalized: str     # forma normalizada (ISO date, valor numérico, etc.)
    page_num: int       # página (0 se desconhecido)
    start_char: int     # posição absoluta no texto completo
    end_char: int
    source: str         # "regex" ou "ner"
    confidence: float
    locked: bool = True

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "text": self.text,
            "normalized": self.normalized,
            "page_num": self.page_num,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "source": self.source,
            "confidence": self.confidence,
        }


class EntityRegistry:
    """Registo imutável de entidades."""

    def __init__(self):
        self._entities: list[LockedEntity] = []
        self._by_type: dict[str, list[LockedEntity]] = {}

    @property
    def entities(self) -> list[LockedEntity]:
        return list(self._entities)

    @property
    def count(self) -> int:
        return len(self._entities)

    def add(self, entity: LockedEntity) -> None:
        """Adicionar entidade ao registo."""
        self._entities.append(entity)
        self._by_type.setdefault(entity.entity_type, []).append(entity)

    def get_by_type(self, entity_type: str) -> list[LockedEntity]:
        """Obter entidades por tipo."""
        return self._by_type.get(entity_type, [])

    def get_in_range(self, start_char: int, end_char: int) -> list[LockedEntity]:
        """Obter entidades que caem dentro de um range de caracteres."""
        return [
            e for e in self._entities
            if e.start_char < end_char and e.end_char > start_char
        ]

    def get_entity_ids_in_range(self, start_char: int, end_char: int) -> list[str]:
        """Obter IDs de entidades num range."""
        return [e.entity_id for e in self.get_in_range(start_char, end_char)]

    def to_dict(self) -> dict:
        """Serializar registo."""
        return {
            "total_entities": self.count,
            "by_type": {
                t: len(entities) for t, entities in self._by_type.items()
            },
            "entities": [e.to_dict() for e in self._entities],
        }

    def summary(self) -> str:
        """Resumo textual do registo."""
        parts = [f"{len(entities)} {entity_type}" for entity_type, entities in self._by_type.items()]
        return f"EntityRegistry: {self.count} entidades ({', '.join(parts)})"


def lock_entities(
    text: str,
    ner_entities: Optional[list] = None,  # list[NamedEntity] from m3b_multifeature
    page_boundaries: Optional[dict[int, tuple[int, int]]] = None,  # {page_num: (start, end)}
) -> EntityRegistry:
    """
    M5: Travamento de entidades.

    Combina regex + NER para criar registo imutável.

    Args:
        text: texto completo do documento (após limpeza M4)
        ner_entities: entidades do M3B (opcional)
        page_boundaries: mapeamento de páginas para posições de caracteres

    Returns:
        EntityRegistry com todas as entidades travadas
    """
    registry = EntityRegistry()

    if not text:
        return registry

    logger.info(f"[M5] Travamento de entidades: {len(text):,} chars")

    # 1. Regex: Datas
    _extract_by_regex(
        text, REGEX_DATAS_PT, "date", registry, page_boundaries
    )

    # 2. Regex: Valores monetários
    _extract_by_regex(
        text, REGEX_VALORES_EURO, "amount", registry, page_boundaries
    )

    # 3. Regex: Referências legais
    _extract_by_regex(
        text, REGEX_ARTIGOS_PT, "legal_ref", registry, page_boundaries
    )

    # 4. Regex: Números de processo
    _extract_by_regex(
        text, REGEX_PROCESSO, "process_number", registry, page_boundaries
    )

    # 5. Regex: NIF/NIPC
    _extract_by_regex(
        text, REGEX_NIF, "nif", registry, page_boundaries
    )

    regex_count = registry.count

    # 6. NER do M3B
    if ner_entities:
        _merge_ner_entities(registry, ner_entities, text)

    ner_added = registry.count - regex_count

    logger.info(
        f"[M5] Entidades travadas: {registry.count} "
        f"(regex={regex_count}, NER={ner_added})"
    )
    logger.info(f"[M5] {registry.summary()}")

    return registry


def _extract_by_regex(
    text: str,
    pattern: re.Pattern,
    entity_type: str,
    registry: EntityRegistry,
    page_boundaries: Optional[dict[int, tuple[int, int]]],
) -> None:
    """Extrair entidades via regex e adicionar ao registo."""
    for match in pattern.finditer(text):
        # Obter o grupo que deu match (pode ser grupo 1 ou grupo inteiro)
        matched_text = match.group(0)
        start = match.start()
        end = match.end()

        # Determinar página
        page_num = _char_to_page_num(start, page_boundaries)

        entity_id = f"ent_{entity_type}_{uuid.uuid4().hex[:8]}"

        registry.add(LockedEntity(
            entity_id=entity_id,
            entity_type=entity_type,
            text=matched_text,
            normalized=_normalize_entity(matched_text, entity_type),
            page_num=page_num,
            start_char=start,
            end_char=end,
            source="regex",
            confidence=0.95,  # Regex tem alta confiança
        ))


def _merge_ner_entities(
    registry: EntityRegistry,
    ner_entities: list,
    text: str,
) -> None:
    """Merge NER entities, evitando duplicações com regex."""
    # Mapear tipos NER para tipos internos
    type_mapping = {
        "PERSON": "person",
        "PER": "person",
        "ORG": "org",
        "ORGANIZATION": "org",
        "GPE": "location",
        "LOC": "location",
        "LOCATION": "location",
        "DATE": "date",
        "MONEY": "amount",
    }

    for ner_ent in ner_entities:
        entity_type = type_mapping.get(ner_ent.entity_type, ner_ent.entity_type.lower())

        # Verificar se já existe entidade na mesma posição (do regex)
        existing = registry.get_in_range(ner_ent.start, ner_ent.end)
        if existing:
            # Já coberto por regex — saltar
            continue

        entity_id = f"ent_{entity_type}_{uuid.uuid4().hex[:8]}"

        registry.add(LockedEntity(
            entity_id=entity_id,
            entity_type=entity_type,
            text=ner_ent.value,
            normalized=ner_ent.value,
            page_num=ner_ent.page_num,
            start_char=ner_ent.start,
            end_char=ner_ent.end,
            source="ner",
            confidence=ner_ent.confidence if ner_ent.confidence > 0 else 0.7,
        ))


def _char_to_page_num(
    char_pos: int,
    page_boundaries: Optional[dict[int, tuple[int, int]]],
) -> int:
    """Mapear posição de carácter para número de página."""
    if not page_boundaries:
        return 0

    for page_num, (start, end) in page_boundaries.items():
        if start <= char_pos < end:
            return page_num

    return 0


def _normalize_entity(text: str, entity_type: str) -> str:
    """Normalizar entidade para forma canónica."""
    text = text.strip()

    if entity_type == "amount":
        # Remover símbolo e normalizar separadores
        cleaned = re.sub(r'[€EUReuros\s]', '', text, flags=re.IGNORECASE)
        cleaned = cleaned.replace('.', '').replace(',', '.')
        try:
            return f"{float(cleaned):.2f}"
        except ValueError:
            return text

    # Default: retornar como está
    return text

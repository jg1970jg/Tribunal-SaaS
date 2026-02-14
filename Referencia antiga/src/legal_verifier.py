# -*- coding: utf-8 -*-
"""
Verificador de Legislação Portuguesa — Self-Healing.

Verifica citações legais contra o PGDL (pgdlisboa.pt).
Auto-descobre códigos e NIDs dinamicamente.
Só legislação portuguesa (Portugal).
"""

import re
import sqlite3
import hashlib
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple, Set
from dataclasses import dataclass, field
from pathlib import Path
from difflib import SequenceMatcher

import httpx

from src.config import (
    DATABASE_PATH,
    LOG_LEVEL,
    SIMBOLOS_VERIFICACAO,
    API_TIMEOUT,
)

logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CitacaoLegal:
    """Representa uma citação legal normalizada."""
    diploma: str
    artigo: str
    numero: Optional[str] = None
    alinea: Optional[str] = None
    texto_original: str = ""
    texto_normalizado: str = ""

    def to_key(self) -> str:
        parts = [self.diploma, self.artigo]
        if self.numero:
            parts.append(f"n.{self.numero}")
        if self.alinea:
            parts.append(f"al.{self.alinea}")
        return "_".join(parts).replace(" ", "_").lower()


@dataclass
class VerificacaoLegal:
    """Resultado da verificação de uma citação legal."""
    citacao: CitacaoLegal
    existe: bool
    texto_encontrado: Optional[str] = None
    fonte: str = ""
    status: str = ""
    simbolo: str = ""
    aplicabilidade: str = "⚠"
    timestamp: datetime = field(default_factory=datetime.now)
    hash_texto: str = ""
    mensagem: str = ""

    def to_dict(self) -> Dict:
        artigo = self.citacao.artigo.replace("ºº", "º") if self.citacao.artigo else ""
        return {
            "diploma": self.citacao.diploma,
            "artigo": artigo,
            "texto_original": self.citacao.texto_original,
            "texto_normalizado": self.citacao.texto_normalizado,
            "existe": self.existe,
            "texto_encontrado": self.texto_encontrado[:500] if self.texto_encontrado else None,
            "fonte": self.fonte,
            "status": self.status,
            "simbolo": self.simbolo,
            "aplicabilidade": self.aplicabilidade,
            "timestamp": self.timestamp.isoformat(),
            "hash_texto": self.hash_texto,
            "mensagem": self.mensagem,
        }


# ---------------------------------------------------------------------------
# LegalVerifier
# ---------------------------------------------------------------------------

class LegalVerifier:
    """
    Verificador de legislação portuguesa com auto-descoberta.

    Pipeline:
    1. Normaliza a citação (diploma + artigo)
    2. Verifica no cache local SQLite
    3. Resolve diploma → NID no PGDL (auto-descoberta + fallback estático)
    4. Carrega artigos do PGDL e verifica existência
    5. Guarda resultado no cache
    6. V = existe, X = não existe, ! = incerto
    """

    PGDL_BASE = "https://www.pgdlisboa.pt/leis"
    PGDL_ARTICULADO = f"{PGDL_BASE}/lei_mostra_articulado.php"
    PGDL_MAIN = f"{PGDL_BASE}/lei_main.php"

    # --- Mapeamento estático VERIFICADO (fallback se auto-descoberta falhar) ---
    # Cada NID foi confirmado individualmente contra o PGDL.
    _STATIC_NIDS: Dict[str, int] = {
        # Constituição
        "Constituição da República Portuguesa": 4,
        # Códigos fundamentais
        "Código Civil": 775,
        "Código Penal": 109,
        "Código de Processo Civil": 1959,
        "Código de Processo Penal": 199,
        "Código do Trabalho": 1047,
        "Código das Sociedades Comerciais": 524,
        # Processo e procedimento
        "Código de Processo do Trabalho": 487,
        "Código de Processo nos Tribunais Administrativos": 439,
        "Código do Procedimento Administrativo": 2248,
        "Código de Procedimento e de Processo Tributário": 256,
        # Registos e notariado
        "Código do Registo Civil": 682,
        "Código do Registo Comercial": 506,
        "Código do Registo Predial": 488,
        "Código do Notariado": 457,
        # Outros códigos
        "Código da Estrada": 349,
        "Código dos Contratos Públicos": 2063,
        "Código da Insolvência e da Recuperação de Empresas": 85,
        "Código dos Valores Mobiliários": 450,
        "Código do Direito de Autor e dos Direitos Conexos": 484,
        "Código da Propriedade Industrial": 2979,
        "Código da Publicidade": 390,
        "Código das Expropriações": 477,
        "Código Cooperativo": 2469,
        "Código da Execução das Penas e Medidas Privativas da Liberdade": 1147,
        "Código de Justiça Militar": 120,
        # Fiscal (IMI + IMT juntos num só diploma)
        "Códigos do IMI e do IMT": 474,
        "Código dos Impostos Especiais de Consumo": 1598,
        # Outros diplomas frequentes
        "Lei Geral Tributária": 253,
        "Regime Geral das Infracções Tributárias": 259,
        "Regulamento das Custas Processuais": 967,
    }

    # --- Diplomas que NÃO existem no PGDL (verificado) ---
    # Para estes, devolvemos "atenção" em vez de "rejeitado".
    _KNOWN_NO_PGDL: Set[str] = {
        "Código do IRS",
        "Código do IRC",
        "Código do IVA",
        "Código do Imposto de Selo",
        "Código do IUC",
        "Código Comercial",
        "Código dos Regimes Contributivos do Sistema Previdencial de Segurança Social",
    }

    # --- Aliases: abreviatura / variante → nome canónico ---
    _ALIASES: Dict[str, str] = {
        "cc": "Código Civil",
        "código civil": "Código Civil",
        "cp": "Código Penal",
        "código penal": "Código Penal",
        "cpc": "Código de Processo Civil",
        "código de processo civil": "Código de Processo Civil",
        "cpp": "Código de Processo Penal",
        "código de processo penal": "Código de Processo Penal",
        "ct": "Código do Trabalho",
        "código do trabalho": "Código do Trabalho",
        "csc": "Código das Sociedades Comerciais",
        "código das sociedades comerciais": "Código das Sociedades Comerciais",
        "código das sociedades": "Código das Sociedades Comerciais",
        "crp": "Constituição da República Portuguesa",
        "constituição": "Constituição da República Portuguesa",
        "constituição da república": "Constituição da República Portuguesa",
        "constituição da república portuguesa": "Constituição da República Portuguesa",
        "cpa": "Código do Procedimento Administrativo",
        "código do procedimento administrativo": "Código do Procedimento Administrativo",
        "cpta": "Código de Processo nos Tribunais Administrativos",
        "código de processo nos tribunais administrativos": "Código de Processo nos Tribunais Administrativos",
        "cppt": "Código de Procedimento e de Processo Tributário",
        "código de procedimento e de processo tributário": "Código de Procedimento e de Processo Tributário",
        "cpt": "Código de Processo do Trabalho",
        "código de processo do trabalho": "Código de Processo do Trabalho",
        "cire": "Código da Insolvência e da Recuperação de Empresas",
        "código da insolvência": "Código da Insolvência e da Recuperação de Empresas",
        "código da insolvência e da recuperação de empresas": "Código da Insolvência e da Recuperação de Empresas",
        "ccp": "Código dos Contratos Públicos",
        "código dos contratos públicos": "Código dos Contratos Públicos",
        "cvm": "Código dos Valores Mobiliários",
        "código dos valores mobiliários": "Código dos Valores Mobiliários",
        "cdadc": "Código do Direito de Autor e dos Direitos Conexos",
        "código do direito de autor": "Código do Direito de Autor e dos Direitos Conexos",
        "cpi": "Código da Propriedade Industrial",
        "código da propriedade industrial": "Código da Propriedade Industrial",
        "ce": "Código da Estrada",
        "código da estrada": "Código da Estrada",
        "crc": "Código do Registo Civil",
        "código do registo civil": "Código do Registo Civil",
        "crcom": "Código do Registo Comercial",
        "código do registo comercial": "Código do Registo Comercial",
        "crp_pred": "Código do Registo Predial",
        "código do registo predial": "Código do Registo Predial",
        "cn": "Código do Notariado",
        "código do notariado": "Código do Notariado",
        "cpub": "Código da Publicidade",
        "código da publicidade": "Código da Publicidade",
        "cexp": "Código das Expropriações",
        "código das expropriações": "Código das Expropriações",
        "ccoop": "Código Cooperativo",
        "código cooperativo": "Código Cooperativo",
        "cepmpl": "Código da Execução das Penas e Medidas Privativas da Liberdade",
        "cjm": "Código de Justiça Militar",
        "código de justiça militar": "Código de Justiça Militar",
        "cimi": "Códigos do IMI e do IMT",
        "código do imi": "Códigos do IMI e do IMT",
        "código do imt": "Códigos do IMI e do IMT",
        "ciec": "Código dos Impostos Especiais de Consumo",
        "código dos impostos especiais de consumo": "Código dos Impostos Especiais de Consumo",
        "lgt": "Lei Geral Tributária",
        "lei geral tributária": "Lei Geral Tributária",
        "rgit": "Regime Geral das Infracções Tributárias",
        "rcp": "Regulamento das Custas Processuais",
        "regulamento das custas processuais": "Regulamento das Custas Processuais",
        # Códigos sem PGDL
        "cirs": "Código do IRS",
        "código do irs": "Código do IRS",
        "circ": "Código do IRC",
        "código do irc": "Código do IRC",
        "civa": "Código do IVA",
        "código do iva": "Código do IVA",
        "cis": "Código do Imposto de Selo",
        "código do imposto de selo": "Código do Imposto de Selo",
        "ciuc": "Código do IUC",
        "código do iuc": "Código do IUC",
        "ccom": "Código Comercial",
        "código comercial": "Código Comercial",
        "nrau": "NRAU",
    }

    # Padrões regex para normalizar diplomas no texto
    DIPLOMA_PATTERNS = {
        r"c[óo]digo\s*civil": "Código Civil",
        r"\bcc\b": "Código Civil",
        r"c[óo]digo\s*penal": "Código Penal",
        r"\bcp\b": "Código Penal",
        r"c[óo]digo\s*(?:do\s*)?trabalho": "Código do Trabalho",
        r"\bct\b": "Código do Trabalho",
        r"c[óo]digo\s*(?:de\s*)?processo\s*civil": "Código de Processo Civil",
        r"\bcpc\b": "Código de Processo Civil",
        r"c[óo]digo\s*(?:de\s*)?processo\s*penal": "Código de Processo Penal",
        r"\bcpp\b": "Código de Processo Penal",
        r"c[óo]digo\s*(?:de\s*)?processo\s*(?:do\s*)?trabalho": "Código de Processo do Trabalho",
        r"\bcpt\b": "Código de Processo do Trabalho",
        r"c[óo]digo\s*(?:de\s*)?processo\s*(?:nos\s*)?tribunais\s*administrativos": "Código de Processo nos Tribunais Administrativos",
        r"\bcpta\b": "Código de Processo nos Tribunais Administrativos",
        r"c[óo]digo\s*(?:do\s*)?procedimento\s*administrativo": "Código do Procedimento Administrativo",
        r"\bcpa\b": "Código do Procedimento Administrativo",
        r"c[óo]digo\s*(?:de\s*)?procedimento\s*e\s*(?:de\s*)?processo\s*tribut[áa]rio": "Código de Procedimento e de Processo Tributário",
        r"\bcppt\b": "Código de Procedimento e de Processo Tributário",
        r"constitui[çc][ãa]o(?:\s*da\s*rep[úu]blica)?(?:\s*portuguesa)?": "Constituição da República Portuguesa",
        r"\bcrp\b": "Constituição da República Portuguesa",
        r"c[óo]digo\s*(?:das\s*)?sociedades(?:\s*comerciais)?": "Código das Sociedades Comerciais",
        r"\bcsc\b": "Código das Sociedades Comerciais",
        r"c[óo]digo\s*(?:da\s*)?insolv[êe]ncia": "Código da Insolvência e da Recuperação de Empresas",
        r"\bcire\b": "Código da Insolvência e da Recuperação de Empresas",
        r"c[óo]digo\s*(?:dos\s*)?contratos\s*p[úu]blicos": "Código dos Contratos Públicos",
        r"\bccp\b": "Código dos Contratos Públicos",
        r"c[óo]digo\s*(?:dos\s*)?valores\s*mobili[áa]rios": "Código dos Valores Mobiliários",
        r"\bcvm\b": "Código dos Valores Mobiliários",
        r"c[óo]digo\s*(?:da\s*)?estrada": "Código da Estrada",
        r"c[óo]digo\s*comercial": "Código Comercial",
        r"\bccom\b": "Código Comercial",
        r"c[óo]digo\s*(?:do\s*)?registo\s*civil": "Código do Registo Civil",
        r"c[óo]digo\s*(?:do\s*)?registo\s*comercial": "Código do Registo Comercial",
        r"c[óo]digo\s*(?:do\s*)?registo\s*predial": "Código do Registo Predial",
        r"c[óo]digo\s*(?:do\s*)?notariado": "Código do Notariado",
        r"c[óo]digo\s*(?:do\s*)?direito\s*(?:de\s*)?autor": "Código do Direito de Autor e dos Direitos Conexos",
        r"c[óo]digo\s*(?:da\s*)?propriedade\s*industrial": "Código da Propriedade Industrial",
        r"c[óo]digo\s*(?:da\s*)?publicidade": "Código da Publicidade",
        r"c[óo]digo\s*(?:das\s*)?expropria[çc][õo]es": "Código das Expropriações",
        r"c[óo]digo\s*cooperativo": "Código Cooperativo",
        r"\bcirs?\b": "Código do IRS",
        r"c[óo]digo\s*(?:do\s*)?irs": "Código do IRS",
        r"\bcirc\b": "Código do IRC",
        r"c[óo]digo\s*(?:do\s*)?irc": "Código do IRC",
        r"\bciva\b": "Código do IVA",
        r"c[óo]digo\s*(?:do\s*)?iva": "Código do IVA",
        r"c[óo]digo\s*(?:do\s*)?imposto\s*(?:de\s*)?selo": "Código do Imposto de Selo",
        r"\bcis\b": "Código do Imposto de Selo",
        r"c[óo]digo\s*(?:do\s*)?iuc": "Código do IUC",
        r"c[óo]digo\s*(?:do\s*)?imi": "Códigos do IMI e do IMT",
        r"c[óo]digo\s*(?:do\s*)?imt": "Códigos do IMI e do IMT",
        r"lei\s*geral\s*tribut[áa]ria": "Lei Geral Tributária",
        r"\blgt\b": "Lei Geral Tributária",
        r"regulamento\s*(?:das\s*)?custas\s*processuais": "Regulamento das Custas Processuais",
        r"\brcp\b": "Regulamento das Custas Processuais",
        r"\bnrau\b": "NRAU",
        r"\brjue\b": "RJUE",
        r"lei\s*(?:n[.º°]?\s*)?(\d+[/-]\d+)": r"Lei n.º \1",
        r"decreto[- ]lei\s*(?:n[.º°]?\s*)?(\d+[/-]\d+)": r"Decreto-Lei n.º \1",
        r"dl\s*(?:n[.º°]?\s*)?(\d+[/-]\d+)": r"Decreto-Lei n.º \1",
    }

    ARTIGO_PATTERN = re.compile(
        r"art(?:igo)?[.º°]?\s*(\d+)\.?[º°]?(?:-([A-Z]))?",
        re.IGNORECASE,
    )

    # TTL para refresh da auto-descoberta (24h)
    _DISCOVERY_TTL = timedelta(hours=24)

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DATABASE_PATH
        self._init_database()
        self._http_client = httpx.Client(timeout=API_TIMEOUT)
        self._stats = {
            "total_verificacoes": 0,
            "cache_hits": 0,
            "pgdl_lookups": 0,
            "encontrados": 0,
            "nao_encontrados": 0,
        }
        # Mapeamento dinâmico: nome canónico → nid
        self._nid_map: Dict[str, int] = dict(self._STATIC_NIDS)
        # Cache em memória: nid → set de números de artigo
        self._pgdl_articles_cache: Dict[int, set] = {}
        # Timestamp da última auto-descoberta
        self._last_discovery: Optional[datetime] = None
        # Carregar NIDs descobertos anteriormente do SQLite
        self._load_discovered_nids()

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    def _init_database(self):
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS legislacao_cache (
                id TEXT PRIMARY KEY,
                diploma TEXT NOT NULL,
                artigo TEXT NOT NULL,
                numero TEXT,
                alinea TEXT,
                texto TEXT,
                fonte TEXT,
                timestamp TEXT,
                hash TEXT,
                verificado INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_diploma_artigo
            ON legislacao_cache(diploma, artigo)
        """)
        # Tabela para NIDs descobertos dinamicamente
        c.execute("""
            CREATE TABLE IF NOT EXISTS pgdl_nid_map (
                diploma TEXT PRIMARY KEY,
                nid INTEGER NOT NULL,
                discovered_at TEXT NOT NULL,
                source TEXT DEFAULT 'auto'
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"[LEGAL] DB inicializada: {self.db_path}")

    def _load_discovered_nids(self):
        """Carrega NIDs descobertos anteriormente do SQLite."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            c = conn.cursor()
            c.execute("SELECT diploma, nid, discovered_at FROM pgdl_nid_map")
            rows = c.fetchall()
            conn.close()
            for diploma, nid, discovered_at in rows:
                if diploma not in self._nid_map:
                    self._nid_map[diploma] = nid
            if rows:
                logger.info(f"[LEGAL] Carregados {len(rows)} NIDs do cache SQLite")
        except Exception as e:
            logger.warning(f"[LEGAL] Erro ao carregar NIDs do SQLite: {e}")

    def _save_discovered_nid(self, diploma: str, nid: int, source: str = "auto"):
        """Guarda um NID descoberto no SQLite."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO pgdl_nid_map (diploma, nid, discovered_at, source)
                VALUES (?, ?, ?, ?)
            """, (diploma, nid, datetime.now().isoformat(), source))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[LEGAL] Erro ao guardar NID: {e}")

    # ------------------------------------------------------------------
    # Auto-descoberta de NIDs via PGDL Área 32
    # ------------------------------------------------------------------

    def auto_discover_nids(self, force: bool = False):
        """
        Crawl PGDL área 32 (Constituição e Códigos) para descobrir NIDs.
        Self-healing: se o PGDL mudar, re-descobre automaticamente.
        """
        if not force and self._last_discovery:
            if datetime.now() - self._last_discovery < self._DISCOVERY_TTL:
                return  # Ainda dentro do TTL

        try:
            response = self._http_client.get(
                self.PGDL_MAIN,
                params={"codarea": "32"},
                headers={"User-Agent": "Mozilla/5.0 LexForum/2.0"},
                follow_redirects=True,
                timeout=15,
            )
            if response.status_code != 200:
                logger.warning(f"[LEGAL] Auto-descoberta falhou: HTTP {response.status_code}")
                return

            text = response.content.decode("iso-8859-1", errors="replace")

            # Extrair pares nid → nome do diploma
            # Formato: <a href="lei_mostra_articulado.php?nid=XXX..."> NOME - DL/Lei </a>
            pattern = (
                r'lei_mostra_articulado\.php\?nid=(\d+)[^"\'>\s]*["\']?\s*>'
                r'\s*(?:&nbsp;)*\s*(.*?)</a>'
            )
            matches = re.findall(pattern, text, re.DOTALL)

            discovered = 0
            for nid_str, raw_text in matches:
                nid = int(nid_str)
                # Limpar nome: remover HTML, &nbsp;, separar nome do DL
                clean = re.sub(r"<[^>]+>", " ", raw_text).strip()
                clean = clean.replace("&nbsp;", " ").strip()
                # Separar nome do diploma da referência legal (antes do " - ")
                parts = clean.split(" - ", 1)
                diploma_name = parts[0].strip()

                if not diploma_name or len(diploma_name) < 3:
                    continue

                # Guardar no mapa (não substituir estáticos verificados)
                if diploma_name not in self._nid_map:
                    self._nid_map[diploma_name] = nid
                    self._save_discovered_nid(diploma_name, nid, "area32")
                    discovered += 1

            self._last_discovery = datetime.now()
            logger.info(
                f"[LEGAL] Auto-descoberta: {len(matches)} diplomas na área 32, "
                f"{discovered} novos, total mapa={len(self._nid_map)}"
            )

        except Exception as e:
            logger.warning(f"[LEGAL] Erro na auto-descoberta: {e}")

    # ------------------------------------------------------------------
    # Resolução de diploma → NID
    # ------------------------------------------------------------------

    def _resolve_nid(self, diploma: str) -> Optional[int]:
        """
        Resolve um nome de diploma para o NID no PGDL.

        1. Lookup directo no mapa
        2. Lookup por alias
        3. Fuzzy matching (>80% similaridade)
        4. Se falhar tudo, tenta auto-descoberta forçada
        """
        # 1. Directo
        if diploma in self._nid_map:
            return self._nid_map[diploma]

        # 2. Alias (case-insensitive)
        diploma_lower = diploma.lower().strip()
        canonical = self._ALIASES.get(diploma_lower)
        if canonical and canonical in self._nid_map:
            return self._nid_map[canonical]

        # 3. Fuzzy matching contra todos os nomes conhecidos
        best_match = None
        best_score = 0.0
        for known_name in self._nid_map:
            score = SequenceMatcher(None, diploma_lower, known_name.lower()).ratio()
            if score > best_score:
                best_score = score
                best_match = known_name

        if best_match and best_score >= 0.80:
            logger.info(
                f"[LEGAL] Fuzzy match: '{diploma}' → '{best_match}' "
                f"(score={best_score:.2f})"
            )
            return self._nid_map[best_match]

        # 4. Forçar auto-descoberta e tentar de novo
        if not self._last_discovery or (
            datetime.now() - self._last_discovery > timedelta(minutes=5)
        ):
            self.auto_discover_nids(force=True)
            # Retry directo + fuzzy
            if diploma in self._nid_map:
                return self._nid_map[diploma]
            if canonical and canonical in self._nid_map:
                return self._nid_map[canonical]
            for known_name in self._nid_map:
                score = SequenceMatcher(None, diploma_lower, known_name.lower()).ratio()
                if score >= 0.80:
                    return self._nid_map[known_name]

        return None

    # ------------------------------------------------------------------
    # Normalização de citações
    # ------------------------------------------------------------------

    def normalizar_citacao(self, texto: str) -> Optional[CitacaoLegal]:
        texto_lower = texto.lower().strip()

        # Identificar diploma
        diploma = None
        for pattern, replacement in self.DIPLOMA_PATTERNS.items():
            match = re.search(pattern, texto_lower)
            if match:
                if r"\1" in replacement:
                    diploma = re.sub(pattern, replacement, texto_lower, flags=re.IGNORECASE)
                    diploma = diploma.strip()
                else:
                    diploma = replacement
                break

        # Identificar artigo
        artigo_match = self.ARTIGO_PATTERN.search(texto)
        if not artigo_match:
            logger.debug(f"Não foi possível extrair artigo de: {texto}")
            return None

        artigo_num = artigo_match.group(1)
        artigo_letra = artigo_match.group(2) if artigo_match.lastindex and artigo_match.lastindex >= 2 else None

        artigo = f"{artigo_num}º"
        if artigo_letra:
            artigo += f"-{artigo_letra}"
        while "ºº" in artigo:
            artigo = artigo.replace("ºº", "º")

        # Número e alínea
        numero = None
        alinea = None
        num_match = re.search(r"n\.?[º°]?\s*(\d+)", texto_lower)
        if num_match:
            numero = num_match.group(1)
        alinea_match = re.search(r"al[ií]nea\s*([a-z])\)?|al\.\s*([a-z])\)?", texto_lower)
        if alinea_match:
            alinea = (alinea_match.group(1) or alinea_match.group(2)) + ")"

        if not diploma:
            diploma = "Diploma não especificado"

        texto_norm = f"{diploma}, artigo {artigo}"
        if numero:
            texto_norm += f", n.º {numero}"
        if alinea:
            texto_norm += f", alínea {alinea}"

        return CitacaoLegal(
            diploma=diploma,
            artigo=artigo,
            numero=numero,
            alinea=alinea,
            texto_original=texto,
            texto_normalizado=texto_norm,
        )

    def extrair_citacoes(self, texto: str) -> List[CitacaoLegal]:
        citacoes = []
        padroes = [
            r"art(?:igo)?[.º°]?\s*\d+\.?[º°]?(?:-[A-Z])?(?:[,\s]+(?:n\.?[º°]?\s*\d+|al[ií]nea\s*[a-z]\)?))*[,\s]*(?:(?:do|da|dos|das)\s+)?(?:[\s\S]{0,80}?)(?=\.|;|\n|$)",
            r"art(?:igo)?[.º°]?\s*\d+\.?[º°]?(?:-[A-Z])?\s*(?:,\s*n\.?[º°]?\s*\d+)?\s+(?:CC|CPC|CPP|CP|CT|CRP|NRAU|CIRS|CIRC|CIVA|RJUE|CSC|CPA|CPTA|CPPT|CCP|CVM|CIRE|CE|LGT|RCP)\b",
            r"(?:código|lei|decreto|regulamento)[^,.\n]{0,100}art(?:igo)?[.º°]?\s*\d+",
        ]
        encontrados = set()
        for padrao in padroes:
            for match in re.finditer(padrao, texto, re.IGNORECASE):
                trecho = match.group(0).strip()
                if trecho not in encontrados:
                    encontrados.add(trecho)
                    citacao = self.normalizar_citacao(trecho)
                    if citacao:
                        citacoes.append(citacao)
        logger.info(f"[LEGAL] Extraídas {len(citacoes)} citações")
        return citacoes

    # ------------------------------------------------------------------
    # Verificação
    # ------------------------------------------------------------------

    def verificar_citacao(self, citacao: CitacaoLegal) -> VerificacaoLegal:
        self._stats["total_verificacoes"] += 1

        # 1. Cache local
        cache_result = self._verificar_cache(citacao)
        if cache_result:
            self._stats["cache_hits"] += 1
            return cache_result

        # 2. PGDL online
        self._stats["pgdl_lookups"] += 1
        result = self._verificar_pgdl(citacao)

        # 3. Guardar no cache
        self._guardar_cache(citacao, result)
        return result

    def _verificar_cache(self, citacao: CitacaoLegal) -> Optional[VerificacaoLegal]:
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute(
            "SELECT texto, fonte, timestamp, hash FROM legislacao_cache WHERE diploma = ? AND artigo = ?",
            (citacao.diploma, citacao.artigo),
        )
        row = c.fetchone()
        conn.close()

        if row:
            texto, fonte, ts, hash_texto = row
            if texto:
                self._stats["encontrados"] += 1
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=True,
                    texto_encontrado=texto,
                    fonte=f"cache_local ({fonte})",
                    status="aprovado",
                    simbolo=SIMBOLOS_VERIFICACAO["aprovado"],
                    timestamp=datetime.fromisoformat(ts) if ts else datetime.now(),
                    hash_texto=hash_texto or "",
                    mensagem=f"Encontrado no cache (fonte: {fonte})",
                )
            else:
                self._stats["nao_encontrados"] += 1
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="cache_local",
                    status="rejeitado",
                    simbolo=SIMBOLOS_VERIFICACAO["rejeitado"],
                    mensagem="Não encontrado (cache)",
                )
        return None

    def _load_pgdl_articles(self, nid: int) -> set:
        """Carrega todos os artigos de um diploma do PGDL."""
        if nid in self._pgdl_articles_cache:
            return self._pgdl_articles_cache[nid]

        try:
            response = self._http_client.get(
                self.PGDL_ARTICULADO,
                params={"nid": nid, "tabela": "leis"},
                headers={"User-Agent": "Mozilla/5.0 LexForum/2.0"},
                follow_redirects=True,
                timeout=15,
            )
            if response.status_code == 200:
                text = response.content.decode("iso-8859-1", errors="replace")
                arts = re.findall(r"Artigo\s+(\d+)", text)
                article_set = set(arts)
                self._pgdl_articles_cache[nid] = article_set
                logger.info(f"[LEGAL] PGDL nid={nid}: {len(article_set)} artigos")
                return article_set
            else:
                logger.warning(f"[LEGAL] PGDL nid={nid}: HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"[LEGAL] Erro PGDL nid={nid}: {e}")
        return set()

    def _verificar_pgdl(self, citacao: CitacaoLegal) -> VerificacaoLegal:
        """Verifica a citação no PGDL."""
        try:
            # Garantir auto-descoberta na primeira utilização
            if not self._last_discovery:
                self.auto_discover_nids()

            # Verificar se é um diploma sem PGDL
            if citacao.diploma in self._KNOWN_NO_PGDL:
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="sem_pgdl",
                    status="atencao",
                    simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                    mensagem=f"'{citacao.diploma}' não disponível no PGDL (verificação manual)",
                )

            # Resolver NID
            nid = self._resolve_nid(citacao.diploma)

            if not nid:
                logger.info(f"[LEGAL] Diploma sem NID: {citacao.diploma}")
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="diploma_desconhecido",
                    status="atencao",
                    simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                    mensagem=f"Diploma '{citacao.diploma}' não encontrado no PGDL",
                )

            # Carregar artigos
            articles = self._load_pgdl_articles(nid)

            if not articles:
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="pgdl_erro",
                    status="atencao",
                    simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                    mensagem="Não foi possível carregar artigos do PGDL",
                )

            # Extrair número do artigo
            art_num = citacao.artigo.replace("º", "").strip()
            art_match = re.match(r"(\d+)", art_num)
            art_num = art_match.group(1) if art_match else ""

            if art_num in articles:
                self._stats["encontrados"] += 1
                hash_texto = hashlib.md5(
                    f"{citacao.diploma}:{art_num}".encode()
                ).hexdigest()
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=True,
                    texto_encontrado=f"Artigo {citacao.artigo} do {citacao.diploma}",
                    fonte="pgdl_online",
                    status="aprovado",
                    simbolo=SIMBOLOS_VERIFICACAO["aprovado"],
                    hash_texto=hash_texto,
                    mensagem=f"Confirmado no PGDL (nid={nid})",
                )
            else:
                self._stats["nao_encontrados"] += 1
                logger.info(
                    f"[LEGAL] Art. {art_num} não encontrado em {citacao.diploma} "
                    f"(nid={nid}, {len(articles)} artigos)"
                )
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="pgdl_online",
                    status="rejeitado",
                    simbolo=SIMBOLOS_VERIFICACAO["rejeitado"],
                    mensagem=f"Art. {citacao.artigo} não encontrado no {citacao.diploma} (PGDL)",
                )

        except httpx.TimeoutException:
            logger.warning("[LEGAL] Timeout PGDL")
            return VerificacaoLegal(
                citacao=citacao,
                existe=False,
                fonte="timeout",
                status="atencao",
                simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                mensagem="Timeout ao consultar PGDL",
            )
        except Exception as e:
            logger.warning(f"[LEGAL] Erro PGDL: {e}")
            return VerificacaoLegal(
                citacao=citacao,
                existe=False,
                fonte="erro",
                status="atencao",
                simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                mensagem=f"Erro: {str(e)}",
            )

    def _guardar_cache(self, citacao: CitacaoLegal, resultado: VerificacaoLegal):
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO legislacao_cache
            (id, diploma, artigo, numero, alinea, texto, fonte, timestamp, hash, verificado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            citacao.to_key(),
            citacao.diploma,
            citacao.artigo,
            citacao.numero,
            citacao.alinea,
            resultado.texto_encontrado,
            resultado.fonte,
            datetime.now().isoformat(),
            resultado.hash_texto,
            1 if resultado.existe else 0,
        ))
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def verificar_multiplas(self, citacoes: List[CitacaoLegal]) -> List[VerificacaoLegal]:
        return [self.verificar_citacao(c) for c in citacoes]

    def verificar_texto(self, texto: str) -> Tuple[List[CitacaoLegal], List[VerificacaoLegal]]:
        citacoes = self.extrair_citacoes(texto)
        verificacoes = self.verificar_multiplas(citacoes)
        return citacoes, verificacoes

    def gerar_relatorio(self, verificacoes: List[VerificacaoLegal]) -> str:
        linhas = [
            "=" * 60,
            "RELATÓRIO DE VERIFICAÇÃO LEGAL",
            "=" * 60,
            f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            f"Total: {len(verificacoes)}",
            "",
        ]
        aprovadas = [v for v in verificacoes if v.status == "aprovado"]
        rejeitadas = [v for v in verificacoes if v.status == "rejeitado"]
        atencao = [v for v in verificacoes if v.status == "atencao"]
        linhas.extend([
            f"V Aprovadas: {len(aprovadas)}",
            f"X Rejeitadas: {len(rejeitadas)}",
            f"! Atenção: {len(atencao)}",
            "",
            "-" * 60,
        ])
        for v in verificacoes:
            linhas.extend([
                f"\n{v.simbolo} {v.citacao.texto_normalizado}",
                f"   Status: {v.status.upper()}",
                f"   Fonte: {v.fonte}",
                f"   Aplicabilidade: {v.aplicabilidade}",
                f"   {v.mensagem}",
            ])
            if v.texto_encontrado:
                linhas.append(f"   Texto: {v.texto_encontrado[:200]}...")
        linhas.extend([
            "",
            "-" * 60,
            "NOTA: Aplicabilidade ao caso requer análise humana",
            "=" * 60,
        ])
        return "\n".join(linhas)

    def health_check(self) -> Dict[str, Any]:
        """Verifica se o PGDL está acessível e o sistema funciona."""
        result = {
            "pgdl_online": False,
            "nids_conhecidos": len(self._nid_map),
            "artigos_em_cache": len(self._pgdl_articles_cache),
            "ultima_descoberta": self._last_discovery.isoformat() if self._last_discovery else None,
        }
        try:
            # Testar com Código Civil (nid=775) — artigo 1 deve existir
            response = self._http_client.get(
                self.PGDL_ARTICULADO,
                params={"nid": 775, "tabela": "leis"},
                headers={"User-Agent": "Mozilla/5.0 LexForum/2.0"},
                follow_redirects=True,
                timeout=10,
            )
            if response.status_code == 200:
                text = response.content.decode("iso-8859-1", errors="replace")
                if "Artigo 1" in text:
                    result["pgdl_online"] = True
        except Exception:
            pass
        return result

    def get_stats(self) -> Dict:
        return {**self._stats, "nids_conhecidos": len(self._nid_map)}

    def close(self):
        self._http_client.close()


# ---------------------------------------------------------------------------
# Singleton e funções de conveniência
# ---------------------------------------------------------------------------

_global_verifier: Optional[LegalVerifier] = None


def get_legal_verifier() -> LegalVerifier:
    global _global_verifier
    if _global_verifier is None:
        _global_verifier = LegalVerifier()
    return _global_verifier


def verificar_citacao_legal(texto: str) -> Optional[VerificacaoLegal]:
    verifier = get_legal_verifier()
    citacao = verifier.normalizar_citacao(texto)
    if citacao:
        return verifier.verificar_citacao(citacao)
    return None

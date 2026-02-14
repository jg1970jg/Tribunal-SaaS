# -*- coding: utf-8 -*-
"""
Verificador de Legislação Portuguesa.
Normaliza citações, verifica existência no DRE, gere cache local.
"""

import re
import sqlite3
import hashlib
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from pathlib import Path
import json

import httpx
from bs4 import BeautifulSoup

from src.config import (
    DATABASE_PATH,
    DRE_BASE_URL,
    DRE_SEARCH_URL,
    LOG_LEVEL,
    SIMBOLOS_VERIFICACAO,
    API_TIMEOUT,
)

# Configurar logging
logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger(__name__)


@dataclass
class CitacaoLegal:
    """Representa uma citação legal normalizada."""
    diploma: str  # Ex: "Código Civil"
    artigo: str   # Ex: "483º"
    numero: Optional[str] = None  # Ex: "1"
    alinea: Optional[str] = None  # Ex: "a)"
    texto_original: str = ""
    texto_normalizado: str = ""

    def to_key(self) -> str:
        """Gera uma chave única para a citação."""
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
    fonte: str = ""  # "local", "dre_online", "não_encontrado"
    status: str = ""  # "aprovado", "rejeitado", "atencao"
    simbolo: str = ""
    aplicabilidade: str = "⚠"  # Sempre ⚠ para aplicabilidade ao caso
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


class LegalVerifier:
    """
    Verifica citações legais portuguesas.

    Pipeline:
    1. Normaliza a citação (diploma + artigo)
    2. Verifica no cache local SQLite
    3. Se não encontrar, busca no PGDL (pgdlisboa.pt) — HTML real
    4. Guarda resultado no cache com timestamp e hash
    5. Retorna status: V (existe), X (não existe), ! (incerto)
    6. Aplicabilidade ao caso é sempre !
    """

    # Mapeamento diploma → NID no PGDL (pgdlisboa.pt)
    PGDL_NIDS = {
        "Código Civil": 775,
        "Código Penal": 109,
        "Código de Processo Civil": 1959,
        "Código de Processo Penal": 120,
        "Código do Trabalho": 199,
        "Código das Sociedades Comerciais": 524,
        "Código Comercial": 584,
        "Constituição da República Portuguesa": 652,
    }

    PGDL_URL = "https://www.pgdlisboa.pt/leis/lei_mostra_articulado.php"

    # Cache em memória dos artigos já carregados por NID
    _pgdl_articles_cache: Dict[int, set] = {}

    # Padrões de normalização para diplomas portugueses
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
        r"constitui[çc][ãa]o": "Constituição da República Portuguesa",
        r"\bcrp\b": "Constituição da República Portuguesa",
        r"c[óo]digo\s*comercial": "Código Comercial",
        r"\bccom\b": "Código Comercial",
        r"\bnrau\b": "Lei n.º 6/2006",
        r"\bcirs?\b": "Código do IRS",
        r"c[óo]digo\s*(?:do\s*)?irs": "Código do IRS",
        r"\brjue\b": "Decreto-Lei n.º 555/99",
        r"\bcsc\b": "Código das Sociedades Comerciais",
        r"c[óo]digo\s*(?:das\s*)?sociedades": "Código das Sociedades Comerciais",
        r"\bcpa\b": "Código do Procedimento Administrativo",
        r"lei\s*(?:n[.º°]?\s*)?(\d+[/-]\d+)": r"Lei n.º \1",
        r"decreto[- ]lei\s*(?:n[.º°]?\s*)?(\d+[/-]\d+)": r"Decreto-Lei n.º \1",
        r"dl\s*(?:n[.º°]?\s*)?(\d+[/-]\d+)": r"Decreto-Lei n.º \1",
    }

    # Padrão para extrair artigos
    ARTIGO_PATTERN = re.compile(
        r"art(?:igo)?[.º°]?\s*(\d+)\.?[º°]?(?:-([A-Z]))?",
        re.IGNORECASE
    )

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DATABASE_PATH
        self._init_database()
        self._http_client = httpx.Client(timeout=API_TIMEOUT)
        self._stats = {
            "total_verificacoes": 0,
            "cache_hits": 0,
            "dre_lookups": 0,
            "encontrados": 0,
            "nao_encontrados": 0,
        }

    def _init_database(self):
        """Inicializa a base de dados de cache."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
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

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_diploma_artigo
            ON legislacao_cache(diploma, artigo)
        """)

        conn.commit()
        conn.close()

        logger.info(f"Base de dados inicializada: {self.db_path}")

    def normalizar_citacao(self, texto: str) -> Optional[CitacaoLegal]:
        """
        Normaliza uma citação legal do texto.

        Args:
            texto: Texto contendo a citação (ex: "art. 483º do CC")

        Returns:
            CitacaoLegal normalizada ou None se não conseguir parsear
        """
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
        artigo_letra = artigo_match.group(2) if artigo_match.lastindex >= 2 else None

        # Garantir que artigo termina com exactamente um º (nunca ºº)
        artigo = f"{artigo_num}º"
        if artigo_letra:
            artigo += f"-{artigo_letra}"
        # Sanitizar: remover qualquer duplicação de º
        while "ºº" in artigo:
            artigo = artigo.replace("ºº", "º")

        # Extrair número e alínea se presentes
        numero = None
        alinea = None

        num_match = re.search(r"n\.?[º°]?\s*(\d+)", texto_lower)
        if num_match:
            numero = num_match.group(1)

        alinea_match = re.search(r"al[ií]nea\s*([a-z])\)?|al\.\s*([a-z])\)?", texto_lower)
        if alinea_match:
            alinea = (alinea_match.group(1) or alinea_match.group(2)) + ")"

        # Se não identificou diploma, tentar inferir
        if not diploma:
            diploma = "Diploma não especificado"

        # Construir texto normalizado
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
        """Extrai todas as citações legais de um texto."""
        citacoes = []

        # Padrão abrangente para encontrar citações
        padroes = [
            # Padrão principal: art. Nº + contexto (do/da/n.º/,) + até 80 chars para capturar diploma
            r"art(?:igo)?[.º°]?\s*\d+\.?[º°]?(?:-[A-Z])?(?:[,\s]+(?:n\.?[º°]?\s*\d+|al[ií]nea\s*[a-z]\)?))*[,\s]*(?:(?:do|da|dos|das)\s+)?(?:[\s\S]{0,80}?)(?=\.|;|\n|$)",
            # Padrão simples: art. Nº + abreviatura diploma (CC, CPC, CT, etc.) logo a seguir
            r"art(?:igo)?[.º°]?\s*\d+\.?[º°]?(?:-[A-Z])?\s*(?:,\s*n\.?[º°]?\s*\d+)?\s+(?:CC|CPC|CPP|CP|CT|CRP|NRAU|CIRS|RJUE|CSC|CPA)\b",
            # Padrão inverso: diploma + art.
            r"(?:código|lei|decreto)[^,.\n]{0,100}art(?:igo)?[.º°]?\s*\d+",
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

        logger.info(f"Extraídas {len(citacoes)} citações do texto")
        return citacoes

    def verificar_citacao(self, citacao: CitacaoLegal) -> VerificacaoLegal:
        """
        Verifica se uma citação legal existe.

        Pipeline:
        1. Verifica cache local
        2. Se não encontrar, busca no DRE
        3. Guarda no cache
        """
        self._stats["total_verificacoes"] += 1

        # 1. Verificar cache local
        cache_result = self._verificar_cache(citacao)
        if cache_result:
            self._stats["cache_hits"] += 1
            return cache_result

        # 2. Buscar no DRE online
        self._stats["dre_lookups"] += 1
        dre_result = self._verificar_dre(citacao)

        # 3. Guardar no cache
        self._guardar_cache(citacao, dre_result)

        return dre_result

    def _verificar_cache(self, citacao: CitacaoLegal) -> Optional[VerificacaoLegal]:
        """Verifica se a citação existe no cache local."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            SELECT texto, fonte, timestamp, hash
            FROM legislacao_cache
            WHERE diploma = ? AND artigo = ?
        """, (citacao.diploma, citacao.artigo))

        row = cursor.fetchone()
        conn.close()

        if row:
            texto, fonte, timestamp, hash_texto = row

            if texto:
                self._stats["encontrados"] += 1
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=True,
                    texto_encontrado=texto,
                    fonte=f"cache_local ({fonte})",
                    status="aprovado",
                    simbolo=SIMBOLOS_VERIFICACAO["aprovado"],
                    timestamp=datetime.fromisoformat(timestamp) if timestamp else datetime.now(),
                    hash_texto=hash_texto or "",
                    mensagem=f"Legislação encontrada no cache local (fonte original: {fonte})",
                )
            else:
                self._stats["nao_encontrados"] += 1
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="cache_local",
                    status="rejeitado",
                    simbolo=SIMBOLOS_VERIFICACAO["rejeitado"],
                    mensagem="Legislação não encontrada (cache)",
                )

        return None

    def _load_pgdl_articles(self, nid: int) -> set:
        """Carrega todos os artigos de um diploma do PGDL para cache em memória."""
        if nid in self._pgdl_articles_cache:
            return self._pgdl_articles_cache[nid]

        try:
            response = self._http_client.get(
                self.PGDL_URL,
                params={"nid": nid, "tabela": "leis"},
                headers={"User-Agent": "Mozilla/5.0 LexForum/2.0"},
                follow_redirects=True,
            )
            if response.status_code == 200:
                text = response.content.decode("iso-8859-1", errors="replace")
                # Extrair todos os números de artigo
                arts = re.findall(r"Artigo\s+(\d+)", text)
                article_set = set(arts)
                self._pgdl_articles_cache[nid] = article_set
                logger.info(f"[LEGAL] PGDL nid={nid}: {len(article_set)} artigos carregados")
                return article_set
            else:
                logger.warning(f"[LEGAL] PGDL nid={nid} retornou HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"[LEGAL] Erro ao carregar PGDL nid={nid}: {e}")

        return set()

    def _verificar_dre(self, citacao: CitacaoLegal) -> VerificacaoLegal:
        """Verifica a citação no PGDL (pgdlisboa.pt) — HTML real."""
        try:
            # Obter NID do diploma
            nid = self.PGDL_NIDS.get(citacao.diploma)

            if not nid:
                # Diploma desconhecido — não conseguimos verificar
                logger.info(f"[LEGAL] Diploma sem NID: {citacao.diploma}")
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="diploma_desconhecido",
                    status="atencao",
                    simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                    mensagem=f"Diploma '{citacao.diploma}' não tem mapeamento PGDL",
                )

            # Carregar artigos do diploma (com cache)
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

            # Extrair número do artigo (sem º)
            art_num = citacao.artigo.replace("º", "").replace("-", "").strip()
            # Suporte para artigos com letra (ex: "405-A" → "405")
            art_num = re.match(r"(\d+)", art_num)
            art_num = art_num.group(1) if art_num else ""

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
                    mensagem=f"Confirmado no PGDL: {citacao.diploma}, Artigo {citacao.artigo}",
                )
            else:
                self._stats["nao_encontrados"] += 1
                logger.info(f"[LEGAL] Artigo {art_num} não encontrado em {citacao.diploma} (nid={nid}, {len(articles)} artigos)")
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="pgdl_online",
                    status="rejeitado",
                    simbolo=SIMBOLOS_VERIFICACAO["rejeitado"],
                    mensagem=f"Artigo {citacao.artigo} não encontrado no {citacao.diploma} (PGDL)",
                )

        except httpx.TimeoutException:
            logger.warning(f"[LEGAL] Timeout ao consultar PGDL")
            return VerificacaoLegal(
                citacao=citacao,
                existe=False,
                fonte="timeout",
                status="atencao",
                simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                mensagem="Timeout ao consultar PGDL",
            )

        except Exception as e:
            logger.warning(f"[LEGAL] Erro ao verificar no PGDL: {e}")
            return VerificacaoLegal(
                citacao=citacao,
                existe=False,
                fonte="erro",
                status="atencao",
                simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                mensagem=f"Erro ao verificar: {str(e)}",
            )

    def _guardar_cache(self, citacao: CitacaoLegal, resultado: VerificacaoLegal):
        """Guarda o resultado da verificação no cache."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
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

        logger.debug(f"Cache atualizado: {citacao.to_key()}")

    def verificar_multiplas(self, citacoes: List[CitacaoLegal]) -> List[VerificacaoLegal]:
        """Verifica múltiplas citações."""
        return [self.verificar_citacao(c) for c in citacoes]

    def verificar_texto(self, texto: str) -> Tuple[List[CitacaoLegal], List[VerificacaoLegal]]:
        """
        Extrai e verifica todas as citações de um texto.

        Returns:
            Tupla (citações extraídas, verificações)
        """
        citacoes = self.extrair_citacoes(texto)
        verificacoes = self.verificar_multiplas(citacoes)
        return citacoes, verificacoes

    def gerar_relatorio(self, verificacoes: List[VerificacaoLegal]) -> str:
        """Gera um relatório de verificação legal."""
        linhas = [
            "=" * 60,
            "RELATÓRIO DE VERIFICAÇÃO LEGAL",
            "=" * 60,
            f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            f"Total de citações verificadas: {len(verificacoes)}",
            "",
        ]

        aprovadas = [v for v in verificacoes if v.status == "aprovado"]
        rejeitadas = [v for v in verificacoes if v.status == "rejeitado"]
        atencao = [v for v in verificacoes if v.status == "atencao"]

        linhas.extend([
            f"✓ Aprovadas: {len(aprovadas)}",
            f"✗ Rejeitadas: {len(rejeitadas)}",
            f"⚠ Atenção: {len(atencao)}",
            "",
            "-" * 60,
        ])

        for v in verificacoes:
            linhas.extend([
                f"\n{v.simbolo} {v.citacao.texto_normalizado}",
                f"   Status: {v.status.upper()}",
                f"   Fonte: {v.fonte}",
                f"   Aplicabilidade ao caso: {v.aplicabilidade}",
                f"   Mensagem: {v.mensagem}",
            ])
            if v.texto_encontrado:
                linhas.append(f"   Texto: {v.texto_encontrado[:200]}...")

        linhas.extend([
            "",
            "-" * 60,
            "NOTA: Aplicabilidade ao caso é sempre ⚠ (requer análise humana)",
            "=" * 60,
        ])

        return "\n".join(linhas)

    def get_stats(self) -> Dict:
        """Retorna estatísticas."""
        return self._stats.copy()

    def close(self):
        """Fecha recursos."""
        self._http_client.close()


# Instância global
_global_verifier: Optional[LegalVerifier] = None


def get_legal_verifier() -> LegalVerifier:
    """Retorna o verificador legal global."""
    global _global_verifier
    if _global_verifier is None:
        _global_verifier = LegalVerifier()
    return _global_verifier


def verificar_citacao_legal(texto: str) -> Optional[VerificacaoLegal]:
    """Função de conveniência para verificar uma citação."""
    verifier = get_legal_verifier()
    citacao = verifier.normalizar_citacao(texto)
    if citacao:
        return verifier.verificar_citacao(citacao)
    return None

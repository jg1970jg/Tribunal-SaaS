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
        return {
            "diploma": self.citacao.diploma,
            "artigo": self.citacao.artigo,
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
    3. Se não encontrar, busca no DRE online
    4. Guarda resultado no cache com timestamp e hash
    5. Retorna status: ✓ (existe), ✗ (não existe), ⚠ (incerto)
    6. Aplicabilidade ao caso é sempre ⚠
    """

    # Padrões de normalização para diplomas portugueses
    DIPLOMA_PATTERNS = {
        r"c[óo]digo\s*civil": "Código Civil",
        r"cc": "Código Civil",
        r"c[óo]digo\s*penal": "Código Penal",
        r"cp": "Código Penal",
        r"c[óo]digo\s*(?:do\s*)?trabalho": "Código do Trabalho",
        r"ct": "Código do Trabalho",
        r"c[óo]digo\s*(?:de\s*)?processo\s*civil": "Código de Processo Civil",
        r"cpc": "Código de Processo Civil",
        r"c[óo]digo\s*(?:de\s*)?processo\s*penal": "Código de Processo Penal",
        r"cpp": "Código de Processo Penal",
        r"constitui[çc][ãa]o": "Constituição da República Portuguesa",
        r"crp": "Constituição da República Portuguesa",
        r"c[óo]digo\s*comercial": "Código Comercial",
        r"ccom": "Código Comercial",
        r"lei\s*(?:n[.º°]?\s*)?(\d+[/-]\d+)": r"Lei n.º \1",
        r"decreto[- ]lei\s*(?:n[.º°]?\s*)?(\d+[/-]\d+)": r"Decreto-Lei n.º \1",
        r"dl\s*(?:n[.º°]?\s*)?(\d+[/-]\d+)": r"Decreto-Lei n.º \1",
    }

    # Padrão para extrair artigos
    ARTIGO_PATTERN = re.compile(
        r"art(?:igo)?[.º°]?\s*(\d+)[.º°]?(?:-([A-Z]))?",
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

        artigo = f"{artigo_num}º"
        if artigo_letra:
            artigo += f"-{artigo_letra}"

        # Extrair número e alínea se presentes
        numero = None
        alinea = None

        num_match = re.search(r"n[.º°]?\s*(\d+)", texto_lower)
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
            r"art(?:igo)?[.º°]?\s*\d+[.º°]?(?:-[A-Z])?\s*(?:(?:do|da|n[.º°])[\s\S]{0,50})?",
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

    def _verificar_dre(self, citacao: CitacaoLegal) -> VerificacaoLegal:
        """Busca a citação no DRE online."""
        try:
            # Construir query de busca
            query = f"{citacao.diploma} artigo {citacao.artigo}"

            # Fazer requisição ao DRE
            response = self._http_client.get(
                DRE_SEARCH_URL,
                params={"q": query, "s": "1"},
                follow_redirects=True,
            )

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "lxml")

                # Procurar resultados
                resultados = soup.find_all("div", class_="result-item")

                if resultados:
                    # Encontrou algo relacionado
                    primeiro = resultados[0]
                    titulo = primeiro.find("h3") or primeiro.find("a")
                    texto_preview = primeiro.get_text(strip=True)[:500]

                    # Verificar se o artigo específico está mencionado
                    if citacao.artigo.replace("º", "") in texto_preview:
                        self._stats["encontrados"] += 1
                        hash_texto = hashlib.md5(texto_preview.encode()).hexdigest()

                        return VerificacaoLegal(
                            citacao=citacao,
                            existe=True,
                            texto_encontrado=texto_preview,
                            fonte="dre_online",
                            status="aprovado",
                            simbolo=SIMBOLOS_VERIFICACAO["aprovado"],
                            hash_texto=hash_texto,
                            mensagem=f"Encontrado no DRE: {titulo.get_text(strip=True) if titulo else 'Resultado'}",
                        )

                # Não encontrou o artigo específico, mas pode existir
                logger.info(f"Artigo não confirmado no DRE: {citacao.texto_normalizado}")

                self._stats["nao_encontrados"] += 1
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="dre_online",
                    status="atencao",
                    simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                    mensagem="Diploma pode existir mas artigo não confirmado no DRE",
                )

            else:
                logger.warning(f"DRE retornou status {response.status_code}")
                return VerificacaoLegal(
                    citacao=citacao,
                    existe=False,
                    fonte="erro_dre",
                    status="atencao",
                    simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                    mensagem=f"Erro ao consultar DRE: HTTP {response.status_code}",
                )

        except httpx.TimeoutException:
            logger.error("Timeout ao consultar DRE")
            return VerificacaoLegal(
                citacao=citacao,
                existe=False,
                fonte="timeout",
                status="atencao",
                simbolo=SIMBOLOS_VERIFICACAO["atencao"],
                mensagem="Timeout ao consultar DRE - não foi possível verificar",
            )

        except Exception as e:
            logger.error(f"Erro ao verificar no DRE: {e}")
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

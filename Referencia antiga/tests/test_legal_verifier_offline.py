# -*- coding: utf-8 -*-
"""
TRIBUNAL GOLDENMASTER - Testes do Legal Verifier (Offline)
============================================================
Testes para verificação de legislação SEM chamadas online.
Usa cache local e fixtures.
============================================================
"""

import pytest
from pathlib import Path


class TestCitacaoLegal:
    """Testes para normalização de citações legais."""

    def test_normalizar_codigo_civil(self):
        """Testa normalização de citação do Código Civil."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        citacao = verifier.normalizar_citacao("art. 483º do Código Civil")

        assert citacao is not None
        assert citacao.diploma == "Código Civil"
        assert citacao.artigo == "483º"

    def test_normalizar_codigo_civil_abreviado(self):
        """Testa normalização de CC (abreviado)."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        citacao = verifier.normalizar_citacao("artigo 1022º CC")

        assert citacao is not None
        assert citacao.diploma == "Código Civil"
        assert citacao.artigo == "1022º"

    def test_normalizar_codigo_trabalho(self):
        """Testa normalização de Código do Trabalho."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        citacao = verifier.normalizar_citacao("art. 127º do Código do Trabalho")

        assert citacao is not None
        assert "Trabalho" in citacao.diploma
        assert citacao.artigo == "127º"

    def test_normalizar_decreto_lei(self):
        """Testa normalização de Decreto-Lei."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        # Nota: O padrão regex suporta "DL 118/2013" (sem "n.º")
        citacao = verifier.normalizar_citacao("DL 118/2013 artigo 5º")

        assert citacao is not None
        assert "Decreto-Lei" in citacao.diploma
        assert citacao.artigo == "5º"

    def test_normalizar_lei(self):
        """Testa normalização de Lei."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        # Nota: O padrão regex suporta "Lei 6/2006" (sem "n.º")
        citacao = verifier.normalizar_citacao("Lei 6/2006 artigo 10º")

        assert citacao is not None
        assert "Lei" in citacao.diploma
        assert citacao.artigo == "10º"

    def test_normalizar_com_numero_e_alinea(self):
        """Testa normalização com número e alínea."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        # Nota: O padrão regex suporta "nº 1" (um caracter após n)
        citacao = verifier.normalizar_citacao("artigo 127º nº 1 alínea a) do CT")

        assert citacao is not None
        assert citacao.artigo == "127º"
        assert citacao.numero == "1"
        assert citacao.alinea == "a)"

    def test_texto_nao_reconhecido(self):
        """Testa que texto sem citação retorna None."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        citacao = verifier.normalizar_citacao("texto sem citação legal")

        assert citacao is None


class TestExtrairCitacoes:
    """Testes para extração de citações de texto."""

    def test_extrair_multiplas_citacoes(self):
        """Testa extração de múltiplas citações."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        texto = """
        Nos termos do artigo 483º do Código Civil e do artigo 1022º CC,
        bem como da Lei n.º 6/2006, artigo 10º.
        """
        citacoes = verifier.extrair_citacoes(texto)

        assert len(citacoes) >= 2  # Deve encontrar pelo menos 2

    def test_extrair_sem_citacoes(self):
        """Testa texto sem citações."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        texto = "Este texto não contém citações legais."
        citacoes = verifier.extrair_citacoes(texto)

        assert len(citacoes) == 0


class TestVerificacaoLegal:
    """Testes para estrutura de verificação."""

    def test_verificacao_legal_to_dict(self, sample_citacao):
        """Testa serialização de VerificacaoLegal."""
        from src.legal_verifier import LegalVerifier, VerificacaoLegal

        verifier = LegalVerifier()
        citacao = verifier.normalizar_citacao(sample_citacao)

        if citacao:
            verificacao = VerificacaoLegal(
                citacao=citacao,
                existe=True,
                texto_encontrado="Texto do artigo encontrado",
                fonte="teste",
                status="aprovado",
                simbolo="✓",
                mensagem="Verificação de teste",
            )

            data = verificacao.to_dict()

            assert "diploma" in data
            assert "artigo" in data
            assert "existe" in data
            assert "status" in data
            assert "simbolo" in data

    def test_citacao_to_key(self, sample_citacao):
        """Testa geração de chave única para citação."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        citacao = verifier.normalizar_citacao(sample_citacao)

        if citacao:
            key = citacao.to_key()
            assert key is not None
            assert len(key) > 0
            assert "_" in key  # Formato: diploma_artigo


class TestGerarRelatorio:
    """Testes para geração de relatório."""

    def test_gerar_relatorio_vazio(self):
        """Testa relatório sem verificações."""
        from src.legal_verifier import LegalVerifier

        verifier = LegalVerifier()
        relatorio = verifier.gerar_relatorio([])

        assert "RELATÓRIO" in relatorio
        assert "Total de citações verificadas: 0" in relatorio

    def test_gerar_relatorio_com_verificacoes(self, sample_citacao):
        """Testa relatório com verificações."""
        from src.legal_verifier import LegalVerifier, VerificacaoLegal

        verifier = LegalVerifier()
        citacao = verifier.normalizar_citacao(sample_citacao)

        if citacao:
            verificacao = VerificacaoLegal(
                citacao=citacao,
                existe=True,
                fonte="teste",
                status="aprovado",
                simbolo="✓",
                mensagem="Teste",
            )

            relatorio = verifier.gerar_relatorio([verificacao])

            assert "RELATÓRIO" in relatorio
            assert "✓ Aprovadas: 1" in relatorio

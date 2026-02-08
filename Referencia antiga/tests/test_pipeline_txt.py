# -*- coding: utf-8 -*-
"""
TRIBUNAL GOLDENMASTER - Testes do Pipeline (TXT)
============================================================
Testes do pipeline com input TXT de fixture.
NÃO depende de internet - usa mocks.
============================================================
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch


class TestPipelineResult:
    """Testes para estrutura PipelineResult."""

    def test_pipeline_result_to_dict(self, sample_documento_content):
        """Testa serialização de PipelineResult."""
        from src.pipeline.processor import PipelineResult

        result = PipelineResult(
            run_id="test_123",
            documento=sample_documento_content,
            area_direito="Civil",
        )

        data = result.to_dict()

        assert data["run_id"] == "test_123"
        assert data["area_direito"] == "Civil"
        assert "documento" in data
        assert "fase1_extracoes" in data
        assert "fase2_auditorias" in data
        assert "fase3_pareceres" in data

    def test_fase_result_to_dict(self):
        """Testa serialização de FaseResult."""
        from src.pipeline.processor import FaseResult

        fase = FaseResult(
            fase="extrator",
            modelo="test/model",
            role="extrator_1",
            conteudo="Conteúdo de teste com informação extraída.",
            tokens_usados=100,
            latencia_ms=500.0,
            sucesso=True,
        )

        data = fase.to_dict()

        assert data["fase"] == "extrator"
        assert data["modelo"] == "test/model"
        assert data["tokens_usados"] == 100
        assert data["sucesso"] is True


class TestTribunalProcessor:
    """Testes para TribunalProcessor."""

    def test_processor_initialization(self):
        """Testa inicialização do processador."""
        from src.pipeline.processor import TribunalProcessor

        processor = TribunalProcessor()

        assert processor.extrator_models is not None
        assert processor.auditor_models is not None
        assert processor.juiz_models is not None
        assert processor.presidente_model is not None

    def test_processor_setup_run(self):
        """Testa setup de uma execução."""
        from src.pipeline.processor import TribunalProcessor

        processor = TribunalProcessor()
        run_id = processor._setup_run()

        assert run_id is not None
        assert len(run_id) > 10  # Formato: YYYYMMDD_HHMMSS_hash
        assert processor._output_dir is not None
        assert processor._output_dir.exists()

    def test_determinar_veredicto_procedente(self):
        """Testa determinação de veredicto PROCEDENTE."""
        from src.pipeline.processor import TribunalProcessor

        processor = TribunalProcessor()
        veredicto, simbolo, status = processor._determinar_veredicto(
            "Considerando todos os factos, julgo o pedido PROCEDENTE."
        )

        assert veredicto == "PROCEDENTE"
        assert simbolo == "✓"
        assert status == "aprovado"

    def test_determinar_veredicto_improcedente(self):
        """Testa determinação de veredicto IMPROCEDENTE."""
        from src.pipeline.processor import TribunalProcessor

        processor = TribunalProcessor()
        veredicto, simbolo, status = processor._determinar_veredicto(
            "Face ao exposto, o pedido é julgado IMPROCEDENTE."
        )

        assert veredicto == "IMPROCEDENTE"
        assert simbolo == "✗"
        assert status == "rejeitado"

    def test_determinar_veredicto_parcialmente_procedente(self):
        """Testa determinação de veredicto PARCIALMENTE PROCEDENTE."""
        from src.pipeline.processor import TribunalProcessor

        processor = TribunalProcessor()
        veredicto, simbolo, status = processor._determinar_veredicto(
            "O pedido é julgado PARCIALMENTE PROCEDENTE."
        )

        assert veredicto == "PARCIALMENTE PROCEDENTE"
        assert simbolo == "⚠"
        assert status == "atencao"


class TestCostController:
    """Testes para controlo de custos."""

    def test_cost_controller_initialization(self):
        """Testa inicialização do controlador de custos."""
        from src.cost_controller import CostController

        controller = CostController(
            run_id="test_123",
            budget_limit_usd=1.0,
            token_limit=10000,
        )

        assert controller.budget_limit == 1.0
        assert controller.token_limit == 10000
        assert controller.can_continue() is True

    def test_cost_controller_register_usage(self):
        """Testa registo de uso."""
        from src.cost_controller import CostController

        controller = CostController(
            run_id="test_123",
            budget_limit_usd=10.0,
            token_limit=100000,
        )

        usage = controller.register_usage(
            phase="fase1_E1",
            model="openai/gpt-4o-mini",
            prompt_tokens=1000,
            completion_tokens=500,
            raise_on_exceed=False,
        )

        assert usage.total_tokens == 1500
        assert controller.usage.total_tokens == 1500
        assert controller.usage.total_cost_usd > 0

    def test_cost_controller_budget_exceeded(self):
        """Testa bloqueio por budget excedido."""
        from src.cost_controller import CostController, BudgetExceededError

        controller = CostController(
            run_id="test_123",
            budget_limit_usd=0.0001,  # Budget muito baixo
            token_limit=1000000,
        )

        with pytest.raises(BudgetExceededError):
            controller.register_usage(
                phase="fase1_E1",
                model="openai/gpt-4o",  # Modelo caro
                prompt_tokens=100000,
                completion_tokens=50000,
                raise_on_exceed=True,
            )

    def test_cost_controller_token_limit_exceeded(self):
        """Testa bloqueio por tokens excedidos."""
        from src.cost_controller import CostController, TokenLimitExceededError

        controller = CostController(
            run_id="test_123",
            budget_limit_usd=100.0,
            token_limit=1000,  # Limite muito baixo
        )

        with pytest.raises(TokenLimitExceededError):
            controller.register_usage(
                phase="fase1_E1",
                model="openai/gpt-4o-mini",
                prompt_tokens=800,
                completion_tokens=500,
                raise_on_exceed=True,
            )

    def test_cost_controller_summary(self):
        """Testa resumo de custos."""
        from src.cost_controller import CostController

        controller = CostController(
            run_id="test_123",
            budget_limit_usd=5.0,
            token_limit=100000,
        )

        controller.register_usage(
            phase="fase1_E1",
            model="openai/gpt-4o-mini",
            prompt_tokens=1000,
            completion_tokens=500,
            raise_on_exceed=False,
        )

        summary = controller.get_summary()

        assert "run_id" in summary
        assert "total_tokens" in summary
        assert "total_cost_usd" in summary
        assert "budget_pct" in summary
        assert "tokens_pct" in summary


class TestParsePerguntas:
    """Testes para parsing de perguntas."""

    def test_parse_perguntas_simples(self):
        """Testa parsing de perguntas simples."""
        from src.utils.perguntas import parse_perguntas

        texto = """Qual é o prazo de recurso?
---
Que legislação se aplica?"""

        perguntas = parse_perguntas(texto)

        assert len(perguntas) == 2
        assert "prazo de recurso" in perguntas[0].lower()
        assert "legislação" in perguntas[1].lower()

    def test_parse_perguntas_vazio(self):
        """Testa parsing de texto vazio."""
        from src.utils.perguntas import parse_perguntas

        perguntas = parse_perguntas("")
        assert len(perguntas) == 0

        perguntas = parse_perguntas("   ")
        assert len(perguntas) == 0

    def test_validar_perguntas_ok(self):
        """Testa validação de perguntas válidas."""
        from src.utils.perguntas import validar_perguntas

        perguntas = ["Pergunta 1?", "Pergunta 2?"]
        pode_continuar, msg = validar_perguntas(perguntas)

        assert pode_continuar is True

    def test_validar_perguntas_excesso(self):
        """Testa validação com excesso de perguntas."""
        from src.utils.perguntas import validar_perguntas
        from src.config import MAX_PERGUNTAS_HARD

        perguntas = [f"Pergunta {i}?" for i in range(MAX_PERGUNTAS_HARD + 1)]
        pode_continuar, msg = validar_perguntas(perguntas)

        assert pode_continuar is False
        assert "limite" in msg.lower() or "máximo" in msg.lower()

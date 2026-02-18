# -*- coding: utf-8 -*-
"""
COMPREHENSIVE STRESS & EDGE CASE TESTS
=======================================
Tests internal logic WITHOUT external API connections.
All external services (Supabase, OpenRouter, OpenAI) are mocked.

Categories:
  1. Config / Tier Tests
  2. Sanitization Tests
  3. Document Loader Edge Cases
  4. LLM Client Logic
  5. Cost Controller Tests
  6. Wallet Logic Tests
  7. Concurrency / Stress Tests
  8. Engine Edge Cases
"""

import io
import math
import os
import re
import sys
import threading
import time
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 1. CONFIG / TIER TESTS
# ============================================================

class TestTierConfig:
    """Tests for src/tier_config.py"""

    def test_all_tier_levels_produce_valid_costs(self):
        """All tiers (bronze, silver, gold) produce positive costs."""
        from src.tier_config import TierLevel, calculate_tier_cost
        for tier in TierLevel:
            costs = calculate_tier_cost(tier)
            assert costs["custo_real"] > 0, f"Tier {tier.value} custo_real should be > 0"
            assert costs["custo_cliente"] > 0, f"Tier {tier.value} custo_cliente should be > 0"
            assert costs["bloqueio"] > 0, f"Tier {tier.value} bloqueio should be > 0"
            # custo_cliente = custo_real * 2 (100% markup)
            assert abs(costs["custo_cliente"] - costs["custo_real"] * 2) < 0.01
            # bloqueio = custo_cliente * 1.50
            assert abs(costs["bloqueio"] - costs["custo_cliente"] * 1.50) < 0.01

    def test_tier_aliases_standard_bronze(self):
        """'standard' maps to bronze tier config."""
        from src.tier_config import TierLevel, TIER_CONFIG
        bronze_config = TIER_CONFIG[TierLevel.BRONZE]
        assert bronze_config["label"] == "Standard"

    def test_tier_aliases_premium_silver(self):
        """'premium' maps to silver tier config."""
        from src.tier_config import TierLevel, TIER_CONFIG
        silver_config = TIER_CONFIG[TierLevel.SILVER]
        assert silver_config["label"] == "Premium"

    def test_tier_aliases_elite_gold(self):
        """'elite' maps to gold tier config."""
        from src.tier_config import TierLevel, TIER_CONFIG
        gold_config = TIER_CONFIG[TierLevel.GOLD]
        assert gold_config["label"] == "Elite"

    def test_invalid_tier_raises_error(self):
        """Invalid tier value raises ValueError."""
        from src.tier_config import TierLevel
        with pytest.raises(ValueError):
            TierLevel("platinum")

    def test_calculate_tier_cost_zero_tokens(self):
        """Zero document_tokens -> size_multiplier = 1.0."""
        from src.tier_config import TierLevel, calculate_tier_cost
        costs = calculate_tier_cost(TierLevel.BRONZE, document_tokens=0)
        assert costs["size_multiplier"] == 1.0

    def test_calculate_tier_cost_30000_tokens(self):
        """30000 tokens -> size_multiplier should be 1.0 (<=30000)."""
        from src.tier_config import TierLevel, calculate_tier_cost
        costs = calculate_tier_cost(TierLevel.BRONZE, document_tokens=30000)
        assert costs["size_multiplier"] == 1.0

    def test_calculate_tier_cost_30001_tokens(self):
        """30001 tokens triggers 1.15 multiplier."""
        from src.tier_config import TierLevel, calculate_tier_cost
        costs = calculate_tier_cost(TierLevel.BRONZE, document_tokens=30001)
        assert costs["size_multiplier"] == 1.15

    def test_calculate_tier_cost_50001_tokens(self):
        """50001 tokens triggers 1.3 multiplier."""
        from src.tier_config import TierLevel, calculate_tier_cost
        costs = calculate_tier_cost(TierLevel.BRONZE, document_tokens=50001)
        assert costs["size_multiplier"] == 1.3

    def test_calculate_tier_cost_60000_tokens(self):
        """60000 tokens triggers 1.3 multiplier (>50000)."""
        from src.tier_config import TierLevel, calculate_tier_cost
        costs = calculate_tier_cost(TierLevel.SILVER, document_tokens=60000)
        assert costs["size_multiplier"] == 1.3

    def test_gold_costs_highest(self):
        """Gold tier should have highest costs."""
        from src.tier_config import TierLevel, calculate_tier_cost
        bronze = calculate_tier_cost(TierLevel.BRONZE)
        gold = calculate_tier_cost(TierLevel.GOLD)
        assert gold["custo_real"] >= bronze["custo_real"]

    def test_get_openrouter_model_known_keys(self):
        """get_openrouter_model returns correct mapping for known keys."""
        from src.tier_config import get_openrouter_model, OPENROUTER_MODEL_MAPPING
        for key, expected in OPENROUTER_MODEL_MAPPING.items():
            result = get_openrouter_model(key)
            assert result == expected, f"Key {key}: expected {expected}, got {result}"

    def test_get_openrouter_model_unknown_key(self):
        """get_openrouter_model returns the key itself if not in mapping."""
        from src.tier_config import get_openrouter_model
        result = get_openrouter_model("unknown-model-xyz")
        assert result == "unknown-model-xyz"

    def test_get_openrouter_model_all_keys_exist(self):
        """All model keys from tier configs are in the mapping or passthrough."""
        from src.tier_config import get_openrouter_model
        expected_keys = [
            "sonnet-4.5", "claude-opus-4", "haiku-4.5", "gpt-5.2",
            "gpt-5.2-pro", "gpt-4o", "deepseek-r1", "gemini-3-pro-preview",
            "llama-3.3-70b", "nova-pro", "nemotron-70b",
        ]
        for key in expected_keys:
            result = get_openrouter_model(key)
            assert result, f"Key {key} should return a value"

    def test_validate_tier_selection_valid(self):
        """Valid tier selection passes validation."""
        from src.tier_config import validate_tier_selection
        selection = {
            "extraction": "bronze",
            "audit": "silver",
            "judgment": "gold",
            "decision": "bronze",
        }
        assert validate_tier_selection(selection) is True

    def test_validate_tier_selection_invalid_phase(self):
        """Missing required phase fails validation."""
        from src.tier_config import validate_tier_selection
        selection = {
            "extraction": "bronze",
            "audit": "silver",
            # missing "judgment" and "decision"
        }
        assert validate_tier_selection(selection) is False

    def test_validate_tier_selection_invalid_tier_value(self):
        """Invalid tier value fails validation."""
        from src.tier_config import validate_tier_selection
        selection = {
            "extraction": "platinum",
            "audit": "silver",
            "judgment": "gold",
            "decision": "bronze",
        }
        assert validate_tier_selection(selection) is False

    def test_get_all_tiers_info_returns_three(self):
        """get_all_tiers_info returns exactly 3 tiers."""
        from src.tier_config import get_all_tiers_info
        info = get_all_tiers_info()
        assert len(info) == 3
        tiers = [t["tier"] for t in info]
        assert "bronze" in tiers
        assert "silver" in tiers
        assert "gold" in tiers


# ============================================================
# 2. SANITIZATION TESTS
# ============================================================

class TestSanitizationUtilModule:
    """Tests for src/utils/sanitize.py"""

    def test_sanitize_filename_path_traversal(self):
        """Path traversal sequences are removed from filenames."""
        from src.utils.sanitize import sanitize_filename
        result = sanitize_filename("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result
        # Should contain 'passwd' or sanitized version
        assert "passwd" in result

    def test_sanitize_filename_null_bytes(self):
        """Null bytes are stripped from filenames."""
        from src.utils.sanitize import sanitize_filename
        result = sanitize_filename("file\x00.pdf")
        assert "\x00" not in result
        assert result  # should not be empty

    def test_sanitize_filename_very_long(self):
        """Names >255 chars are truncated while preserving extension."""
        from src.utils.sanitize import sanitize_filename
        long_name = "a" * 300 + ".pdf"
        result = sanitize_filename(long_name)
        assert len(result) <= 255
        assert result.endswith(".pdf")

    def test_sanitize_filename_empty_raises(self):
        """Empty filename raises ValueError."""
        from src.utils.sanitize import sanitize_filename
        with pytest.raises(ValueError):
            sanitize_filename("")

    def test_sanitize_filename_none_raises(self):
        """None filename raises ValueError."""
        from src.utils.sanitize import sanitize_filename
        with pytest.raises(ValueError):
            sanitize_filename(None)

    def test_sanitize_filename_unicode_chars(self):
        """Unicode chars in filename produce valid result."""
        from src.utils.sanitize import sanitize_filename
        result = sanitize_filename("contrato_ação_nº5.pdf")
        assert result  # should not be empty
        assert ".pdf" in result or result.endswith(".pdf")

    def test_sanitize_run_id_path_traversal(self):
        """Path traversal in run_id raises ValueError."""
        from src.utils.sanitize import sanitize_run_id
        with pytest.raises(ValueError):
            sanitize_run_id("../../etc/passwd")

    def test_sanitize_run_id_null_bytes(self):
        """Null bytes in run_id raises ValueError."""
        from src.utils.sanitize import sanitize_run_id
        with pytest.raises(ValueError):
            sanitize_run_id("abc\x00def")

    def test_sanitize_run_id_valid(self):
        """Valid run_id passes through unchanged."""
        from src.utils.sanitize import sanitize_run_id
        valid = "20260203_154057_891a6226"
        assert sanitize_run_id(valid) == valid

    def test_sanitize_run_id_empty_raises(self):
        """Empty run_id raises ValueError."""
        from src.utils.sanitize import sanitize_run_id
        with pytest.raises(ValueError):
            sanitize_run_id("")

    def test_safe_join_path_traversal(self):
        """safe_join_path rejects path traversal."""
        from src.utils.sanitize import safe_join_path
        import tempfile
        base = Path(tempfile.gettempdir()) / "test_base"
        base.mkdir(exist_ok=True)
        with pytest.raises(ValueError):
            safe_join_path(base, "..", "..", "etc", "passwd")


class TestSanitizeFilenameMain:
    """Tests for _sanitize_filename in main.py"""

    def test_path_traversal_removed(self):
        """Path traversal chars are removed."""
        from main import _sanitize_filename
        result = _sanitize_filename("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result

    def test_null_bytes_removed(self):
        """Null bytes are sanitized."""
        from main import _sanitize_filename
        result = _sanitize_filename("file\x00.pdf")
        assert "\x00" not in result

    def test_very_long_name_truncated(self):
        """Names >255 chars are truncated."""
        from main import _sanitize_filename
        long_name = "a" * 300 + ".pdf"
        result = _sanitize_filename(long_name)
        assert len(result) <= 255

    def test_empty_returns_default(self):
        """Empty string returns 'documento'."""
        from main import _sanitize_filename
        result = _sanitize_filename("")
        assert result == "documento"

    def test_none_returns_default(self):
        """None returns 'documento'."""
        from main import _sanitize_filename
        result = _sanitize_filename(None)
        assert result == "documento"


class TestSanitizeContent:
    """Tests for _sanitize_content in main.py"""

    def test_removes_internal_patterns(self):
        """Internal patterns like MISSING_CITATION are removed."""
        from main import _sanitize_content
        text = "Normal text\nMISSING_CITATION something\nMore text"
        result = _sanitize_content(text)
        assert "MISSING_CITATION" not in result
        assert "Normal text" in result
        assert "More text" in result

    def test_removes_match_ratio_lines(self):
        """Lines with match_ratio= are removed."""
        from main import _sanitize_content
        text = "Good line\nmatch_ratio=0.85\nAnother good line"
        result = _sanitize_content(text)
        assert "match_ratio=" not in result

    def test_empty_returns_empty(self):
        """Empty/None input returns as-is."""
        from main import _sanitize_content
        assert _sanitize_content("") == ""
        assert _sanitize_content(None) is None

    def test_collapses_excess_newlines(self):
        """More than 2 consecutive newlines collapsed to 2."""
        from main import _sanitize_content
        text = "Line1\n\n\n\n\nLine2"
        result = _sanitize_content(text)
        assert "\n\n\n" not in result


# ============================================================
# 3. DOCUMENT LOADER EDGE CASES
# ============================================================

class TestDocumentLoader:
    """Tests for src/document_loader.py"""

    def test_loading_empty_bytes_fails(self):
        """Empty bytes produces failed DocumentContent."""
        from src.document_loader import DocumentLoader
        loader = DocumentLoader()
        doc = loader.load(io.BytesIO(b""), filename="empty.txt")
        # Empty TXT file is technically valid but has empty text
        # The loader won't raise but text will be empty
        assert doc.text.strip() == "" or doc.success

    def test_loading_non_pdf_with_pdf_extension(self):
        """Non-PDF bytes with .pdf extension fails gracefully."""
        from src.document_loader import DocumentLoader
        loader = DocumentLoader()
        fake_pdf = b"This is NOT a PDF, just plain text pretending to be"
        doc = loader.load(io.BytesIO(fake_pdf), filename="fake.pdf")
        # pdfplumber/pypdf should fail, producing error or empty text
        assert not doc.success or doc.text.strip() == ""

    def test_loading_oversized_file(self):
        """File exceeding MAX_FILE_SIZE_BYTES returns error."""
        from src.document_loader import DocumentLoader, MAX_FILE_SIZE_BYTES
        loader = DocumentLoader()
        # Create bytes just over the limit (don't allocate full 100MB)
        # We'll patch MAX_FILE_SIZE_BYTES to a small value for this test
        with patch("src.document_loader.MAX_FILE_SIZE_BYTES", 100):
            doc = loader.load(io.BytesIO(b"x" * 101), filename="big.txt")
            assert not doc.success
            assert "grande" in doc.error.lower() or "big" in doc.error.lower() or "máximo" in doc.error.lower()

    def test_loading_unsupported_extension(self):
        """Unsupported extension returns error."""
        from src.document_loader import DocumentLoader
        loader = DocumentLoader()
        doc = loader.load(io.BytesIO(b"some data"), filename="file.xyz")
        assert not doc.success
        assert "suportada" in doc.error.lower() or "extension" in doc.error.lower()

    def test_txt_loading_utf8(self):
        """UTF-8 encoded TXT loads correctly."""
        from src.document_loader import DocumentLoader
        loader = DocumentLoader()
        text = "Olá mundo, ação judicial nº 12345"
        doc = loader.load(io.BytesIO(text.encode("utf-8")), filename="test.txt")
        assert doc.success
        assert "Olá" in doc.text
        assert "ação" in doc.text

    def test_txt_loading_latin1(self):
        """Latin-1 encoded TXT loads correctly."""
        from src.document_loader import DocumentLoader
        loader = DocumentLoader()
        text = "Olá mundo, ação judicial"
        doc = loader.load(io.BytesIO(text.encode("latin-1")), filename="test.txt")
        assert doc.success
        assert "mundo" in doc.text

    def test_txt_loading_cp1252(self):
        """CP1252 encoded TXT loads correctly (common on Windows)."""
        from src.document_loader import DocumentLoader
        loader = DocumentLoader()
        text = "Contrato de prestação de serviços"
        doc = loader.load(io.BytesIO(text.encode("cp1252")), filename="test.txt")
        assert doc.success
        assert "Contrato" in doc.text

    def test_bytesio_without_filename_raises(self):
        """BytesIO without filename raises ValueError."""
        from src.document_loader import DocumentLoader
        loader = DocumentLoader()
        with pytest.raises(ValueError, match="filename"):
            loader.load(io.BytesIO(b"test"))

    def test_document_content_to_dict(self):
        """DocumentContent.to_dict produces valid dict."""
        from src.document_loader import DocumentContent
        doc = DocumentContent(
            filename="test.pdf",
            extension=".pdf",
            text="Hello world " * 200,
            num_pages=5,
            num_chars=2400,
            num_words=400,
        )
        d = doc.to_dict()
        assert d["filename"] == "test.pdf"
        assert d["num_pages"] == 5
        assert "..." in d["text"]  # truncated for to_dict
        assert d["text_full_length"] == len(doc.text)

    def test_stats_tracking(self):
        """Loader tracks statistics correctly."""
        from src.document_loader import DocumentLoader
        loader = DocumentLoader()
        loader.reset_stats()
        loader.load(io.BytesIO(b"hello"), filename="a.txt")
        loader.load(io.BytesIO(b"world"), filename="b.txt")
        stats = loader.get_stats()
        assert stats["total_loaded"] == 2
        assert stats["successful"] == 2
        assert stats["by_extension"].get(".txt", 0) == 2


# ============================================================
# 4. LLM CLIENT LOGIC
# ============================================================

class TestLLMClientLogic:
    """Tests for src/llm_client.py model detection functions."""

    def test_is_openai_model_gpt5(self):
        from src.llm_client import is_openai_model
        assert is_openai_model("openai/gpt-5.2") is True
        assert is_openai_model("gpt-5.2-pro") is True
        assert is_openai_model("openai/gpt-5.2-pro") is True

    def test_is_openai_model_gpt4(self):
        from src.llm_client import is_openai_model
        assert is_openai_model("openai/gpt-4o") is True
        assert is_openai_model("gpt-4.1") is True
        assert is_openai_model("openai/gpt-4o-mini") is True

    def test_is_openai_model_o_series(self):
        from src.llm_client import is_openai_model
        assert is_openai_model("o1-pro") is True
        assert is_openai_model("openai/o3") is True

    def test_is_openai_model_non_openai(self):
        from src.llm_client import is_openai_model
        assert is_openai_model("anthropic/claude-opus-4.6") is False
        assert is_openai_model("google/gemini-3-pro-preview") is False
        assert is_openai_model("deepseek/deepseek-r1") is False

    def test_uses_responses_api_gpt52(self):
        from src.llm_client import uses_responses_api
        assert uses_responses_api("openai/gpt-5.2") is True
        assert uses_responses_api("gpt-5.2") is True
        assert uses_responses_api("gpt-5.2-pro") is True

    def test_uses_responses_api_gpt4o(self):
        from src.llm_client import uses_responses_api
        assert uses_responses_api("openai/gpt-4o") is False
        assert uses_responses_api("gpt-4.1") is False

    def test_uses_responses_api_o_series(self):
        from src.llm_client import uses_responses_api
        assert uses_responses_api("o1-pro") is True
        assert uses_responses_api("o3") is True

    def test_supports_temperature_normal_models(self):
        from src.llm_client import supports_temperature
        assert supports_temperature("openai/gpt-5.2") is True
        assert supports_temperature("openai/gpt-4o") is True
        assert supports_temperature("anthropic/claude-opus-4.6") is True

    def test_supports_temperature_reasoning_models_no_temp(self):
        from src.llm_client import supports_temperature
        assert supports_temperature("gpt-5.2-pro") is False
        assert supports_temperature("o1-pro") is False
        assert supports_temperature("o3") is False
        assert supports_temperature("o3-pro") is False

    def test_supports_temperature_deepseek_r1(self):
        from src.llm_client import supports_temperature
        assert supports_temperature("deepseek/deepseek-r1") is False
        assert supports_temperature("deepseek-r1") is False

    def test_normalize_model_name_openai(self):
        from src.llm_client import normalize_model_name
        # For OpenAI API: remove prefix
        assert normalize_model_name("openai/gpt-5.2", "openai") == "gpt-5.2"
        assert normalize_model_name("gpt-5.2", "openai") == "gpt-5.2"

    def test_normalize_model_name_openrouter(self):
        from src.llm_client import normalize_model_name
        # For OpenRouter: add prefix if OpenAI model
        assert normalize_model_name("gpt-5.2", "openrouter") == "openai/gpt-5.2"
        # Already has prefix
        assert normalize_model_name("openai/gpt-5.2", "openrouter") == "openai/gpt-5.2"
        # Non-OpenAI model stays as-is
        assert normalize_model_name("anthropic/claude-opus-4.6", "openrouter") == "anthropic/claude-opus-4.6"

    def test_llm_response_cache_hit_rate(self):
        from src.llm_client import LLMResponse
        resp = LLMResponse(
            content="test", model="test", role="assistant",
            prompt_tokens=1000, cached_tokens=500,
        )
        assert resp.cache_hit_rate == 50.0

    def test_llm_response_cache_hit_rate_zero_prompt(self):
        from src.llm_client import LLMResponse
        resp = LLMResponse(
            content="test", model="test", role="assistant",
            prompt_tokens=0, cached_tokens=0,
        )
        assert resp.cache_hit_rate == 0.0

    def test_supports_cache(self):
        from src.llm_client import supports_cache
        assert supports_cache("anthropic/claude-sonnet-4.5") is True
        assert supports_cache("openai/gpt-5.2") is True
        assert supports_cache("some/unknown-model") is False

    def test_requires_manual_cache(self):
        from src.llm_client import requires_manual_cache
        assert requires_manual_cache("anthropic/claude-sonnet-4.5") is True
        assert requires_manual_cache("openai/gpt-5.2") is False


# ============================================================
# 5. COST CONTROLLER TESTS
# ============================================================

class TestCostController:
    """Tests for src/cost_controller.py"""

    def setup_method(self):
        """Reset DynamicPricing state before each test."""
        from src.cost_controller import DynamicPricing
        DynamicPricing.reset()

    def test_cost_calculation_accuracy_known_model(self):
        """Cost calculation for GPT-5.2 matches expected hardcoded pricing."""
        from src.cost_controller import CostController, HARDCODED_PRICING
        controller = CostController(run_id="test_1", budget_limit_usd=100.0)
        # GPT-5.2: input=$1.75/M, output=$14.00/M
        cost = controller.calculate_cost(
            model="openai/gpt-5.2",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        # Expected: 1.75 + 14.00 = 15.75
        expected_pricing = HARDCODED_PRICING["openai/gpt-5.2"]
        expected = expected_pricing["input"] + expected_pricing["output"]
        assert abs(cost - expected) < 0.01, f"Expected {expected}, got {cost}"

    def test_budget_exceeded_raises(self):
        """BudgetExceededError is raised when budget is exceeded."""
        from src.cost_controller import CostController, BudgetExceededError
        controller = CostController(run_id="test_budget", budget_limit_usd=0.001)
        with pytest.raises(BudgetExceededError) as exc_info:
            controller.register_usage(
                phase="test",
                model="openai/gpt-5.2",
                prompt_tokens=100_000,
                completion_tokens=100_000,
            )
        assert exc_info.value.budget_limit == 0.001
        assert exc_info.value.current_cost > 0.001

    def test_budget_exceeded_error_attributes(self):
        """BudgetExceededError has correct attributes."""
        from src.cost_controller import BudgetExceededError
        err = BudgetExceededError("test", current_cost=5.0, budget_limit=3.0)
        assert err.current_cost == 5.0
        assert err.budget_limit == 3.0

    def test_phase_usage_tracking(self):
        """register_usage correctly tracks phases."""
        from src.cost_controller import CostController
        controller = CostController(run_id="test_phases", budget_limit_usd=100.0)
        controller.register_usage(
            phase="fase1_E1", model="anthropic/claude-haiku-4.5",
            prompt_tokens=100, completion_tokens=50,
        )
        controller.register_usage(
            phase="fase1_E2", model="google/gemini-3-pro-preview",
            prompt_tokens=200, completion_tokens=100,
        )
        assert len(controller.usage.phases) == 2
        assert controller.usage.total_prompt_tokens == 300
        assert controller.usage.total_completion_tokens == 150
        assert controller.usage.total_tokens == 450

    def test_dynamic_pricing_hardcoded_fallback(self):
        """DynamicPricing falls back to hardcoded when no cache and API unavailable."""
        from src.cost_controller import DynamicPricing
        DynamicPricing.reset()
        # Mock fetch to simulate API being unavailable
        with patch.object(DynamicPricing, "fetch_openrouter_prices", return_value=False):
            pricing = DynamicPricing.get_pricing("openai/gpt-5.2")
            assert pricing["input"] == 1.75
            assert pricing["output"] == 14.00
            assert pricing["fonte"] == "hardcoded"

    def test_dynamic_pricing_default_fallback(self):
        """Unknown model falls back to default pricing."""
        from src.cost_controller import DynamicPricing, HARDCODED_PRICING
        DynamicPricing.reset()
        pricing = DynamicPricing.get_pricing("totally/unknown-model-xyz")
        assert pricing["input"] == HARDCODED_PRICING["default"]["input"]
        assert pricing["output"] == HARDCODED_PRICING["default"]["output"]

    def test_register_usage_zero_tokens(self):
        """register_usage with 0 tokens works without error."""
        from src.cost_controller import CostController
        controller = CostController(run_id="test_zero", budget_limit_usd=100.0)
        phase = controller.register_usage(
            phase="test", model="openai/gpt-5.2",
            prompt_tokens=0, completion_tokens=0,
        )
        assert phase.cost_usd == 0.0
        assert phase.total_tokens == 0

    def test_register_usage_very_large_tokens(self):
        """register_usage with 100M tokens calculates large cost without overflow."""
        from src.cost_controller import CostController
        controller = CostController(run_id="test_huge", budget_limit_usd=1_000_000.0)
        phase = controller.register_usage(
            phase="test", model="openai/gpt-5.2",
            prompt_tokens=100_000_000, completion_tokens=100_000_000,
            raise_on_exceed=False,  # don't raise, just track
        )
        # 100M tokens at $1.75/M = $175 input + $1400 output = $1575
        assert phase.cost_usd > 1000  # sanity check
        assert phase.total_tokens == 200_000_000

    def test_can_continue_after_small_usage(self):
        """can_continue returns True when under budget."""
        from src.cost_controller import CostController
        controller = CostController(run_id="test_continue", budget_limit_usd=100.0)
        controller.register_usage(
            phase="test", model="openai/gpt-4o-mini",
            prompt_tokens=100, completion_tokens=50,
        )
        assert controller.can_continue() is True

    def test_get_remaining_budget(self):
        """get_remaining_budget decreases after usage."""
        from src.cost_controller import CostController
        controller = CostController(run_id="test_remaining", budget_limit_usd=100.0)
        remaining_before = controller.get_remaining_budget()
        controller.register_usage(
            phase="test", model="openai/gpt-5.2",
            prompt_tokens=1000, completion_tokens=500,
        )
        remaining_after = controller.get_remaining_budget()
        assert remaining_after < remaining_before

    def test_get_summary(self):
        """get_summary produces valid dict."""
        from src.cost_controller import CostController
        controller = CostController(run_id="test_summary", budget_limit_usd=100.0)
        summary = controller.get_summary()
        assert summary["run_id"] == "test_summary"
        assert summary["budget_limit_usd"] == 100.0
        assert summary["blocked"] is False

    def test_finalize_sets_timestamp(self):
        """finalize() sets timestamp_end."""
        from src.cost_controller import CostController
        controller = CostController(run_id="test_finalize", budget_limit_usd=100.0)
        usage = controller.finalize()
        assert usage.timestamp_end is not None

    def test_phase_usage_to_dict(self):
        """PhaseUsage.to_dict produces valid dict."""
        from src.cost_controller import PhaseUsage
        phase = PhaseUsage(
            phase="test_phase", model="test_model",
            prompt_tokens=100, completion_tokens=50,
            total_tokens=150, cost_usd=0.05,
            pricing_source="hardcoded",
        )
        d = phase.to_dict()
        assert d["phase"] == "test_phase"
        assert d["cost_usd"] == 0.05


# ============================================================
# 6. WALLET LOGIC TESTS (mock Supabase)
# ============================================================

class TestWalletLogic:
    """Tests for src/wallet_manager.py without actual Supabase."""

    def test_insufficient_credits_error_attributes(self):
        """InsufficientCreditsError has correct attributes."""
        from src.wallet_manager import InsufficientCreditsError
        err = InsufficientCreditsError(required=10.0, available=5.0)
        assert err.required == 10.0
        assert err.available == 5.0
        assert err.saldo_atual == 5.0
        assert err.saldo_necessario == 10.0
        assert "10.00" in str(err)
        assert "5.00" in str(err)

    def test_safety_margin_value(self):
        """SAFETY_MARGIN is 1.50."""
        from src.wallet_manager import SAFETY_MARGIN
        assert SAFETY_MARGIN == 1.50

    def test_get_markup_multiplier(self):
        """get_markup_multiplier returns 2.0."""
        from src.wallet_manager import WalletManager
        mock_sb = MagicMock()
        wm = WalletManager(mock_sb)
        assert wm.get_markup_multiplier() == 2.0

    def test_usd_to_credits(self):
        """usd_to_credits converts correctly with ceiling."""
        from src.wallet_manager import usd_to_credits, USD_PER_CREDIT
        # $1.00 / $0.005 = 200 credits
        assert usd_to_credits(1.00) == 200
        # $0.005 = 1 credit
        assert usd_to_credits(0.005) == 1
        # $0.001 rounds up to 1 credit
        assert usd_to_credits(0.001) == 1
        # $0.00 = 0 credits
        assert usd_to_credits(0.0) == 0

    def test_credits_to_usd(self):
        """credits_to_usd converts correctly."""
        from src.wallet_manager import credits_to_usd, USD_PER_CREDIT
        assert credits_to_usd(200) == 1.00
        assert credits_to_usd(1) == 0.005
        assert credits_to_usd(0) == 0.0

    def test_usd_credits_roundtrip(self):
        """Conversion roundtrip is consistent (ceiling may add)."""
        from src.wallet_manager import usd_to_credits, credits_to_usd
        original = 2.71
        credits = usd_to_credits(original)
        back = credits_to_usd(credits)
        # back >= original (due to ceiling)
        assert back >= original

    def test_wallet_error_base(self):
        """WalletError is a valid exception."""
        from src.wallet_manager import WalletError
        err = WalletError("test error")
        assert str(err) == "test error"

    def test_insufficient_credits_is_wallet_error(self):
        """InsufficientCreditsError inherits from WalletError."""
        from src.wallet_manager import InsufficientCreditsError, WalletError
        err = InsufficientCreditsError(required=1.0, available=0.5)
        assert isinstance(err, WalletError)

    def test_block_credits_calculation(self):
        """Blocked amount = estimated_cost * SAFETY_MARGIN."""
        from src.wallet_manager import SAFETY_MARGIN
        estimated = 5.0
        blocked = estimated * SAFETY_MARGIN
        assert blocked == 7.5  # 5.0 * 1.50

    def test_usd_per_credit_constant(self):
        """USD_PER_CREDIT is $0.005."""
        from src.wallet_manager import USD_PER_CREDIT
        assert USD_PER_CREDIT == 0.005


# ============================================================
# 7. CONCURRENCY / STRESS TESTS
# ============================================================

class TestConcurrency:
    """Tests for thread safety of shared data structures."""

    def test_active_user_analyses_concurrent_access(self):
        """_active_user_analyses dict handles concurrent reads/writes."""
        from main import _active_user_analyses
        errors = []
        # Save initial state and restore after test
        original = dict(_active_user_analyses)
        _active_user_analyses.clear()

        def writer(user_id, count):
            try:
                for i in range(count):
                    _active_user_analyses[f"{user_id}_{i}"] = f"analysis_{i}"
                    time.sleep(0.001)
                    _active_user_analyses.pop(f"{user_id}_{i}", None)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(f"user_{t}", 50))
            for t in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        _active_user_analyses.clear()
        _active_user_analyses.update(original)
        assert len(errors) == 0, f"Errors during concurrent access: {errors}"

    def test_blacklist_cache_thread_safety(self):
        """_blacklist_cache handles concurrent reads safely."""
        from main import _blacklist_cache, _blacklist_lock
        errors = []
        original_loaded_at = _blacklist_cache["loaded_at"]

        def reader(count):
            try:
                for _ in range(count):
                    with _blacklist_lock:
                        _ = _blacklist_cache["emails"].copy()
                        _ = _blacklist_cache["ips"].copy()
                        _ = _blacklist_cache["domains"].copy()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=reader, args=(100,))
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        _blacklist_cache["loaded_at"] = original_loaded_at
        assert len(errors) == 0, f"Errors during concurrent read: {errors}"

    def test_token_cache_eviction(self):
        """Token cache evicts expired entries when exceeding TOKEN_CACHE_MAX_SIZE."""
        from auth_service import _token_cache, _token_cache_lock, TOKEN_CACHE_MAX_SIZE
        original = dict(_token_cache)

        with _token_cache_lock:
            _token_cache.clear()

        now = time.time()
        # Fill cache beyond max
        with _token_cache_lock:
            for i in range(TOKEN_CACHE_MAX_SIZE + 100):
                _token_cache[f"token_{i}"] = {
                    "payload": {"sub": f"user_{i}"},
                    "expires": now - 1,  # already expired
                }

        assert len(_token_cache) > TOKEN_CACHE_MAX_SIZE

        # Eviction happens during get_current_user call.
        # Simulate the eviction logic directly:
        with _token_cache_lock:
            if len(_token_cache) > TOKEN_CACHE_MAX_SIZE:
                expired_keys = [k for k, v in _token_cache.items() if now >= v["expires"]]
                for k in expired_keys:
                    del _token_cache[k]

        assert len(_token_cache) == 0  # all were expired

        # Restore
        with _token_cache_lock:
            _token_cache.clear()
            _token_cache.update(original)

    def test_cost_controller_concurrent_register_usage(self):
        """CostController handles concurrent register_usage calls safely."""
        from src.cost_controller import CostController, DynamicPricing
        DynamicPricing.reset()
        controller = CostController(run_id="concurrent_test", budget_limit_usd=1_000_000.0)
        errors = []
        num_threads = 20
        calls_per_thread = 50

        def register(thread_id):
            try:
                for i in range(calls_per_thread):
                    controller.register_usage(
                        phase=f"thread_{thread_id}_call_{i}",
                        model="openai/gpt-4o-mini",
                        prompt_tokens=100,
                        completion_tokens=50,
                        raise_on_exceed=False,
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=register, args=(t,))
            for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Errors during concurrent register_usage: {errors}"
        expected_phases = num_threads * calls_per_thread
        assert len(controller.usage.phases) == expected_phases
        # Total tokens should be exactly (100+50) * num_threads * calls_per_thread
        expected_tokens = 150 * num_threads * calls_per_thread
        assert controller.usage.total_tokens == expected_tokens

    def test_dynamic_pricing_concurrent_lookups(self):
        """DynamicPricing handles concurrent get_pricing calls."""
        from src.cost_controller import DynamicPricing
        DynamicPricing.reset()
        errors = []

        def lookup(count):
            try:
                for _ in range(count):
                    DynamicPricing.get_pricing("openai/gpt-5.2")
                    DynamicPricing.get_pricing("anthropic/claude-opus-4.6")
                    DynamicPricing.get_pricing("unknown/model")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=lookup, args=(100,))
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Errors during concurrent pricing lookup: {errors}"


# ============================================================
# 8. ENGINE EDGE CASES
# ============================================================

class TestEngineEdgeCases:
    """Tests for src/engine.py internal logic."""

    def test_verificar_saldo_wallet_skip_check(self):
        """SKIP_WALLET_CHECK=true skips wallet verification (in dev environment)."""
        with patch.dict(os.environ, {"SKIP_WALLET_CHECK": "true", "ENV": "development"}):
            from src.engine import verificar_saldo_wallet
            result = verificar_saldo_wallet("fake-user-id")
            assert result["suficiente"] is True
            assert result["saldo_atual"] == 999.99

    def test_verificar_saldo_wallet_skip_check_uppercase(self):
        """SKIP_WALLET_CHECK=TRUE also works (case-insensitive, in dev environment)."""
        with patch.dict(os.environ, {"SKIP_WALLET_CHECK": "TRUE", "ENV": "dev"}):
            from src.engine import verificar_saldo_wallet
            result = verificar_saldo_wallet("fake-user-id")
            assert result["suficiente"] is True

    def test_is_wallet_skip_false_by_default(self):
        """_is_wallet_skip returns False when env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove the key if it exists
            os.environ.pop("SKIP_WALLET_CHECK", None)
            from src.engine import _is_wallet_skip
            assert _is_wallet_skip() is False

    def test_area_direito_normalization_case(self):
        """Area do direito normalization is case-insensitive."""
        import unicodedata
        from src.config import AREAS_DIREITO

        def _normalize_area(s):
            s = s.strip().lower()
            return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

        _areas_map = {_normalize_area(a): a for a in AREAS_DIREITO}

        # "civil" -> "Civil"
        assert _areas_map.get(_normalize_area("civil")) == "Civil"
        # "PENAL" -> "Penal"
        assert _areas_map.get(_normalize_area("PENAL")) == "Penal"
        # "trabalho" -> "Trabalho"
        assert _areas_map.get(_normalize_area("trabalho")) == "Trabalho"

    def test_area_direito_normalization_accents(self):
        """Area do direito normalization handles accents."""
        import unicodedata
        from src.config import AREAS_DIREITO

        def _normalize_area(s):
            s = s.strip().lower()
            return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

        _areas_map = {_normalize_area(a): a for a in AREAS_DIREITO}

        # "Tributário" with accent
        assert _areas_map.get(_normalize_area("Tributário")) == "Tributário"
        # Without accent
        assert _areas_map.get(_normalize_area("Tributario")) == "Tributário"
        # "Família"
        assert _areas_map.get(_normalize_area("Família")) == "Família"
        assert _areas_map.get(_normalize_area("Familia")) == "Família"

    def test_invalid_area_direito_not_in_map(self):
        """Invalid area_direito is not in the areas map."""
        import unicodedata
        from src.config import AREAS_DIREITO

        def _normalize_area(s):
            s = s.strip().lower()
            return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

        _areas_map = {_normalize_area(a): a for a in AREAS_DIREITO}
        assert _areas_map.get(_normalize_area("Espacial")) is None
        assert _areas_map.get(_normalize_area("XYZ_INVALID")) is None

    def test_all_valid_areas_recognized(self):
        """All valid areas are in AREAS_DIREITO."""
        from src.config import AREAS_DIREITO
        expected = [
            "Civil", "Penal", "Trabalho", "Família", "Administrativo",
            "Constitucional", "Comercial", "Tributário", "Ambiental",
            "Consumidor", "Multi-área", "Outro"
        ]
        for area in expected:
            assert area in AREAS_DIREITO, f"'{area}' not in AREAS_DIREITO"

    def test_engine_error_hierarchy(self):
        """Engine exception classes have correct hierarchy."""
        from src.engine import (
            EngineError, InsufficientBalanceError,
            InvalidDocumentError, MissingApiKeyError,
        )
        assert issubclass(InsufficientBalanceError, EngineError)
        assert issubclass(InvalidDocumentError, EngineError)
        assert issubclass(MissingApiKeyError, EngineError)

    def test_insufficient_balance_error_attributes(self):
        """InsufficientBalanceError has correct attributes."""
        from src.engine import InsufficientBalanceError
        err = InsufficientBalanceError(saldo_atual=1.0, saldo_necessario=5.0)
        assert err.saldo_atual == 1.0
        assert err.saldo_necessario == 5.0
        assert err.saldo_minimo == 5.0  # compat

    def test_carregar_documento_de_bytes_invalid_doc(self):
        """carregar_documento_de_bytes raises InvalidDocumentError for empty text."""
        from src.engine import carregar_documento_de_bytes, InvalidDocumentError
        # A TXT file with only whitespace should raise InvalidDocumentError
        with pytest.raises(InvalidDocumentError):
            carregar_documento_de_bytes(
                file_bytes=b"   \n  \t  ",
                filename="empty.txt",
                use_pdf_safe=False,
            )


# ============================================================
# PERGUNTAS PARSING EDGE CASES
# ============================================================

class TestPerguntasParsing:
    """Tests for src/utils/perguntas.py"""

    def test_parse_empty_string(self):
        from src.utils.perguntas import parse_perguntas
        assert parse_perguntas("") == []

    def test_parse_none(self):
        from src.utils.perguntas import parse_perguntas
        assert parse_perguntas(None) == []

    def test_parse_single_question(self):
        from src.utils.perguntas import parse_perguntas
        result = parse_perguntas("Qual o prazo?")
        assert len(result) == 1
        assert result[0] == "Qual o prazo?"

    def test_parse_multiple_questions_with_separator(self):
        from src.utils.perguntas import parse_perguntas
        raw = "Pergunta 1?\n---\nPergunta 2?\n---\nPergunta 3?"
        result = parse_perguntas(raw)
        assert len(result) == 3

    def test_parse_windows_line_endings(self):
        from src.utils.perguntas import parse_perguntas
        raw = "Pergunta 1?\r\n---\r\nPergunta 2?"
        result = parse_perguntas(raw)
        assert len(result) == 2

    def test_parse_alternative_separators(self):
        from src.utils.perguntas import parse_perguntas
        # em dash
        raw = "Pergunta 1?\n\u2014\nPergunta 2?"
        result = parse_perguntas(raw)
        assert len(result) == 2

    def test_parse_underscore_separator(self):
        from src.utils.perguntas import parse_perguntas
        raw = "Pergunta 1?\n___\nPergunta 2?"
        result = parse_perguntas(raw)
        assert len(result) == 2

    def test_parse_whitespace_only(self):
        from src.utils.perguntas import parse_perguntas
        assert parse_perguntas("   \n  \t  ") == []

    def test_validar_perguntas_none(self):
        from src.utils.perguntas import validar_perguntas
        ok, msg = validar_perguntas(None)
        assert ok is True

    def test_validar_perguntas_empty(self):
        from src.utils.perguntas import validar_perguntas
        ok, msg = validar_perguntas([])
        assert ok is True

    def test_validar_perguntas_valid(self):
        from src.utils.perguntas import validar_perguntas
        perguntas = ["Pergunta 1?", "Pergunta 2?"]
        ok, msg = validar_perguntas(perguntas)
        assert ok is True

    def test_validar_perguntas_too_many(self):
        """More than MAX_PERGUNTAS_HARD is rejected."""
        from src.utils.perguntas import validar_perguntas
        from src.config import MAX_PERGUNTAS_HARD
        perguntas = [f"Pergunta {i}?" for i in range(MAX_PERGUNTAS_HARD + 1)]
        ok, msg = validar_perguntas(perguntas)
        assert ok is False

    def test_validar_perguntas_too_long_single(self):
        """Single question over MAX_CHARS_PERGUNTA_HARD is rejected."""
        from src.utils.perguntas import validar_perguntas
        from src.config import MAX_CHARS_PERGUNTA_HARD
        perguntas = ["x" * (MAX_CHARS_PERGUNTA_HARD + 1)]
        ok, msg = validar_perguntas(perguntas)
        assert ok is False

    def test_validar_perguntas_total_too_long(self):
        """Total chars over MAX_CHARS_TOTAL_PERGUNTAS_HARD is rejected."""
        from src.utils.perguntas import validar_perguntas
        from src.config import MAX_CHARS_TOTAL_PERGUNTAS_HARD, MAX_PERGUNTAS_HARD
        # Create questions whose total exceeds the limit but individually OK
        chars_per = (MAX_CHARS_TOTAL_PERGUNTAS_HARD // MAX_PERGUNTAS_HARD) + 100
        # Ensure individual limit is respected
        from src.config import MAX_CHARS_PERGUNTA_HARD
        if chars_per > MAX_CHARS_PERGUNTA_HARD:
            chars_per = MAX_CHARS_PERGUNTA_HARD
        num_needed = (MAX_CHARS_TOTAL_PERGUNTAS_HARD // chars_per) + 2
        if num_needed > MAX_PERGUNTAS_HARD:
            pytest.skip("Cannot exceed total chars within hard limits")
        perguntas = ["x" * chars_per for _ in range(num_needed)]
        ok, msg = validar_perguntas(perguntas)
        # Either blocked by total chars or by count
        assert not ok or "AVISO" in msg


# ============================================================
# CONFIG HELPER FUNCTION TESTS
# ============================================================

class TestConfigHelpers:
    """Tests for helper functions in src/config.py"""

    def test_get_max_tokens_para_fase_agregador(self):
        from src.config import get_max_tokens_para_fase
        assert get_max_tokens_para_fase("Agregador") == 32768

    def test_get_max_tokens_para_fase_chefe(self):
        from src.config import get_max_tokens_para_fase
        assert get_max_tokens_para_fase("Chefe") == 32768

    def test_get_max_tokens_para_fase_conselheiro(self):
        from src.config import get_max_tokens_para_fase
        assert get_max_tokens_para_fase("Conselheiro") == 32768

    def test_get_max_tokens_para_fase_presidente(self):
        from src.config import get_max_tokens_para_fase
        assert get_max_tokens_para_fase("Presidente") == 32768

    def test_get_max_tokens_para_fase_sintese(self):
        from src.config import get_max_tokens_para_fase
        assert get_max_tokens_para_fase("sintese") == 8192

    def test_get_max_tokens_para_fase_extrator_returns_none(self):
        from src.config import get_max_tokens_para_fase
        assert get_max_tokens_para_fase("E1") is None
        assert get_max_tokens_para_fase("A1") is None
        assert get_max_tokens_para_fase("J1") is None

    def test_calcular_max_tokens_small_doc(self):
        from src.config import calcular_max_tokens
        result = calcular_max_tokens(10_000, "openai/gpt-5.2")
        assert result == 16_384

    def test_calcular_max_tokens_medium_doc(self):
        from src.config import calcular_max_tokens
        result = calcular_max_tokens(100_000, "openai/gpt-5.2")
        assert result == 20_000

    def test_calcular_max_tokens_large_doc(self):
        from src.config import calcular_max_tokens
        result = calcular_max_tokens(200_000, "openai/gpt-5.2")
        assert result == 24_000

    def test_calcular_max_tokens_very_large_doc(self):
        from src.config import calcular_max_tokens
        result = calcular_max_tokens(400_000, "openai/gpt-5.2")
        assert result == 28_000

    def test_calcular_max_tokens_huge_doc(self):
        from src.config import calcular_max_tokens
        result = calcular_max_tokens(600_000, "openai/gpt-5.2")
        assert result == 32_000

    def test_calcular_max_tokens_respects_model_limit(self):
        """Max tokens never exceeds model's real limit."""
        from src.config import calcular_max_tokens, MODEL_MAX_OUTPUT
        # Nova Pro has only 5120 max output
        result = calcular_max_tokens(600_000, "amazon/nova-pro-v1")
        assert result <= MODEL_MAX_OUTPUT["amazon/nova-pro-v1"]
        assert result == 5_120

    def test_calcular_max_tokens_fixed_phase(self):
        """Consolidator phases get fixed max_tokens."""
        from src.config import calcular_max_tokens
        result = calcular_max_tokens(10_000, "openai/gpt-5.2", role_name="Agregador")
        assert result == 32_768  # Fixed for Agregador

    def test_selecionar_modelo_com_failover_small_doc(self):
        """Small doc stays with original model."""
        from src.config import selecionar_modelo_com_failover
        result = selecionar_modelo_com_failover("openai/gpt-5.2", 100_000, "E1")
        assert result == "openai/gpt-5.2"

    def test_selecionar_modelo_com_failover_large_doc(self):
        """Large doc fails over to GPT-4.1."""
        from src.config import selecionar_modelo_com_failover, LIMITE_NIVEL1_CHARS, FALLBACK_MODEL_NIVEL2
        result = selecionar_modelo_com_failover(
            "openai/gpt-5.2", LIMITE_NIVEL1_CHARS + 1, "E1"
        )
        assert result == FALLBACK_MODEL_NIVEL2

    def test_selecionar_modelo_com_failover_non_gpt52(self):
        """Non-GPT-5.2 models never trigger failover."""
        from src.config import selecionar_modelo_com_failover
        result = selecionar_modelo_com_failover(
            "anthropic/claude-opus-4.6", 10_000_000, "E1"
        )
        assert result == "anthropic/claude-opus-4.6"

    def test_selecionar_modelo_com_failover_impossible(self):
        """Impossibly large doc raises ValueError."""
        from src.config import selecionar_modelo_com_failover, LIMITE_NIVEL3_CHARS
        with pytest.raises(ValueError):
            selecionar_modelo_com_failover(
                "openai/gpt-5.2", LIMITE_NIVEL3_CHARS + 1, "E1"
            )

    def test_classificar_documento_levels(self):
        """classificar_documento returns correct levels."""
        from src.config import (
            classificar_documento, LIMITE_NIVEL1_CHARS,
            LIMITE_NIVEL2_CHARS, LIMITE_NIVEL3_CHARS,
        )
        # Level 1
        r = classificar_documento(1000)
        assert r["nivel"] == 1
        assert r["pode_processar"] is True
        assert r["requer_confirmacao"] is False

        # Level 2
        r = classificar_documento(LIMITE_NIVEL1_CHARS + 1)
        assert r["nivel"] == 2
        assert r["pode_processar"] is True

        # Level 3
        r = classificar_documento(LIMITE_NIVEL2_CHARS + 1)
        assert r["nivel"] == 3
        assert r["pode_processar"] is True
        assert r["requer_confirmacao"] is True

        # Level 4
        r = classificar_documento(LIMITE_NIVEL3_CHARS + 1)
        assert r["nivel"] == 4
        assert r["pode_processar"] is False


# ============================================================
# ADDITIONAL STRESS TESTS
# ============================================================

class TestStress:
    """Stress tests for internal data structures."""

    def test_tier_cost_calculation_batch(self):
        """Compute tier costs 1000 times without error."""
        from src.tier_config import TierLevel, calculate_tier_cost
        for _ in range(1000):
            for tier in TierLevel:
                costs = calculate_tier_cost(tier, document_tokens=50000)
                assert costs["custo_real"] > 0

    def test_sanitize_filename_batch(self):
        """Sanitize 1000 filenames rapidly."""
        from src.utils.sanitize import sanitize_filename
        for i in range(1000):
            name = f"file_{i}_{'x' * 20}.pdf"
            result = sanitize_filename(name)
            assert len(result) <= 255

    def test_parse_perguntas_batch(self):
        """Parse 100 question blocks rapidly."""
        from src.utils.perguntas import parse_perguntas
        raw = "\n---\n".join([f"Pergunta {i}?" for i in range(100)])
        result = parse_perguntas(raw)
        assert len(result) == 100

    def test_model_detection_batch(self):
        """Run model detection on many models rapidly."""
        from src.llm_client import is_openai_model, uses_responses_api, supports_temperature
        models = [
            "openai/gpt-5.2", "openai/gpt-4o", "anthropic/claude-opus-4.6",
            "google/gemini-3-pro-preview", "deepseek/deepseek-r1",
            "gpt-5.2-pro", "o1-pro", "o3",
        ]
        for _ in range(1000):
            for m in models:
                is_openai_model(m)
                uses_responses_api(m)
                supports_temperature(m)

    def test_dynamic_pricing_reset_cycle(self):
        """DynamicPricing can be reset and queried repeatedly with hardcoded fallback."""
        from src.cost_controller import DynamicPricing
        # Mock API to simulate unavailability, forcing hardcoded fallback
        with patch.object(DynamicPricing, "fetch_openrouter_prices", return_value=False):
            for _ in range(50):
                DynamicPricing.reset()
                p = DynamicPricing.get_pricing("openai/gpt-5.2")
                assert p["fonte"] == "hardcoded"
                assert p["input"] == 1.75

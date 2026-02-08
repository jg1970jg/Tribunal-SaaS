# -*- coding: utf-8 -*-
"""
TRIBUNAL GOLDENMASTER - Testes do Document Loader
============================================================
Testes para carregamento de documentos (TXT, PDF, DOCX, XLSX).
============================================================
"""

import pytest
from pathlib import Path
import io


class TestDocumentLoader:
    """Testes para DocumentLoader."""

    def test_load_txt_from_fixture(self, sample_txt_path):
        """Testa carregamento de TXT da pasta fixtures."""
        from src.document_loader import DocumentLoader

        if not sample_txt_path.exists():
            pytest.skip("Fixture sample_input.txt não existe")

        loader = DocumentLoader()
        doc = loader.load(sample_txt_path)

        assert doc.success is True
        assert doc.extension == ".txt"
        assert doc.num_chars > 0
        assert doc.num_words > 0
        assert "CONTRATO" in doc.text or "arrendamento" in doc.text.lower()

    def test_load_txt_from_bytes(self):
        """Testa carregamento de TXT a partir de bytes."""
        from src.document_loader import DocumentLoader

        texto = "Este é um texto de teste.\nCom múltiplas linhas."
        file_bytes = io.BytesIO(texto.encode("utf-8"))

        loader = DocumentLoader()
        doc = loader.load(file_bytes, filename="teste.txt")

        assert doc.success is True
        assert doc.extension == ".txt"
        assert doc.text == texto
        assert doc.num_chars == len(texto)

    def test_load_unsupported_extension(self):
        """Testa que extensões não suportadas retornam erro."""
        from src.document_loader import DocumentLoader

        file_bytes = io.BytesIO(b"conteudo qualquer")

        loader = DocumentLoader()
        doc = loader.load(file_bytes, filename="teste.xyz")

        assert doc.success is False
        assert "não suportada" in doc.error.lower()

    def test_document_content_to_dict(self, sample_documento_content):
        """Testa serialização de DocumentContent."""
        data = sample_documento_content.to_dict()

        assert "filename" in data
        assert "extension" in data
        assert "text" in data
        assert "num_chars" in data
        assert "success" in data

    def test_loader_stats(self):
        """Testa estatísticas do loader."""
        from src.document_loader import DocumentLoader

        loader = DocumentLoader()

        # Carregar alguns documentos
        loader.load(io.BytesIO(b"teste 1"), filename="a.txt")
        loader.load(io.BytesIO(b"teste 2"), filename="b.txt")

        stats = loader.get_stats()

        assert stats["total_loaded"] == 2
        assert stats["successful"] == 2
        assert ".txt" in stats["by_extension"]

    def test_supported_extensions(self):
        """Testa que extensões suportadas estão definidas."""
        from src.document_loader import get_supported_extensions

        extensions = get_supported_extensions()

        assert ".pdf" in extensions
        assert ".docx" in extensions
        assert ".xlsx" in extensions
        assert ".txt" in extensions

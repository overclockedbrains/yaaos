"""Tests for new embedding providers (Voyage, Ollama)."""

from __future__ import annotations

import pytest


class TestVoyageProvider:
    def test_import_without_voyageai(self):
        """Should raise ImportError if voyageai not installed."""
        try:
            import voyageai  # noqa: F401

            pytest.skip("voyageai is installed, can't test import error")
        except ImportError:
            pass

        with pytest.raises(ImportError, match="voyageai"):
            from yaaos_sfs.providers.voyage_provider import VoyageEmbeddingProvider

            VoyageEmbeddingProvider(api_key="test")

    def test_dims_mapping(self):
        """Test dimension mapping for known models."""
        from yaaos_sfs.providers.voyage_provider import VOYAGE_DIMS

        assert VOYAGE_DIMS["voyage-code-3"] == 1024
        assert VOYAGE_DIMS["voyage-3"] == 1024
        assert VOYAGE_DIMS["voyage-3-lite"] == 512


class TestOllamaProvider:
    def test_dims_mapping(self):
        """Test dimension mapping for known Ollama models."""
        from yaaos_sfs.providers.ollama_provider import OLLAMA_DIMS

        assert OLLAMA_DIMS["nomic-embed-text"] == 768
        assert OLLAMA_DIMS["mxbai-embed-large"] == 1024
        assert OLLAMA_DIMS["all-minilm"] == 384

    def test_connection_error_without_server(self):
        """Should raise ConnectionError if Ollama isn't running."""
        from yaaos_sfs.providers.ollama_provider import OllamaEmbeddingProvider

        with pytest.raises(ConnectionError, match="Cannot connect"):
            OllamaEmbeddingProvider(
                model="nomic-embed-text",
                base_url="http://localhost:99999",  # Unlikely to be running
            )

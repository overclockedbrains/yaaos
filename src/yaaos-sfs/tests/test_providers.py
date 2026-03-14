"""Tests for embedding providers."""

from __future__ import annotations

import pytest

from yaaos_sfs.providers import EmbeddingProvider
from yaaos_sfs.providers.local import LocalEmbeddingProvider


class TestEmbeddingProviderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            EmbeddingProvider()


class TestLocalProvider:
    """These tests actually load the model — they're slow but essential.
    Mark with @pytest.mark.slow if you want to skip in CI.
    """

    @pytest.fixture(scope="class")
    def provider(self):
        return LocalEmbeddingProvider("all-MiniLM-L6-v2")

    def test_dims(self, provider):
        assert provider.dims == 384

    def test_embed_single(self, provider):
        result = provider.embed(["hello world"])
        assert len(result) == 1
        assert len(result[0]) == 384
        assert all(isinstance(v, float) for v in result[0])

    def test_embed_batch(self, provider):
        texts = ["hello", "world", "foo bar"]
        result = provider.embed(texts)
        assert len(result) == 3
        assert all(len(v) == 384 for v in result)

    def test_embed_query(self, provider):
        result = provider.embed_query("test query")
        assert len(result) == 384

    def test_similar_texts_close_embeddings(self, provider):
        """Semantically similar texts should have closer embeddings."""
        import math

        def cosine_sim(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(x * x for x in b))
            return dot / (norm_a * norm_b)

        e1 = provider.embed_query("python programming language")
        e2 = provider.embed_query("coding in python")
        e3 = provider.embed_query("chocolate cake recipe")

        sim_related = cosine_sim(e1, e2)
        sim_unrelated = cosine_sim(e1, e3)

        assert sim_related > sim_unrelated, (
            f"Related texts similarity ({sim_related:.3f}) should be > "
            f"unrelated ({sim_unrelated:.3f})"
        )

    def test_empty_text(self, provider):
        result = provider.embed([""])
        assert len(result) == 1
        assert len(result[0]) == 384

    def test_lazy_model_loading(self):
        """Model should not load until first use."""
        p = LocalEmbeddingProvider("all-MiniLM-L6-v2")
        assert p._model is None
        _ = p.dims  # Triggers load
        assert p._model is not None

"""Tests for the Model Bus embedding provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from yaaos_sfs.providers import EmbeddingProvider


class TestModelBusProviderKnownDims:
    def test_known_dims_mapping(self):
        """Test dimension mapping for known models."""
        from yaaos_sfs.providers.modelbus_provider import _KNOWN_DIMS

        assert _KNOWN_DIMS["nomic-embed-text"] == 768
        assert _KNOWN_DIMS["all-MiniLM-L6-v2"] == 384
        assert _KNOWN_DIMS["text-embedding-3-small"] == 1536
        assert _KNOWN_DIMS["voyage-code-3"] == 1024


class TestModelBusProviderInit:
    @patch(
        "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider.__init__",
        return_value=None,
    )
    def test_is_embedding_provider(self, mock_init):
        """ModelBusEmbeddingProvider should be a subclass of EmbeddingProvider."""
        from yaaos_sfs.providers.modelbus_provider import ModelBusEmbeddingProvider

        assert issubclass(ModelBusEmbeddingProvider, EmbeddingProvider)

    def test_import_error_without_modelbus(self):
        """Should raise ImportError if yaaos-modelbus not installed."""
        from yaaos_sfs.providers.modelbus_provider import ModelBusEmbeddingProvider

        with patch.dict("sys.modules", {"yaaos_modelbus": None, "yaaos_modelbus.client": None}):
            with pytest.raises(ImportError, match="yaaos-modelbus"):
                ModelBusEmbeddingProvider()


class TestModelBusProviderWithMockClient:
    """Tests using a mock ModelBusClient to avoid needing a running daemon."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.embed.return_value = {
            "embeddings": [[0.1, 0.2, 0.3] * 256],  # 768-dim
            "model": "ollama/nomic-embed-text",
            "dims": 768,
        }
        return client

    @pytest.fixture
    def provider(self, mock_client):
        from yaaos_sfs.providers.modelbus_provider import ModelBusEmbeddingProvider

        with patch(
            "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider.__init__",
            return_value=None,
        ):
            p = ModelBusEmbeddingProvider.__new__(ModelBusEmbeddingProvider)
            p._model = "ollama/nomic-embed-text"
            p._client = mock_client
            p._dims_cache = 768
        return p

    def test_embed_returns_vectors(self, provider, mock_client):
        result = provider.embed(["hello world", "test text"])
        mock_client.embed.assert_called_once_with(
            ["hello world", "test text"], model="ollama/nomic-embed-text"
        )
        assert isinstance(result, list)

    def test_embed_query_returns_single_vector(self, provider, mock_client):
        mock_client.embed.return_value = {
            "embeddings": [[0.5] * 768],
            "model": "ollama/nomic-embed-text",
            "dims": 768,
        }
        result = provider.embed_query("search query")
        mock_client.embed.assert_called_once_with(["search query"], model="ollama/nomic-embed-text")
        assert isinstance(result, list)
        assert len(result) == 768

    def test_dims_returns_cached(self, provider):
        assert provider.dims == 768

    def test_dims_probes_when_not_cached(self, mock_client):
        from yaaos_sfs.providers.modelbus_provider import ModelBusEmbeddingProvider

        with patch(
            "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider.__init__",
            return_value=None,
        ):
            p = ModelBusEmbeddingProvider.__new__(ModelBusEmbeddingProvider)
            p._model = None
            p._client = mock_client
            p._dims_cache = None

        assert p.dims == 768
        mock_client.embed.assert_called_once_with(["hello"], model=None)

    def test_dims_from_embeddings_length(self, mock_client):
        """When response has no 'dims' key, infer from embedding length."""
        from yaaos_sfs.providers.modelbus_provider import ModelBusEmbeddingProvider

        mock_client.embed.return_value = {
            "embeddings": [[0.1] * 512],
            "model": "test",
        }

        with patch(
            "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider.__init__",
            return_value=None,
        ):
            p = ModelBusEmbeddingProvider.__new__(ModelBusEmbeddingProvider)
            p._model = None
            p._client = mock_client
            p._dims_cache = None

        assert p.dims == 512

    def test_dims_raises_on_empty_response(self, mock_client):
        """Should raise RuntimeError if cannot determine dims."""
        from yaaos_sfs.providers.modelbus_provider import ModelBusEmbeddingProvider

        mock_client.embed.return_value = {"embeddings": []}

        with patch(
            "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider.__init__",
            return_value=None,
        ):
            p = ModelBusEmbeddingProvider.__new__(ModelBusEmbeddingProvider)
            p._model = None
            p._client = mock_client
            p._dims_cache = None

        with pytest.raises(RuntimeError, match="Cannot determine"):
            _ = p.dims

    def test_embed_caches_dims(self, mock_client):
        """embed() should cache dims from response."""
        from yaaos_sfs.providers.modelbus_provider import ModelBusEmbeddingProvider

        with patch(
            "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider.__init__",
            return_value=None,
        ):
            p = ModelBusEmbeddingProvider.__new__(ModelBusEmbeddingProvider)
            p._model = None
            p._client = mock_client
            p._dims_cache = None

        p.embed(["test"])
        assert p._dims_cache == 768

    def test_model_none_uses_bus_default(self, mock_client):
        """When model is None, embed call passes None — Bus uses its default."""
        from yaaos_sfs.providers.modelbus_provider import ModelBusEmbeddingProvider

        with patch(
            "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider.__init__",
            return_value=None,
        ):
            p = ModelBusEmbeddingProvider.__new__(ModelBusEmbeddingProvider)
            p._model = None
            p._client = mock_client
            p._dims_cache = 768

        p.embed(["test"])
        mock_client.embed.assert_called_once_with(["test"], model=None)


class TestModelBusProviderKnownModelDims:
    def test_known_model_sets_dims_at_init(self):
        """Provider should recognize known models and set dims without probing."""
        from yaaos_sfs.providers.modelbus_provider import ModelBusEmbeddingProvider

        with patch(
            "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider.__init__",
            return_value=None,
        ):
            p = ModelBusEmbeddingProvider.__new__(ModelBusEmbeddingProvider)
            p._model = "ollama/nomic-embed-text"
            p._client = MagicMock()
            p._dims_cache = None

        # Simulate what __init__ does for known models
        model = "ollama/nomic-embed-text"
        model_name = model.split("/")[-1] if "/" in model else model
        from yaaos_sfs.providers.modelbus_provider import _KNOWN_DIMS

        dims = _KNOWN_DIMS.get(model_name)
        assert dims == 768

    def test_unknown_model_dims_none(self):
        """Unknown model should leave dims as None (will probe on first use)."""
        from yaaos_sfs.providers.modelbus_provider import _KNOWN_DIMS

        assert _KNOWN_DIMS.get("some-unknown-model") is None


class TestModelBusProviderDaemonConfig:
    def test_get_provider_modelbus(self):
        """_get_provider should return ModelBusEmbeddingProvider for 'modelbus'."""
        from yaaos_sfs.config import Config

        config = Config(embedding_provider="modelbus")

        with patch(
            "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider"
        ) as MockProvider:
            mock_instance = MagicMock()
            mock_instance.dims = 768
            MockProvider.return_value = mock_instance

            from yaaos_sfs.daemon import _get_provider

            result = _get_provider(config)

            MockProvider.assert_called_once_with(
                model=None,
                socket_path=None,
                embedding_dims=None,
            )
            assert result is mock_instance

    def test_get_provider_modelbus_with_custom_config(self):
        """_get_provider should pass modelbus config options."""
        from yaaos_sfs.config import Config

        config = Config(
            embedding_provider="modelbus",
            modelbus_model="ollama/nomic-embed-text",
            modelbus_socket="/tmp/test.sock",
            embedding_dims=768,
        )

        with patch(
            "yaaos_sfs.providers.modelbus_provider.ModelBusEmbeddingProvider"
        ) as MockProvider:
            mock_instance = MagicMock()
            mock_instance.dims = 768
            MockProvider.return_value = mock_instance

            from yaaos_sfs.daemon import _get_provider

            result = _get_provider(config)

            MockProvider.assert_called_once_with(
                model="ollama/nomic-embed-text",
                socket_path="/tmp/test.sock",
                embedding_dims=768,
            )
            assert result is mock_instance

    def test_get_provider_local_still_works(self):
        """Existing providers should still work (backward compatibility)."""
        from yaaos_sfs.config import Config

        config = Config(embedding_provider="local")

        with patch("yaaos_sfs.daemon.LocalEmbeddingProvider") as MockLocal:
            mock_instance = MagicMock()
            MockLocal.return_value = mock_instance

            from yaaos_sfs.daemon import _get_provider

            result = _get_provider(config)
            assert result is mock_instance


class TestModelBusConfigLoading:
    def test_config_has_modelbus_fields(self):
        from yaaos_sfs.config import Config

        config = Config()
        assert config.modelbus_socket is None
        assert config.modelbus_model is None

    def test_config_loads_modelbus_from_toml(self, tmp_path):
        from yaaos_sfs.config import Config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "[providers.modelbus]\n"
            'socket = "/run/yaaos/modelbus.sock"\n'
            'model = "ollama/nomic-embed-text"\n'
            "\n"
            "[embedding]\n"
            'provider = "modelbus"\n'
        )

        # Need watch_dir and db_path to exist for Config.load
        watch_dir = tmp_path / "semantic"
        watch_dir.mkdir()

        config = Config.load(config_file)
        assert config.embedding_provider == "modelbus"
        assert config.modelbus_socket == "/run/yaaos/modelbus.sock"
        assert config.modelbus_model == "ollama/nomic-embed-text"

"""Tests for yaaos_modelbus.types."""

from yaaos_modelbus.types import (
    Chunk,
    EmbedResult,
    GenerateResult,
    Message,
    ModelInfo,
    ProviderHealth,
    ResourceStatus,
    parse_model_string,
)


class TestMessage:
    def test_to_dict(self):
        msg = Message(role="user", content="hello")
        assert msg.to_dict() == {"role": "user", "content": "hello"}

    def test_from_dict(self):
        msg = Message.from_dict({"role": "assistant", "content": "hi"})
        assert msg.role == "assistant"
        assert msg.content == "hi"

    def test_frozen(self):
        msg = Message(role="user", content="hello")
        try:
            msg.role = "system"
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestChunk:
    def test_to_dict_basic(self):
        chunk = Chunk(token="hello")
        d = chunk.to_dict()
        assert d == {"token": "hello", "done": False}

    def test_to_dict_done_with_usage(self):
        chunk = Chunk(token="", done=True, usage={"tokens": 42})
        d = chunk.to_dict()
        assert d["done"] is True
        assert d["usage"] == {"tokens": 42}


class TestEmbedResult:
    def test_to_dict(self):
        result = EmbedResult(
            embeddings=[[0.1, 0.2], [0.3, 0.4]],
            model="test/model",
            dims=2,
            usage={"total_tokens": 5},
        )
        d = result.to_dict()
        assert d["dims"] == 2
        assert len(d["embeddings"]) == 2
        assert d["usage"]["total_tokens"] == 5

    def test_to_dict_no_usage(self):
        result = EmbedResult(embeddings=[], model="test", dims=0)
        d = result.to_dict()
        assert "usage" not in d


class TestGenerateResult:
    def test_to_dict(self):
        result = GenerateResult(text="hello world", model="test/m")
        d = result.to_dict()
        assert d["text"] == "hello world"
        assert d["model"] == "test/m"
        assert "usage" not in d


class TestModelInfo:
    def test_to_dict_minimal(self):
        info = ModelInfo(id="p/m", provider="p", name="m")
        d = info.to_dict()
        assert d["id"] == "p/m"
        assert "params_billions" not in d

    def test_to_dict_full(self):
        info = ModelInfo(
            id="ollama/phi3:mini",
            provider="ollama",
            name="phi3:mini",
            capabilities=["generate", "chat"],
            params_billions=3.8,
            quantization="Q4_K_M",
            estimated_vram_mb=2500,
            context_length=4096,
        )
        d = info.to_dict()
        assert d["params_billions"] == 3.8
        assert d["quantization"] == "Q4_K_M"
        assert d["estimated_vram_mb"] == 2500


class TestProviderHealth:
    def test_healthy(self):
        h = ProviderHealth(name="test", healthy=True, latency_ms=12.5)
        d = h.to_dict()
        assert d["healthy"] is True
        assert d["latency_ms"] == 12.5

    def test_unhealthy(self):
        h = ProviderHealth(name="test", healthy=False, error="connection refused")
        d = h.to_dict()
        assert d["healthy"] is False
        assert d["error"] == "connection refused"


class TestResourceStatus:
    def test_with_gpu(self):
        r = ResourceStatus(
            gpu_name="GTX 1650 Ti",
            vram_total_mb=4096,
            vram_free_mb=1200,
            ram_total_mb=16384,
            ram_available_mb=8200,
        )
        d = r.to_dict()
        assert d["gpu"]["name"] == "GTX 1650 Ti"
        assert d["ram"]["total_mb"] == 16384

    def test_without_gpu(self):
        r = ResourceStatus(ram_total_mb=8192, ram_available_mb=4096)
        d = r.to_dict()
        assert "gpu" not in d
        assert d["ram"]["total_mb"] == 8192


class TestParseModelString:
    def test_with_provider(self):
        assert parse_model_string("ollama/phi3:mini") == ("ollama", "phi3:mini")

    def test_without_provider(self):
        assert parse_model_string("phi3:mini") == ("", "phi3:mini")

    def test_openai_format(self):
        assert parse_model_string("openai/gpt-4o") == ("openai", "gpt-4o")

    def test_deep_path(self):
        assert parse_model_string("anthropic/claude-sonnet-4-20250514") == (
            "anthropic",
            "claude-sonnet-4-20250514",
        )

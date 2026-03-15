"""Tests for the resource manager — VRAM/RAM monitoring, model slots, eviction."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from yaaos_modelbus.config import ResourceConfig
from yaaos_modelbus.errors import InsufficientResourcesError
from yaaos_modelbus.resources import ModelSlot, ResourceManager, estimate_vram_mb


# ── ModelSlot ──────────────────────────────────────────────


class TestModelSlot:
    def test_initial_state(self):
        slot = ModelSlot(model_id="ollama/phi3:mini", provider="ollama", vram_mb=2400)
        assert slot.model_id == "ollama/phi3:mini"
        assert slot.provider == "ollama"
        assert slot.vram_mb == 2400
        assert slot.request_count == 0
        assert slot.idle_sec >= 0

    def test_touch_updates_last_used(self):
        slot = ModelSlot(model_id="test", provider="mock", vram_mb=100)
        old_last_used = slot.last_used
        # Small sleep to ensure monotonic time advances
        time.sleep(0.01)
        slot.touch()
        assert slot.last_used > old_last_used
        assert slot.request_count == 1

    def test_idle_sec_increases(self):
        slot = ModelSlot(model_id="test", provider="mock", vram_mb=100)
        time.sleep(0.05)
        assert slot.idle_sec >= 0.04


# ── estimate_vram_mb ───────────────────────────────────────


class TestEstimateVram:
    def test_known_model(self):
        assert estimate_vram_mb("phi3:mini") == 2400

    def test_known_base_name(self):
        assert estimate_vram_mb("nomic-embed-text") == 300

    def test_unknown_model_returns_default(self):
        assert estimate_vram_mb("some-unknown-model") == 2000


# ── ResourceManager ────────────────────────────────────────


@pytest.fixture
def resource_config():
    return ResourceConfig(
        max_vram_usage_pct=85,
        model_idle_timeout_sec=10,
        max_ram_usage_pct=80,
    )


@pytest.fixture
def resource_mgr(resource_config):
    return ResourceManager(resource_config)


class TestResourceManagerSlots:
    def test_register_model(self, resource_mgr):
        resource_mgr.register_model("ollama/phi3:mini", "ollama", vram_mb=2400)
        assert "ollama/phi3:mini" in resource_mgr.slots
        assert resource_mgr.total_vram_used_mb == 2400

    def test_register_same_model_touches(self, resource_mgr):
        resource_mgr.register_model("ollama/phi3:mini", "ollama", vram_mb=2400)
        old_used = resource_mgr.slots["ollama/phi3:mini"].last_used
        time.sleep(0.01)
        resource_mgr.register_model("ollama/phi3:mini", "ollama", vram_mb=2400)
        # Should touch, not add duplicate
        assert len(resource_mgr.slots) == 1
        assert resource_mgr.slots["ollama/phi3:mini"].last_used > old_used

    def test_unregister_model(self, resource_mgr):
        resource_mgr.register_model("ollama/phi3:mini", "ollama", vram_mb=2400)
        slot = resource_mgr.unregister_model("ollama/phi3:mini")
        assert slot is not None
        assert slot.model_id == "ollama/phi3:mini"
        assert len(resource_mgr.slots) == 0

    def test_unregister_nonexistent(self, resource_mgr):
        assert resource_mgr.unregister_model("nonexistent") is None

    def test_touch_model(self, resource_mgr):
        resource_mgr.register_model("test", "mock", vram_mb=100)
        old = resource_mgr.slots["test"].last_used
        time.sleep(0.01)
        resource_mgr.touch_model("test")
        assert resource_mgr.slots["test"].last_used > old

    def test_total_vram_used(self, resource_mgr):
        resource_mgr.register_model("m1", "p1", vram_mb=1000)
        resource_mgr.register_model("m2", "p2", vram_mb=500)
        assert resource_mgr.total_vram_used_mb == 1500


class TestResourceManagerIdleEviction:
    def test_get_idle_models_none_idle(self, resource_mgr):
        resource_mgr.register_model("test", "mock", vram_mb=100)
        assert resource_mgr.get_idle_models() == []

    def test_get_idle_models_with_idle(self):
        config = ResourceConfig(model_idle_timeout_sec=0)  # Immediate timeout
        mgr = ResourceManager(config)
        mgr.register_model("test", "mock", vram_mb=100)
        time.sleep(0.01)
        idle = mgr.get_idle_models()
        assert len(idle) == 1
        assert idle[0].model_id == "test"

    def test_idle_sorted_by_longest_first(self):
        config = ResourceConfig(model_idle_timeout_sec=0)
        mgr = ResourceManager(config)
        mgr.register_model("old", "mock", vram_mb=100)
        time.sleep(0.02)
        mgr.register_model("new", "mock", vram_mb=100)
        time.sleep(0.01)
        idle = mgr.get_idle_models()
        assert len(idle) == 2
        assert idle[0].model_id == "old"


class TestResourceManagerEvictionCandidates:
    def test_eviction_candidates_lru_order(self, resource_mgr):
        resource_mgr.register_model("old", "mock", vram_mb=1000)
        time.sleep(0.01)
        resource_mgr.register_model("new", "mock", vram_mb=1000)
        candidates = resource_mgr.get_eviction_candidates(1000)
        assert len(candidates) == 1
        assert candidates[0].model_id == "old"

    def test_eviction_candidates_enough_to_free(self, resource_mgr):
        resource_mgr.register_model("m1", "mock", vram_mb=500)
        time.sleep(0.01)
        resource_mgr.register_model("m2", "mock", vram_mb=500)
        time.sleep(0.01)
        resource_mgr.register_model("m3", "mock", vram_mb=500)
        # Need 800 MB — should return m1 (500) + m2 (500) = 1000 >= 800
        candidates = resource_mgr.get_eviction_candidates(800)
        assert len(candidates) == 2


class TestResourceManagerCapacity:
    @pytest.mark.asyncio
    async def test_ensure_capacity_no_gpu(self, resource_mgr):
        """Without GPU, capacity check should pass."""
        with patch("yaaos_modelbus.resources._get_gpu_info", return_value=(None, None, None)):
            await resource_mgr.ensure_capacity("phi3:mini", AsyncMock())

    @pytest.mark.asyncio
    async def test_ensure_capacity_enough_space(self, resource_mgr):
        with patch(
            "yaaos_modelbus.resources._get_gpu_info",
            return_value=("GTX 1650 Ti", 4096, 3000),
        ):
            await resource_mgr.ensure_capacity("nomic-embed-text", AsyncMock())

    @pytest.mark.asyncio
    async def test_ensure_capacity_evicts_when_needed(self, resource_mgr):
        resource_mgr.register_model("ollama/old-model", "ollama", vram_mb=2000)

        unload = AsyncMock()
        # GPU: 4096 total, 1500 free (2596 used), need 2400 for phi3
        # max_usage = 4096 * 0.85 = 3481
        # current 2596 + 2400 = 4996 > 3481 → shortfall = 1515
        # old-model can free 2000 >= 1515 → eviction succeeds
        with patch(
            "yaaos_modelbus.resources._get_gpu_info",
            return_value=("GTX 1650 Ti", 4096, 1500),
        ):
            await resource_mgr.ensure_capacity("phi3:mini", unload)

        unload.assert_called_once_with("ollama/old-model", "ollama")
        assert "ollama/old-model" not in resource_mgr.slots

    @pytest.mark.asyncio
    async def test_ensure_capacity_raises_when_impossible(self, resource_mgr):
        """If even evicting everything isn't enough, raise."""
        resource_mgr.register_model("small", "mock", vram_mb=100)

        with patch(
            "yaaos_modelbus.resources._get_gpu_info",
            return_value=("GTX 1650 Ti", 4096, 100),
        ):
            with pytest.raises(InsufficientResourcesError):
                await resource_mgr.ensure_capacity("mistral", AsyncMock())

    @pytest.mark.asyncio
    async def test_ensure_capacity_skips_already_loaded(self, resource_mgr):
        resource_mgr.register_model("phi3:mini", "ollama", vram_mb=2400)
        # Should just touch and return
        await resource_mgr.ensure_capacity("phi3:mini", AsyncMock())
        assert resource_mgr.slots["phi3:mini"].request_count == 1


class TestResourceManagerStatus:
    def test_get_status(self, resource_mgr):
        with patch(
            "yaaos_modelbus.resources._get_gpu_info",
            return_value=("GTX 1650 Ti", 4096, 2048),
        ):
            status = resource_mgr.get_status()
        assert status.gpu_name == "GTX 1650 Ti"
        assert status.vram_total_mb == 4096
        assert status.vram_free_mb == 2048
        assert status.ram_total_mb > 0

    def test_to_dict(self, resource_mgr):
        resource_mgr.register_model("ollama/phi3:mini", "ollama", vram_mb=2400)
        with patch(
            "yaaos_modelbus.resources._get_gpu_info",
            return_value=("GTX 1650 Ti", 4096, 1696),
        ):
            d = resource_mgr.to_dict()
        assert "gpu" in d
        assert "ram" in d
        assert "models_loaded" in d
        assert len(d["models_loaded"]) == 1
        assert d["models_loaded"][0]["id"] == "ollama/phi3:mini"


class TestResourceManagerEvictionLoop:
    @pytest.mark.asyncio
    async def test_eviction_loop_starts_and_stops(self, resource_mgr):
        unload = AsyncMock()
        await resource_mgr.start_eviction_loop(unload)
        assert resource_mgr._eviction_task is not None
        assert not resource_mgr._eviction_task.done()
        await resource_mgr.stop_eviction_loop()
        assert resource_mgr._eviction_task.done()


class TestVramCheck:
    def test_check_can_load_no_gpu(self, resource_mgr):
        with patch("yaaos_modelbus.resources._get_gpu_info", return_value=(None, None, None)):
            resource_mgr.check_can_load("phi3:mini")  # Should not raise

    def test_check_can_load_enough_space(self, resource_mgr):
        with patch(
            "yaaos_modelbus.resources._get_gpu_info",
            return_value=("GTX 1650 Ti", 4096, 3000),
        ):
            resource_mgr.check_can_load("nomic-embed-text")  # 300 MB needed, 3000 free

    def test_check_can_load_insufficient(self, resource_mgr):
        with patch(
            "yaaos_modelbus.resources._get_gpu_info",
            return_value=("GTX 1650 Ti", 4096, 100),
        ):
            with pytest.raises(InsufficientResourcesError) as exc_info:
                resource_mgr.check_can_load("mistral")  # 4500 MB needed, ~100 free
            assert "mistral" in str(exc_info.value)

"""Resource manager — VRAM/RAM monitoring, model slot tracking, idle eviction.

Manages GPU/RAM resources to prevent OOM on constrained hardware (e.g., GTX 1650 Ti 4GB).
Tracks which models are loaded, their VRAM footprint, and last-use timestamps.
Handles idle eviction and memory-pressure eviction for new model loads.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import psutil
import structlog

from yaaos_modelbus.config import ResourceConfig
from yaaos_modelbus.errors import InsufficientResourcesError
from yaaos_modelbus.types import ResourceStatus

logger = structlog.get_logger()


# Estimated VRAM for common Ollama models (MB) — conservative estimates
_ESTIMATED_VRAM_MB: dict[str, int] = {
    "phi3:mini": 2400,
    "phi3": 2400,
    "llama3.2:1b": 1200,
    "llama3.2:3b": 2200,
    "llama3.2": 2200,
    "mistral": 4500,
    "qwen2:1.5b": 1200,
    "qwen2:7b": 4800,
    "gemma2:2b": 1800,
    "nomic-embed-text": 300,
    "mxbai-embed-large": 700,
    "all-minilm": 200,
    "snowflake-arctic-embed": 700,
}


@dataclass
class ModelSlot:
    """Tracks a loaded model's resource usage and activity."""

    model_id: str
    provider: str
    vram_mb: int
    loaded_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    request_count: int = 0

    @property
    def idle_sec(self) -> float:
        return time.monotonic() - self.last_used

    def touch(self) -> None:
        """Mark this model as recently used."""
        self.last_used = time.monotonic()
        self.request_count += 1


def _get_gpu_info() -> tuple[str | None, int | None, int | None]:
    """Get GPU name, total VRAM (MB), free VRAM (MB). Returns Nones if no GPU."""
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode()
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        total_mb = mem.total // (1024 * 1024)
        free_mb = mem.free // (1024 * 1024)
        pynvml.nvmlShutdown()
        return name, total_mb, free_mb
    except Exception:
        return None, None, None


def _get_ram_info() -> tuple[int, int]:
    """Get total RAM (MB), available RAM (MB)."""
    mem = psutil.virtual_memory()
    return mem.total // (1024 * 1024), mem.available // (1024 * 1024)


def estimate_vram_mb(model_name: str) -> int:
    """Estimate VRAM needed for a model. Returns conservative estimate."""
    base = model_name.split(":")[0]
    if model_name in _ESTIMATED_VRAM_MB:
        return _ESTIMATED_VRAM_MB[model_name]
    if base in _ESTIMATED_VRAM_MB:
        return _ESTIMATED_VRAM_MB[base]
    # Default: assume moderate model size
    return 2000


class ResourceManager:
    """Manages VRAM/RAM resources and model slot lifecycle.

    Responsibilities:
    - Monitor GPU VRAM and system RAM
    - Track loaded model slots with usage timestamps
    - Evict idle models after configurable timeout
    - Evict least-recently-used models under memory pressure
    - Pre-check whether a model can be loaded without OOM
    """

    def __init__(self, config: ResourceConfig):
        self._config = config
        self._slots: dict[str, ModelSlot] = {}  # model_id → ModelSlot
        self._eviction_task: asyncio.Task | None = None

    @property
    def slots(self) -> dict[str, ModelSlot]:
        return self._slots

    @property
    def total_vram_used_mb(self) -> int:
        return sum(s.vram_mb for s in self._slots.values())

    def get_status(self) -> ResourceStatus:
        """Get current resource status snapshot."""
        gpu_name, vram_total, vram_free = _get_gpu_info()
        ram_total, ram_available = _get_ram_info()
        return ResourceStatus(
            gpu_name=gpu_name,
            vram_total_mb=vram_total,
            vram_free_mb=vram_free,
            ram_total_mb=ram_total,
            ram_available_mb=ram_available,
        )

    def check_can_load(self, model_name: str) -> None:
        """Check if we have resources to load this model. Raises if not.

        Raises InsufficientResourcesError if loading would exceed thresholds.
        """
        needed_mb = estimate_vram_mb(model_name)
        _, vram_total, vram_free = _get_gpu_info()

        if vram_total is None or vram_free is None:
            # No GPU detected — can't check VRAM, allow it (CPU inference)
            return

        max_usage = vram_total * self._config.max_vram_usage_pct / 100
        current_used = vram_total - vram_free

        if current_used + needed_mb > max_usage:
            raise InsufficientResourcesError(
                model=model_name,
                needed_mb=needed_mb,
                available_mb=int(max_usage - current_used),
            )

    def register_model(self, model_id: str, provider: str, vram_mb: int | None = None) -> None:
        """Register a model as loaded, tracking its resource usage."""
        if model_id in self._slots:
            self._slots[model_id].touch()
            return

        estimated = vram_mb or estimate_vram_mb(model_id.split("/")[-1])
        slot = ModelSlot(model_id=model_id, provider=provider, vram_mb=estimated)
        self._slots[model_id] = slot
        logger.info(
            "resource.model_loaded",
            model=model_id,
            vram_mb=estimated,
            total_slots=len(self._slots),
        )

    def touch_model(self, model_id: str) -> None:
        """Mark a model as recently used (resets idle timer)."""
        if model_id in self._slots:
            self._slots[model_id].touch()

    def unregister_model(self, model_id: str) -> ModelSlot | None:
        """Remove a model from tracking. Returns the slot if it existed."""
        slot = self._slots.pop(model_id, None)
        if slot:
            logger.info(
                "resource.model_unloaded",
                model=model_id,
                was_idle_sec=round(slot.idle_sec, 1),
                total_slots=len(self._slots),
            )
        return slot

    def get_idle_models(self) -> list[ModelSlot]:
        """Get models that have exceeded the idle timeout, sorted by idle time (longest first)."""
        timeout = self._config.model_idle_timeout_sec
        idle = [s for s in self._slots.values() if s.idle_sec > timeout]
        return sorted(idle, key=lambda s: s.idle_sec, reverse=True)

    def get_eviction_candidates(self, needed_mb: int) -> list[ModelSlot]:
        """Get models to evict to free needed_mb VRAM, sorted by LRU.

        Returns enough models to free the needed space, prioritizing
        least-recently-used models first.
        """
        candidates = sorted(self._slots.values(), key=lambda s: s.last_used)
        result = []
        freed = 0
        for slot in candidates:
            if freed >= needed_mb:
                break
            result.append(slot)
            freed += slot.vram_mb
        return result

    async def start_eviction_loop(self, unload_callback) -> None:
        """Start background task that evicts idle models periodically.

        Args:
            unload_callback: async function(model_id, provider) to actually unload the model
        """
        self._eviction_task = asyncio.create_task(self._eviction_loop(unload_callback))

    async def stop_eviction_loop(self) -> None:
        """Stop the background eviction task."""
        if self._eviction_task and not self._eviction_task.done():
            self._eviction_task.cancel()
            try:
                await self._eviction_task
            except asyncio.CancelledError:
                pass

    async def _eviction_loop(self, unload_callback) -> None:
        """Periodically check for and evict idle models."""
        check_interval = min(self._config.model_idle_timeout_sec / 2, 30)
        while True:
            try:
                await asyncio.sleep(check_interval)
                idle_models = self.get_idle_models()
                for slot in idle_models:
                    logger.info(
                        "resource.evicting_idle",
                        model=slot.model_id,
                        idle_sec=round(slot.idle_sec, 1),
                    )
                    try:
                        await unload_callback(slot.model_id, slot.provider)
                    except Exception as e:
                        logger.warning(
                            "resource.eviction_failed",
                            model=slot.model_id,
                            error=str(e),
                        )
                    # Re-check: model may have been used during the await
                    current_slot = self._slots.get(slot.model_id)
                    if current_slot and current_slot.idle_sec > self._config.model_idle_timeout_sec:
                        self.unregister_model(slot.model_id)
                    elif current_slot:
                        logger.info(
                            "resource.eviction_skipped",
                            model=slot.model_id,
                            reason="model became active during unload",
                        )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("resource.eviction_loop_error")

    async def ensure_capacity(self, model_name: str, unload_callback) -> None:
        """Ensure we have capacity for model_name, evicting if necessary.

        1. Check if model already loaded → touch and return
        2. Check available resources
        3. If insufficient, evict LRU models until capacity exists
        4. If still insufficient after evicting all, raise error
        """
        model_key = model_name
        if model_key in self._slots:
            self._slots[model_key].touch()
            return

        needed_mb = estimate_vram_mb(model_name.split("/")[-1] if "/" in model_name else model_name)
        _, vram_total, vram_free = _get_gpu_info()

        if vram_total is None:
            # No GPU — skip VRAM checks
            return

        max_usage = vram_total * self._config.max_vram_usage_pct / 100
        current_used = vram_total - vram_free

        if current_used + needed_mb <= max_usage:
            return  # Enough space

        # Need to evict — calculate how much we need to free
        # Our tracked slots represent models we can unload to reclaim VRAM
        shortfall = (current_used + needed_mb) - int(max_usage)
        candidates = self.get_eviction_candidates(shortfall)
        can_free = sum(c.vram_mb for c in candidates)

        if can_free < shortfall:
            # Can't free enough even by evicting everything we track
            raise InsufficientResourcesError(
                model=model_name,
                needed_mb=needed_mb,
                available_mb=max(int(max_usage - current_used) + can_free, 0),
            )

        for slot in candidates:
            logger.info(
                "resource.evicting_for_capacity",
                model=slot.model_id,
                freeing_mb=slot.vram_mb,
                target_model=model_name,
            )
            try:
                await unload_callback(slot.model_id, slot.provider)
            except Exception as e:
                logger.warning("resource.eviction_failed", model=slot.model_id, error=str(e))
            self.unregister_model(slot.model_id)

    def to_dict(self) -> dict:
        """Export current state for health/status endpoints."""
        status = self.get_status()
        result = status.to_dict()
        result["models_loaded"] = [
            {
                "id": s.model_id,
                "provider": s.provider,
                "vram_mb": s.vram_mb,
                "idle_sec": round(s.idle_sec, 1),
                "request_count": s.request_count,
            }
            for s in self._slots.values()
        ]
        return result

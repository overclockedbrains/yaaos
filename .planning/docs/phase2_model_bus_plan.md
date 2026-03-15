# Phase 2: Model Bus — Unified AI Runtime

**Status:** Approved
**Last Updated:** 2026-03-15
**Depends On:** Phase 1.5 (SFS v2) — Complete
**Consumed By:** Phase 3 (SystemAgentd), Phase 4 (Agentic Shell), SFS (migration)

---

## 1. Vision

The Model Bus is the **single inference gateway** for all YAAOS components. Every AI call in the system — embedding, generation, chat — routes through this daemon. No component talks to Ollama, OpenAI, or Anthropic directly. This gives us:

- **One config change** to switch providers system-wide
- **Resource awareness** — prevent OOM by tracking VRAM/RAM before loading models
- **Unified observability** — every inference request logged, metered, traceable
- **Provider isolation** — components depend on the Bus API, not provider SDKs
- **Streaming** — first-class streaming generation with back-pressure

```
┌─────────────────────────────────────────────────────────────┐
│                     YAAOS Components                        │
│  ┌──────┐  ┌────────────┐  ┌──────┐  ┌──────────────────┐   │
│  │ SFS  │  │SystemAgentd│  │ aish │  │ Desktop / Others │   │
│  └──┬───┘  └─────┬──────┘  └──┬───┘  └────────┬─────────┘   │
│     │            │            │               │             │
│     └────────────┴────────────┴───────────────┘             │
│                          │                                  │
│              Unix Socket (JSON-RPC 2.0)                     │
│              /run/yaaos/modelbus.sock                       │
│                          │                                  │
├──────────────────────────┼──────────────────────────────────┤
│                    Model Bus Daemon                         │
│                          │                                  │
│  ┌───────────────────────┼───────────────────────────┐      │
│  │              Request Router                       │      │
│  │  ┌─────────────┐ ┌──────────┐ ┌───────────────┐   │      │
│  │  │ Capability  │ │ Concurr. │ │   Streaming   │   │      │
│  │  │ Matching    │ │ Control  │ │   Proxy       │   │      │
│  │  └─────────────┘ └──────────┘ └───────────────┘   │      │
│  └───────────────────────┼───────────────────────────┘      │
│                          │                                  │
│  ┌───────────────────────┼───────────────────────────┐      │
│  │            Resource Manager                       │      │
│  │  ┌─────────────┐ ┌──────────┐ ┌───────────────┐   │      │
│  │  │ VRAM/RAM    │ │ Model    │ │   Idle        │   │      │
│  │  │ Monitor     │ │ Slots    │ │   Eviction    │   │      │
│  │  └─────────────┘ └──────────┘ └───────────────┘   │      │
│  └───────────────────────┼───────────────────────────┘      │
│                          │                                  │
│  ┌───────────┬───────────┼───────────┬───────────────┐      │
│  │  Ollama   │  OpenAI   │ Anthropic │   Voyage      │      │
│  │ (local)   │  (cloud)  │  (cloud)  │  (embed)      │      │
│  └───────────┴───────────┴───────────┴───────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture Decisions

### 2.1 Transport: asyncio Unix Socket + NDJSON

**Decision:** `asyncio.start_unix_server()` with Newline-Delimited JSON (NDJSON) framing.

**Why not HTTP/REST:**
- This is local IPC, not a web API. HTTP adds unnecessary overhead (headers, content negotiation, connection management).
- Unix sockets give us file-permission-based security for free.
- `asyncio` is stdlib — no framework dependency.

**Why NDJSON over length-prefixed binary:**
- Human-debuggable (`socat` / `nc` can read the socket directly).
- Matches Ollama's streaming format and MCP's transport.
- Simple: each message is one JSON line terminated by `\n`.

**Wire format:**
```
Client → Server:  {"jsonrpc":"2.0","method":"generate","params":{...},"id":1}\n
Server → Client:  {"jsonrpc":"2.0","method":"chunk","params":{"token":"Hello"}}\n
Server → Client:  {"jsonrpc":"2.0","method":"chunk","params":{"token":" world"}}\n
Server → Client:  {"jsonrpc":"2.0","result":{"text":"Hello world","usage":{...}},"id":1}\n
```

Streaming chunks are JSON-RPC **notifications** (no `id`). The final result is a JSON-RPC **response** (with `id` matching the request). This is spec-compliant and proven by MCP.

### 2.2 Protocol: JSON-RPC 2.0

**Methods:**

| Method | Type | Description |
|--------|------|-------------|
| `embed` | Request | Embed text(s) → vectors |
| `generate` | Request | Text completion (streaming) |
| `chat` | Request | Chat completion with messages (streaming) |
| `models.list` | Request | List available models across providers |
| `models.info` | Request | Get model details (params, quant, VRAM) |
| `models.load` | Request | Pre-load a model into VRAM |
| `models.unload` | Request | Unload a model from VRAM |
| `health` | Request | Provider health + resource status |
| `config.get` | Request | Read current config |
| `config.set` | Notification | Update config at runtime |

### 2.3 Provider Interface: typing.Protocol

**Decision:** `typing.Protocol` (structural subtyping) over ABC.

**Why:** Third-party providers can implement the interface without importing our package. `@runtime_checkable` validates at registration time.

```python
from typing import Protocol, AsyncIterator, runtime_checkable

@runtime_checkable
class InferenceProvider(Protocol):
    """Interface every provider must implement."""
    name: str

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop: list[str] | None = None,
    ) -> AsyncIterator[Chunk]: ...

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop: list[str] | None = None,
    ) -> AsyncIterator[Chunk]: ...

    async def embed(
        self,
        model: str,
        texts: list[str],
    ) -> EmbedResult: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def health(self) -> ProviderHealth: ...
```

**Provider discovery:** Entry points for pip-installable third-party plugins:
```toml
[project.entry-points."yaaos.modelbus.providers"]
my_custom = "my_package:MyProvider"
```

### 2.4 Model Routing: `provider/model` Convention

Inspired by LiteLLM. The model string encodes the provider:

```
"ollama/llama3.2"       → Ollama provider, llama3.2 model
"openai/gpt-4o"         → OpenAI provider, gpt-4o model
"anthropic/claude-sonnet-4-20250514" → Anthropic provider
"voyage/voyage-3.5"     → Voyage provider (embed only)
"local/all-MiniLM-L6-v2" → sentence-transformers (direct, no Ollama)
```

If no prefix, route to configured default provider for that capability.

### 2.5 Config: TOML + Env Vars

```toml
# ~/.config/yaaos/modelbus.toml

[daemon]
socket_path = "/run/yaaos/modelbus.sock"
log_level = "info"
max_concurrent_requests = 8

[defaults]
embedding = "ollama/nomic-embed-text"
generation = "ollama/phi3:mini"
chat = "ollama/phi3:mini"

[providers.ollama]
enabled = true
base_url = "http://localhost:11434"

[providers.openai]
enabled = false
# API key always via env: OPENAI_API_KEY

[providers.anthropic]
enabled = false
# API key always via env: ANTHROPIC_API_KEY

[providers.voyage]
enabled = false
# API key always via env: VOYAGE_API_KEY

[providers.local]
enabled = true
device = null  # auto-detect: cuda > mps > cpu

[resources]
max_vram_usage_pct = 85
model_idle_timeout_sec = 300
max_ram_usage_pct = 80
```

**API keys are NEVER in config files.** Always loaded from environment variables.

### 2.6 Resource Management: Ollama-Style Scheduler

Port Ollama's proven `sched.go` patterns to Python:

1. **Model Slots:** Each loaded model tracked as a `ModelSlot` with usage count, last-used time, estimated VRAM.
2. **Idle Eviction:** Models idle > `model_idle_timeout_sec` (default 5 min) are unloaded.
3. **Memory-Pressure Eviction:** If a new model request arrives but VRAM is insufficient, idle models evicted oldest-first.
4. **Pre-load Check:** Before loading, verify `free_vram >= estimated_model_vram`. Reject with clear error if impossible.
5. **VRAM Recovery Polling:** After unloading, poll GPU memory until free VRAM stabilizes before loading new model.

```python
class ModelSlot:
    model_id: str
    provider_name: str
    loaded_at: float
    last_used: float
    active_requests: int
    estimated_vram_bytes: int

class ResourceManager:
    slots: dict[str, ModelSlot]

    async def ensure_model_loaded(self, model_id: str) -> bool:
        if model_id in self.slots:
            self.slots[model_id].last_used = time.monotonic()
            return True
        needed = self.estimate_vram(model_id)
        await self._evict_until_free(needed)
        if self.get_free_vram() < needed:
            raise InsufficientResourcesError(model_id, needed, self.get_free_vram())
        await self._load_model(model_id)
        return True
```

**GPU monitoring:** `pynvml` for NVIDIA (direct C bindings, no shell overhead). `psutil` for RAM. Fallback to `/sys/class/drm/` for AMD.

---

## 3. Data Types

```python
from dataclasses import dataclass, field

@dataclass
class Message:
    role: str          # "system" | "user" | "assistant"
    content: str

@dataclass
class Chunk:
    token: str
    done: bool = False
    usage: dict | None = None  # final chunk includes token counts

@dataclass
class EmbedResult:
    embeddings: list[list[float]]
    model: str
    dims: int
    usage: dict | None = None

@dataclass
class ModelInfo:
    id: str                    # "ollama/phi3:mini"
    provider: str              # "ollama"
    name: str                  # "phi3:mini"
    capabilities: list[str]    # ["generate", "chat"]  or ["embed"]
    params_billions: float | None
    quantization: str | None   # "Q4_K_M"
    estimated_vram_mb: int | None
    context_length: int | None
    embedding_dims: int | None

@dataclass
class ProviderHealth:
    name: str
    healthy: bool
    latency_ms: float | None
    error: str | None
    models_loaded: list[str] = field(default_factory=list)
```

---

## 4. Project Structure

```
src/yaaos-modelbus/
├── pyproject.toml
├── README.md
├── ARCHITECTURE.md
│
├── src/yaaos_modelbus/
│   ├── __init__.py
│   ├── types.py              # Message, Chunk, EmbedResult, ModelInfo, etc.
│   ├── config.py             # TOML + env var config loading (dataclass)
│   ├── errors.py             # Custom exceptions
│   │
│   ├── daemon.py             # Main entry: start server, signal handling, sd_notify
│   ├── server.py             # asyncio Unix socket server, JSON-RPC dispatch
│   ├── client.py             # Python client SDK (sync + async)
│   ├── cli.py                # `yaaos-bus` CLI (health, list, config, test)
│   │
│   ├── router.py             # Capability matching, provider/model routing
│   ├── resources.py          # VRAM/RAM monitor, model slots, eviction
│   ├── streaming.py          # Stream proxy: provider iterators → NDJSON chunks
│   │
│   └── providers/            # Pluggable provider implementations
│       ├── __init__.py       # Provider Protocol + registry + discovery
│       ├── ollama.py         # Ollama REST API (httpx async)
│       ├── openai.py         # OpenAI SDK (async)
│       ├── anthropic.py      # Anthropic SDK (async)
│       ├── voyage.py         # Voyage AI SDK (embed only)
│       └── local.py          # Direct sentence-transformers (no server)
│
├── systemd/
│   └── yaaos-modelbus.service  # systemd unit file
│
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_server.py
    ├── test_router.py
    ├── test_resources.py
    ├── test_client.py
    ├── test_streaming.py
    └── providers/
        ├── test_ollama.py
        ├── test_openai.py
        ├── test_anthropic.py
        ├── test_voyage.py
        └── test_local.py
```

---

## 5. Dependencies

### Core (always installed)
```toml
[project]
dependencies = [
    "httpx>=0.27",            # Async HTTP client (Ollama, health checks)
    "orjson>=3.10",           # Fast JSON encode/decode (3-10x stdlib)
    "pydantic-settings>=2.0", # Type-safe config with TOML + env binding
    "psutil>=5.9",            # RAM/CPU monitoring
    "structlog>=24.0",        # Structured JSON logging
    "click>=8.0",             # CLI framework (matches SFS)
    "rich>=13.0",             # Styled terminal output (matches SFS)
    "tomli>=2.0;python_version<'3.11'",  # TOML parsing fallback
]
```

### Optional Provider Groups
```toml
[project.optional-dependencies]
openai = ["openai>=1.0"]
anthropic = ["anthropic>=0.40"]
voyage = ["voyageai>=0.3"]
local = ["sentence-transformers>=3.0"]  # Direct embedding, no Ollama
nvidia = ["pynvml>=12.0"]              # NVIDIA VRAM monitoring
systemd = ["sdnotify>=0.3"]           # systemd notify protocol
all = ["yaaos-modelbus[openai,anthropic,voyage,local,nvidia,systemd]"]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24", "ruff>=0.5"]
```

### Entry Points
```toml
[project.scripts]
yaaos-bus = "yaaos_modelbus.cli:main"         # CLI tool
yaaos-modelbusd = "yaaos_modelbus.daemon:main" # Daemon process

[project.entry-points."yaaos.modelbus.providers"]
ollama = "yaaos_modelbus.providers.ollama:OllamaProvider"
openai = "yaaos_modelbus.providers.openai:OpenAIProvider"
anthropic = "yaaos_modelbus.providers.anthropic:AnthropicProvider"
voyage = "yaaos_modelbus.providers.voyage:VoyageProvider"
local = "yaaos_modelbus.providers.local:LocalProvider"
```

---

## 6. JSON-RPC API Specification

### 6.1 `embed` — Generate Embeddings

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "embed",
  "params": {
    "texts": ["Hello world", "Another document"],
    "model": "ollama/nomic-embed-text"  // optional, uses default if omitted
  },
  "id": 1
}

// Response
{
  "jsonrpc": "2.0",
  "result": {
    "embeddings": [[0.1, 0.2, ...], [0.3, 0.4, ...]],
    "model": "ollama/nomic-embed-text",
    "dims": 768,
    "usage": {"total_tokens": 12}
  },
  "id": 1
}
```

### 6.2 `generate` — Text Completion (Streaming)

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "generate",
  "params": {
    "prompt": "Explain Linux cgroups in one paragraph",
    "model": "ollama/phi3:mini",
    "temperature": 0.7,
    "max_tokens": 512,
    "stream": true
  },
  "id": 2
}

// Streaming notifications (no id)
{"jsonrpc":"2.0","method":"chunk","params":{"request_id":2,"token":"Linux"}}
{"jsonrpc":"2.0","method":"chunk","params":{"request_id":2,"token":" cgroups"}}
...

// Final response (with id)
{
  "jsonrpc": "2.0",
  "result": {
    "text": "Linux cgroups are...",
    "model": "ollama/phi3:mini",
    "usage": {"prompt_tokens": 12, "completion_tokens": 87}
  },
  "id": 2
}
```

When `"stream": false`, only the final response is sent (no chunk notifications).

### 6.3 `chat` — Chat Completion (Streaming)

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "chat",
  "params": {
    "messages": [
      {"role": "system", "content": "You are a Linux expert."},
      {"role": "user", "content": "What is systemd?"}
    ],
    "model": "anthropic/claude-sonnet-4-20250514",
    "temperature": 0.5,
    "max_tokens": 1024,
    "stream": true
  },
  "id": 3
}
// Same streaming pattern as generate
```

### 6.4 `models.list` — List Available Models

```json
// Request
{"jsonrpc":"2.0","method":"models.list","id":4}

// Response
{
  "jsonrpc": "2.0",
  "result": {
    "models": [
      {
        "id": "ollama/phi3:mini",
        "provider": "ollama",
        "name": "phi3:mini",
        "capabilities": ["generate", "chat"],
        "params_billions": 3.8,
        "quantization": "Q4_K_M",
        "estimated_vram_mb": 2500,
        "context_length": 4096
      },
      {
        "id": "ollama/nomic-embed-text",
        "provider": "ollama",
        "name": "nomic-embed-text",
        "capabilities": ["embed"],
        "embedding_dims": 768
      }
    ]
  },
  "id": 4
}
```

### 6.5 `health` — System Health

```json
// Request
{"jsonrpc":"2.0","method":"health","id":5}

// Response
{
  "jsonrpc": "2.0",
  "result": {
    "status": "healthy",
    "uptime_sec": 3621,
    "resources": {
      "gpu": {"name": "GTX 1650 Ti", "vram_total_mb": 4096, "vram_free_mb": 1200},
      "ram": {"total_mb": 16384, "available_mb": 8200}
    },
    "providers": {
      "ollama": {"healthy": true, "latency_ms": 12, "models_loaded": ["phi3:mini"]},
      "openai": {"healthy": true, "latency_ms": 180},
      "anthropic": {"healthy": false, "error": "ANTHROPIC_API_KEY not set"}
    },
    "active_requests": 2,
    "models_loaded": [
      {"id": "ollama/phi3:mini", "vram_mb": 2500, "idle_sec": 42}
    ]
  },
  "id": 5
}
```

### 6.6 Error Codes

| Code | Meaning |
|------|---------|
| -32600 | Invalid request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |
| -32000 | Provider unavailable |
| -32001 | Model not found |
| -32002 | Insufficient resources (OOM prevention) |
| -32003 | Rate limited |
| -32004 | Provider authentication failed |
| -32005 | Request timeout |

---

## 7. Implementation Phases

### Phase A: Core Infrastructure (Foundation)

**Goal:** A working daemon that accepts connections and dispatches JSON-RPC.

| Task | Deliverable |
|------|-------------|
| A1 | Project scaffolding (pyproject.toml, src layout, uv setup) |
| A2 | `types.py` — all data types (Message, Chunk, EmbedResult, ModelInfo, etc.) |
| A3 | `errors.py` — custom exceptions with JSON-RPC error codes |
| A4 | `config.py` — TOML config loading with pydantic-settings, env var binding |
| A5 | `server.py` — asyncio Unix socket server with NDJSON framing + JSON-RPC dispatch |
| A6 | `client.py` — sync + async Python client (for SFS migration + CLI) |
| A7 | Unit tests for config, types, server protocol, client |

**Success:** Can connect to socket, send `health` request, get response. Tests pass.

### Phase B: Provider System (Pluggable Backends)

**Goal:** Ollama provider working end-to-end with embed + generate + chat.

| Task | Deliverable |
|------|-------------|
| B1 | `providers/__init__.py` — Provider Protocol, registry, entry_point discovery |
| B2 | `router.py` — `provider/model` string parsing, capability matching, default routing |
| B3 | `providers/ollama.py` — Full Ollama provider (embed, generate, chat, list_models, health) via httpx |
| B4 | `streaming.py` — Stream proxy (Ollama NDJSON → JSON-RPC chunk notifications) |
| B5 | `providers/local.py` — Direct sentence-transformers embedding (for offline / no-Ollama use) |
| B6 | Integration tests: embed(), generate(), chat() through full stack (socket → router → Ollama → response) |

**Success:** `echo '{"jsonrpc":"2.0","method":"embed","params":{"texts":["hello"]},"id":1}' | socat - UNIX:/run/yaaos/modelbus.sock` returns embeddings.

### Phase C: Cloud Providers + Config CLI

**Goal:** OpenAI, Anthropic, Voyage working. CLI for management.

| Task | Deliverable |
|------|-------------|
| C1 | `providers/openai.py` — OpenAI provider (embed + generate + chat, streaming via SDK) |
| C2 | `providers/anthropic.py` — Anthropic provider (chat + generate, streaming via SDK) |
| C3 | `providers/voyage.py` — Voyage provider (embed only) |
| C4 | `cli.py` — `yaaos-bus` CLI: `health`, `list`, `embed`, `generate`, `config get/set` |
| C5 | Provider hot-reload: change config → providers re-initialize without daemon restart |
| C6 | Tests for each cloud provider (mocked HTTP responses) |

**Success:** `yaaos-bus config set defaults.generation openai/gpt-4o` switches provider. `yaaos-bus generate "hello"` streams tokens to terminal.

### Phase D: Resource Management + Production Hardening

**Goal:** VRAM-aware model loading, graceful shutdown, systemd integration.

| Task | Deliverable |
|------|-------------|
| D1 | `resources.py` — VRAM/RAM monitor (pynvml + psutil), model slot tracking |
| D2 | Idle eviction: unload models after configurable timeout |
| D3 | Memory-pressure eviction: auto-unload when new model needs VRAM |
| D4 | Pre-load VRAM check: reject requests that would OOM with clear error |
| D5 | Concurrency control: `asyncio.Semaphore` per provider, request queue with back-pressure |
| D6 | Graceful shutdown: drain in-flight requests on SIGTERM, notify systemd |
| D7 | `systemd/yaaos-modelbus.service` — unit file with Type=notify, watchdog, restart |
| D8 | Structured logging via structlog (JSON to stdout, request tracing) |
| D9 | Full test suite: resource manager, eviction, concurrency, shutdown |

**Success:** Model Bus prevents OOM on GTX 1650 Ti (4GB). `systemctl status yaaos-modelbus` shows healthy. Logs are structured JSON.

### Phase E: SFS Migration + Integration

**Goal:** SFS uses Model Bus instead of direct embedding providers.

| Task | Deliverable |
|------|-------------|
| E1 | Create `ModelBusEmbeddingProvider` in SFS that implements `EmbeddingProvider` ABC using Model Bus client |
| E2 | Update SFS config: `provider = "modelbus"` routes through the Bus |
| E3 | Update SFS daemon to start after modelbus.service (systemd ordering) |
| E4 | Backward compatibility: SFS still works standalone with `provider = "local"` (no Bus required) |
| E5 | Integration test: SFS indexes files → embeddings come from Model Bus → search works |

**Success:** SFS works identically but embeddings route through Model Bus. 136 existing SFS tests still pass. Standalone mode preserved.

---

## 8. Key Design Patterns (Matching SFS Conventions)

### Pattern: Registry + Protocol + Factory
Same as SFS extractors/chunkers — global registry dict, lazy loading, graceful degradation:
```python
_REGISTRY: dict[str, type[InferenceProvider]] = {}

def register(name: str, provider_cls: type) -> None: ...
def get_provider(name: str, config: ProviderConfig) -> InferenceProvider: ...
def discover_plugins() -> None:  # entry_points loading
```

### Pattern: Dataclass Config + TOML
Same as SFS `Config` — dataclass with `@classmethod load()`, TOML source, `Path.expanduser()`:
```python
@dataclass
class ModelBusConfig:
    socket_path: Path = Path("/run/yaaos/modelbus.sock")
    max_concurrent_requests: int = 8
    # ...
    @classmethod
    def load(cls, path: Path | None = None) -> "ModelBusConfig": ...
```

### Pattern: Client with Daemon Fallback
Same as SFS `DaemonClient` — try socket first, degrade gracefully:
```python
class ModelBusClient:
    async def embed(self, texts: list[str], model: str | None = None) -> EmbedResult: ...
    async def generate(self, prompt: str, **kwargs) -> AsyncIterator[Chunk]: ...
    # Falls back to local provider if daemon unavailable
```

### Pattern: Graceful Degradation for Optional Deps
Same as SFS extractors — try import, skip if missing:
```python
try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False
```

---

## 9. systemd Integration

```ini
# systemd/yaaos-modelbus.service
[Unit]
Description=YAAOS Model Bus — Unified AI Inference Daemon
Documentation=https://github.com/yaaos/yaaos
After=network.target ollama.service
Wants=ollama.service

[Service]
Type=notify
ExecStart=/usr/bin/python3 -m yaaos_modelbus.daemon
RuntimeDirectory=yaaos
RuntimeDirectoryMode=0755
NotifyAccess=all

# Watchdog: daemon must call sd_notify("WATCHDOG=1") every 30s
WatchdogSec=60

# Resource limits
MemoryMax=2G
CPUQuota=50%

# Restart policy
Restart=on-failure
RestartSec=5
StartLimitBurst=3
StartLimitIntervalSec=60

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/run/yaaos

Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

---

## 10. CLI: `yaaos-bus`

```bash
# Health check
$ yaaos-bus health
Model Bus: healthy (uptime 1h 2m)
GPU: GTX 1650 Ti — 1.2 GB / 4.0 GB free
RAM: 8.2 GB / 16.0 GB available
Providers:
  ollama    ✓ healthy (12ms)    models: phi3:mini, nomic-embed-text
  openai    ✓ healthy (180ms)
  anthropic ✗ ANTHROPIC_API_KEY not set
Active requests: 0
Loaded models: ollama/phi3:mini (2.5 GB VRAM, idle 42s)

# List models
$ yaaos-bus models
ollama/phi3:mini         3.8B Q4_K_M  generate,chat   ~2.5 GB VRAM
ollama/nomic-embed-text  137M         embed           768 dims
openai/gpt-4o            —            generate,chat   cloud
openai/text-embedding-3-small  —      embed           1536 dims

# Quick embed test
$ yaaos-bus embed "hello world"
Model: ollama/nomic-embed-text | Dims: 768 | Tokens: 3
[0.0234, -0.1456, 0.0891, ...]

# Quick generate test
$ yaaos-bus generate "What is FUSE?"
FUSE (Filesystem in Userspace) is a Linux kernel module that allows...

# Config
$ yaaos-bus config get defaults.generation
ollama/phi3:mini
$ yaaos-bus config set defaults.generation openai/gpt-4o
Updated defaults.generation → openai/gpt-4o
```

---

## 11. Success Criteria

| # | Criterion | How to Verify |
|---|-----------|---------------|
| 1 | Any component can `embed()` via socket | `socat` + JSON-RPC request returns vectors |
| 2 | Any component can `generate()` with streaming | Tokens stream to client in real-time |
| 3 | Provider switch is one config change | `yaaos-bus config set defaults.generation openai/gpt-4o` → next generate uses OpenAI |
| 4 | OOM prevention works | Request model exceeding VRAM → clear error, no crash |
| 5 | Idle models auto-unload | Load model, wait 5 min → VRAM freed |
| 6 | SFS works through Model Bus | Index files, search works, 136 SFS tests pass |
| 7 | SFS standalone mode preserved | `provider = "local"` still works without Model Bus |
| 8 | Graceful shutdown | SIGTERM → drain requests → clean exit |
| 9 | Health endpoint comprehensive | Shows GPU, RAM, providers, loaded models |
| 10 | Structured logging | All requests logged as JSON with model, latency, tokens |

---

## 12. VRAM Budget (GTX 1650 Ti — 4 GB)

| Model | VRAM | Use Case |
|-------|------|----------|
| phi3:mini Q4_K_M | ~2.5 GB | Generation / Chat |
| nomic-embed-text | ~0.5 GB | Embeddings |
| **Headroom** | **~1.0 GB** | KV cache + OS |

The Resource Manager enforces `max_vram_usage_pct = 85%` (3.4 GB usable). Loading phi3 + nomic = 3.0 GB, within budget. Attempting Mistral-7B (4.5 GB) → rejected with clear error suggesting CPU offload or cloud fallback.

---

## 13. Testing Strategy

| Layer | Type | Count Target |
|-------|------|-------------|
| Types + Config | Unit | ~20 |
| Server protocol (NDJSON, JSON-RPC) | Unit | ~15 |
| Client (sync + async) | Unit | ~10 |
| Router (model string parsing, routing) | Unit | ~15 |
| Resource Manager (slots, eviction) | Unit | ~15 |
| Each provider (mocked HTTP) | Unit | ~10 each × 5 = ~50 |
| Full stack integration | Integration | ~15 |
| SFS migration | Integration | ~5 |
| **Total** | | **~145+** |

All tests run with `pytest` + `pytest-asyncio`. Cloud provider tests use mocked HTTP responses (no real API calls in CI). Ollama integration tests require Ollama running (marked `@pytest.mark.integration`).

---

## 14. Migration Path for SFS

The SFS migration is **additive, not breaking**:

1. New `ModelBusEmbeddingProvider` added to SFS providers
2. Config gains `provider = "modelbus"` option
3. Existing providers (`local`, `openai`, `voyage`, `ollama`) remain
4. Default stays `local` — Model Bus is opt-in until Phase 3
5. When Model Bus is running, SFS can use any provider the Bus supports (including Anthropic for future multimodal)

```toml
# SFS config — before (direct)
[embedding]
provider = "local"

# SFS config — after (via Model Bus)
[embedding]
provider = "modelbus"
model = "ollama/nomic-embed-text"  # or omit for Bus default
```

---

## 15. Future Considerations (Not in Phase 2)

- **Caching layer:** Cache embedding results by content hash (avoid re-embedding identical text). Defer to Phase 3 when agents create high-volume requests.
- **Request batching:** Auto-batch concurrent embed requests to the same model. Useful when multiple agents query simultaneously. Defer to Phase 3.
- **Metrics export:** Prometheus/OpenTelemetry metrics endpoint. Defer until we have a monitoring stack.
- **Rust rewrite:** Model Bus is on the Rust migration path (hot path for inference routing). Defer to post-Phase 4 when the Python version is battle-tested.
- **Multi-GPU:** Support for systems with multiple GPUs. Not relevant for target hardware (single GTX 1650 Ti).
- **Tool use / function calling:** Provider-agnostic tool-use interface. Defer to Phase 4 (aish needs it).

"""Custom exceptions with JSON-RPC 2.0 error codes."""

from __future__ import annotations

# Standard JSON-RPC 2.0 error codes
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Model Bus custom error codes (-32000 to -32099)
PROVIDER_UNAVAILABLE = -32000
MODEL_NOT_FOUND = -32001
INSUFFICIENT_RESOURCES = -32002
RATE_LIMITED = -32003
AUTH_FAILED = -32004
REQUEST_TIMEOUT = -32005


class ModelBusError(Exception):
    """Base exception for all Model Bus errors."""

    code: int = INTERNAL_ERROR
    message: str = "Internal error"

    def __init__(self, message: str | None = None, data: dict | None = None):
        self.message = message or self.__class__.message
        self.data = data
        super().__init__(self.message)

    def to_jsonrpc_error(self) -> dict:
        """Convert to JSON-RPC 2.0 error object."""
        err: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


class InvalidRequestError(ModelBusError):
    code = INVALID_REQUEST
    message = "Invalid request"


class MethodNotFoundError(ModelBusError):
    code = METHOD_NOT_FOUND
    message = "Method not found"


class InvalidParamsError(ModelBusError):
    code = INVALID_PARAMS
    message = "Invalid params"


class InternalError(ModelBusError):
    code = INTERNAL_ERROR
    message = "Internal error"


class ProviderUnavailableError(ModelBusError):
    code = PROVIDER_UNAVAILABLE
    message = "Provider unavailable"


class ModelNotFoundError(ModelBusError):
    code = MODEL_NOT_FOUND
    message = "Model not found"


class InsufficientResourcesError(ModelBusError):
    code = INSUFFICIENT_RESOURCES
    message = "Insufficient resources"

    def __init__(
        self,
        model: str,
        needed_mb: int | None = None,
        available_mb: int | None = None,
    ):
        data = {"model": model}
        if needed_mb is not None:
            data["needed_mb"] = needed_mb
        if available_mb is not None:
            data["available_mb"] = available_mb
        msg = f"Insufficient resources to load {model}"
        if needed_mb and available_mb:
            msg += f" (needs ~{needed_mb} MB, {available_mb} MB available)"
        super().__init__(msg, data)


class RateLimitedError(ModelBusError):
    code = RATE_LIMITED
    message = "Rate limited"


class AuthFailedError(ModelBusError):
    code = AUTH_FAILED
    message = "Provider authentication failed"


class RequestTimeoutError(ModelBusError):
    code = REQUEST_TIMEOUT
    message = "Request timeout"


class DaemonNotRunning(Exception):
    """Model Bus daemon is not running or socket is unreachable."""

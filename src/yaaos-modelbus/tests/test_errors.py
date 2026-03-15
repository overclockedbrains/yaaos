"""Tests for yaaos_modelbus.errors."""

from yaaos_modelbus.errors import (
    AuthFailedError,
    DaemonNotRunning,
    InsufficientResourcesError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
    ModelBusError,
    ModelNotFoundError,
    ProviderUnavailableError,
    RateLimitedError,
    RequestTimeoutError,
)


class TestModelBusError:
    def test_base_error(self):
        err = ModelBusError("something broke")
        assert str(err) == "something broke"
        assert err.code == -32603

    def test_to_jsonrpc_error(self):
        err = ModelBusError("test error", data={"key": "val"})
        d = err.to_jsonrpc_error()
        assert d["code"] == -32603
        assert d["message"] == "test error"
        assert d["data"] == {"key": "val"}

    def test_no_data(self):
        err = ModelBusError("no data")
        d = err.to_jsonrpc_error()
        assert "data" not in d


class TestSpecificErrors:
    def test_invalid_request(self):
        err = InvalidRequestError()
        assert err.code == -32600

    def test_method_not_found(self):
        err = MethodNotFoundError("unknown method: foo")
        assert err.code == -32601
        assert "foo" in str(err)

    def test_invalid_params(self):
        assert InvalidParamsError().code == -32602

    def test_provider_unavailable(self):
        assert ProviderUnavailableError().code == -32000

    def test_model_not_found(self):
        assert ModelNotFoundError().code == -32001

    def test_rate_limited(self):
        assert RateLimitedError().code == -32003

    def test_auth_failed(self):
        assert AuthFailedError().code == -32004

    def test_request_timeout(self):
        assert RequestTimeoutError().code == -32005


class TestInsufficientResourcesError:
    def test_with_details(self):
        err = InsufficientResourcesError("phi3:mini", needed_mb=2500, available_mb=1200)
        assert err.code == -32002
        assert "phi3:mini" in str(err)
        assert "2500" in str(err)
        assert "1200" in str(err)
        d = err.to_jsonrpc_error()
        assert d["data"]["model"] == "phi3:mini"
        assert d["data"]["needed_mb"] == 2500

    def test_without_details(self):
        err = InsufficientResourcesError("big-model")
        assert "big-model" in str(err)


class TestDaemonNotRunning:
    def test_is_regular_exception(self):
        err = DaemonNotRunning("socket missing")
        assert isinstance(err, Exception)
        assert not isinstance(err, ModelBusError)

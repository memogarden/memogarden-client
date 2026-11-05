"""Tests for the memogarden CLI and provider registry API wrapper."""

import json
from unittest.mock import Mock, patch

import httpx
import pytest

from mg_client.auth import AuthManager
from mg_client.cli import main, provider_payload
from mg_client.exceptions import ConflictError, MemoGardenClientError
from mg_client.providers import ProviderAPI, RegisterProviderRequest


@pytest.fixture
def registered_response():
    """Stored manifest as the server would return it (201)."""
    return {
        "name": "gmail",
        "module": "gmail_importer.provider",
        "class": "GmailProvider",
        "interval": 3600,
        "enabled": True,
        "config": {"batch_limit": 1000},
        "capabilities": ["sync", "create_relations"],
        "registered_at": "2026-11-05T12:00:00Z",
        "last_run_at": None,
        "last_status": None,
        "next_due_at": None,
    }


def _mock_response(status=201, body=None, json_error=False):
    """Build a mock httpx response."""
    response = Mock()
    response.status_code = status
    if json_error:
        response.json.side_effect = ValueError("not json")
        response.text = "raw error"
    else:
        response.json.return_value = body if body is not None else {}
    return response


class TestRegisterProviderRequest:
    """Validation of the registration payload model."""

    def test_minimal_payload_uses_defaults(self):
        request = RegisterProviderRequest(
            name="gmail", module="gmail_importer.provider", class_name="GmailProvider"
        )
        assert request.interval == 3600
        assert request.enabled is True
        assert request.config == {}

    def test_class_alias_roundtrip(self):
        request = RegisterProviderRequest(
            **{"name": "gmail", "module": "m", "class": "GmailProvider"}
        )
        assert request.class_name == "GmailProvider"
        data = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert data["class"] == "GmailProvider"
        assert "description" not in data

    def test_rejects_invalid_name(self):
        with pytest.raises(Exception):
            RegisterProviderRequest(
                **{"name": "Gmail!", "module": "m", "class": "C"}
            )

    def test_rejects_interval_below_60(self):
        with pytest.raises(Exception):
            RegisterProviderRequest(
                **{"name": "gmail", "module": "m", "class": "C", "interval": 30}
            )


class TestProviderAPIRegister:
    """ProviderAPI.register against a mocked HTTP layer."""

    @pytest.fixture
    def auth(self):
        return AuthManager(api_key="mg_sk_agent_test123456")

    @pytest.mark.asyncio
    async def test_register_returns_manifest(self, auth, registered_response):
        api = ProviderAPI("http://localhost:8600", auth)
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_request.return_value = _mock_response(201, registered_response)
            mock_request.return_value.raise_for_status = Mock()
            result = await api.register(
                RegisterProviderRequest(
                    **{"name": "gmail", "module": "gmail_importer.provider",
                       "class": "GmailProvider", "config": {"batch_limit": 1000}}
                )
            )
        assert result["capabilities"] == ["sync", "create_relations"]
        body = mock_request.call_args.kwargs["json"]
        assert body["class"] == "GmailProvider"
        assert body["interval"] == 3600

    @pytest.mark.asyncio
    async def test_register_conflict_maps_to_conflict_error(self, auth):
        api = ProviderAPI("http://localhost:8600", auth)
        response = _mock_response(409)
        response.json.return_value = {
            "error": {"type": "ConflictError", "message": "provider already registered"}
        }
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "409", request=Mock(), response=response
        )
        with patch("httpx.AsyncClient.request", return_value=response):
            with pytest.raises(ConflictError):
                await api.register(
                    RegisterProviderRequest(**{"name": "gmail", "module": "m", "class": "C"})
                )

    @pytest.mark.asyncio
    async def test_request_error_maps_to_client_error(self, auth):
        api = ProviderAPI("http://localhost:8600", auth)
        with patch(
            "httpx.AsyncClient.request",
            side_effect=httpx.RequestError("connection refused"),
        ):
            with pytest.raises(MemoGardenClientError, match="Request failed"):
                await api.register(
                    RegisterProviderRequest(**{"name": "gmail", "module": "m", "class": "C"})
                )


class TestProviderPayload:
    """CLI payload construction for `register provider`."""

    def _args(self, **overrides):
        from mg_client.cli import build_parser

        argv = ["register", "provider"]
        for key, value in overrides.items():
            flag = key.replace("_", "-")
            if value is True:
                argv.append(f"--{flag}")
            else:
                argv.extend([f"--{flag}", str(value)])
        args = build_parser().parse_args(argv)
        return args

    def test_required_flags_build_payload(self):
        payload = provider_payload(
            self._args(name="gmail", module="gmail_importer.provider", **{"class": "GmailProvider"})
        )
        assert payload == {
            "name": "gmail",
            "module": "gmail_importer.provider",
            "class": "GmailProvider",
        }

    def test_missing_required_field_reports_flag_names(self):
        with pytest.raises(Exception, match="--name"):
            provider_payload(self._args(module="m", **{"class": "C"}))

    def test_config_entries_parse_json_values(self):
        payload = provider_payload(
            self._args(
                name="gmail",
                module="m",
                **{"class": "C", "config": "batch_limit=1000"},
            )
        )
        assert payload["config"]["batch_limit"] == 1000

    def test_config_rejects_missing_equals(self):
        from mg_client.cli import CliError

        with pytest.raises(CliError, match="KEY=VALUE"):
            provider_payload(self._args(name="g", module="m", **{"class": "C", "config": "oops"}))

    def test_file_payload_accepted(self, tmp_path):
        path = tmp_path / "payload.json"
        path.write_text(json.dumps({"name": "gmail", "module": "m", "class": "C"}))
        payload = provider_payload(self._args(**{"file": str(path)}))
        assert payload["name"] == "gmail"

    def test_file_rejects_unknown_fields(self, tmp_path):
        from mg_client.cli import CliError

        path = tmp_path / "payload.json"
        path.write_text(json.dumps({"name": "gmail", "module": "m", "class": "C", "creds": "x"}))
        with pytest.raises(CliError, match="unknown payload fields"):
            provider_payload(self._args(**{"file": str(path)}))

    def test_file_rejects_non_object(self, tmp_path):
        from mg_client.cli import CliError

        path = tmp_path / "payload.json"
        path.write_text("[]")
        with pytest.raises(CliError, match="JSON object"):
            provider_payload(self._args(**{"file": str(path)}))

    def test_file_conflicts_with_field_flags(self, tmp_path):
        from mg_client.cli import CliError

        path = tmp_path / "payload.json"
        path.write_text("{}")
        with pytest.raises(CliError, match="cannot be combined"):
            provider_payload(self._args(**{"file": str(path), "name": "gmail"}))


class TestCliMain:
    """End-to-end CLI behavior against a mocked server."""

    @pytest.fixture
    def connection(self, monkeypatch):
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        monkeypatch.setenv("MEMOGARDEN_API_KEY", "mg_sk_agent_test123456")

    def test_register_provider_success(self, connection, capsys, registered_response):
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_request.return_value = _mock_response(201, registered_response)
            mock_request.return_value.raise_for_status = Mock()
            exit_code = main(
                ["register", "provider", "--name", "gmail",
                 "--module", "gmail_importer.provider", "--class", "GmailProvider",
                 "--config", "batch_limit=1000"]
            )
        assert exit_code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["name"] == "gmail"
        assert out["capabilities"] == ["sync", "create_relations"]

    def test_register_provider_conflict_exits_1(self, connection, capsys):
        response = _mock_response(409)
        response.json.return_value = {
            "error": {"type": "ConflictError", "message": "provider already registered"}
        }
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "409", request=Mock(), response=response
        )
        with patch("httpx.AsyncClient.request", return_value=response):
            exit_code = main(
                ["register", "provider", "--name", "gmail",
                 "--module", "m", "--class", "C"]
            )
        assert exit_code == 1
        assert "already registered" in capsys.readouterr().err

    def test_missing_connection_env_exits_1(self, monkeypatch, capsys):
        monkeypatch.delenv("MEMOGARDEN_URL", raising=False)
        monkeypatch.delenv("MEMOGARDEN_API_KEY", raising=False)
        exit_code = main(
            ["register", "provider", "--name", "gmail", "--module", "m", "--class", "C"]
        )
        assert exit_code == 1
        assert "MEMOGARDEN_URL" in capsys.readouterr().err

    def test_invalid_payload_exits_1(self, connection, capsys):
        exit_code = main(
            ["register", "provider", "--name", "Not-A-Slug",
             "--module", "m", "--class", "C"]
        )
        assert exit_code == 1
        assert "invalid registration payload" in capsys.readouterr().err

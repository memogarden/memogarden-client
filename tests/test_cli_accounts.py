"""Tests for provider account CLI verbs and the AccountAPI wrapper (RFC-012)."""

import argparse
import json
from unittest.mock import Mock, patch

import httpx
import pytest

from mg_client.accounts import AccountAPI, RegisterAccountRequest
from mg_client.cli import account_payload, build_parser, main
from mg_client.exceptions import ConflictError, MemoGardenClientError


@pytest.fixture
def account_response():
    """Stored account manifest as the server would return it (201)."""
    return {
        "id": "acc-uuid-1",
        "provider_name": "gmail",
        "platform": "google",
        "subject": "you@gmail.com",
        "label": "work",
        "status": "active",
        "credential_ref": "google:you@gmail.com",
        "config": {},
        "capabilities": [],
        "error": None,
        "interval": None,
        "next_due_at": None,
        "last_run_id": None,
        "last_run_at": None,
        "last_status": None,
        "last_verified_at": None,
        "created_at": "2026-11-14T12:00:00Z",
        "updated_at": "2026-11-14T12:00:00Z",
    }


def _mock_response(status=200, body=None):
    response = Mock()
    response.status_code = status
    response.json.return_value = body if body is not None else {}
    return response


def _env(monkeypatch):
    monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
    monkeypatch.setenv("MEMOGARDEN_API_KEY", "mg_sk_agent_test123456")


class TestRegisterAccountRequest:
    def test_minimal_payload(self):
        request = RegisterAccountRequest(platform="google", subject="you@gmail.com")
        data = request.model_dump(mode="json", exclude_none=True)
        assert data == {"platform": "google", "subject": "you@gmail.com", "config": {}}
        assert "credential_ref" not in data  # server defaults the convention

    def test_rejects_bad_platform(self):
        with pytest.raises(Exception):
            RegisterAccountRequest(platform="Google!", subject="x@y.z")


class TestAccountPayload:
    def _parse(self, argv):
        parser = build_parser()
        return parser.parse_args(argv)

    def test_payload_defaults_platform_from_provider(self):
        args = self._parse(
            ["register", "account", "--provider", "gmail",
             "--account", "you@gmail.com"]
        )
        payload = account_payload(args)
        assert payload["platform"] == "google"
        assert payload["subject"] == "you@gmail.com"
        assert "credential_ref" not in payload

    def test_payload_full(self):
        args = self._parse(
            ["register", "account", "--provider", "gmail", "--account", "a@b.c",
             "--label", "work", "--credential-ref", "google:custom",
             "--config", "batch_limit=500", "--config", "path=/tmp/x"]
        )
        payload = account_payload(args)
        assert payload["label"] == "work"
        assert payload["credential_ref"] == "google:custom"
        assert payload["config"] == {"batch_limit": 500, "path": "/tmp/x"}

    def test_unknown_provider_needs_platform(self):
        args = self._parse(
            ["register", "account", "--provider", "thingverse",
             "--account", "me", "--platform", "thing"]
        )
        assert account_payload(args)["platform"] == "thing"

    def test_unknown_provider_without_platform_rejected(self):
        args = self._parse(
            ["register", "account", "--provider", "thingverse", "--account", "me"]
        )
        from mg_client.cli import CliError

        with pytest.raises(CliError, match="platform"):
            account_payload(args)


class TestAccountAPI:
    @pytest.fixture
    def auth(self):
        from mg_client.auth import AuthManager

        return AuthManager(api_key="mg_sk_agent_test123456")

    @pytest.mark.asyncio
    async def test_register_posts_reference_only(self, auth, account_response):
        api = AccountAPI("http://localhost:8600", auth)
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_request.return_value = _mock_response(201, account_response)
            mock_request.return_value.raise_for_status = Mock()
            result = await api.register(
                "gmail",
                RegisterAccountRequest(platform="google", subject="you@gmail.com"),
            )
        assert result["id"] == "acc-uuid-1"
        body = mock_request.call_args.kwargs["json"]
        assert body["subject"] == "you@gmail.com"
        assert "token" not in json.dumps(body)  # references, never secrets

    @pytest.mark.asyncio
    async def test_conflict_maps_to_conflict_error(self, auth):
        api = AccountAPI("http://localhost:8600", auth)
        response = _mock_response(409)
        response.json.return_value = {
            "error": {"type": "ConflictError", "message": "subject exists"}
        }
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "409", request=Mock(), response=response
        )
        with patch("httpx.AsyncClient.request", return_value=response):
            with pytest.raises(ConflictError):
                await api.register(
                    "gmail",
                    RegisterAccountRequest(platform="google", subject="dupe@gmail.com"),
                )

    @pytest.mark.asyncio
    async def test_remove_handles_204_empty_body(self, auth):
        api = AccountAPI("http://localhost:8600", auth)
        response = Mock()
        response.status_code = 204
        response.content = b""
        with patch("httpx.AsyncClient.request", return_value=response):
            assert await api.remove("gmail", "acc-uuid-1") is None


class TestCliVerbs:
    def _env(self, monkeypatch):
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        monkeypatch.setenv("MEMOGARDEN_API_KEY", "mg_sk_agent_test123456")

    def test_register_account_end_to_end(self, monkeypatch, capsys, account_response):
        self._env(monkeypatch)
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_request.return_value = _mock_response(201, account_response)
            mock_request.return_value.raise_for_status = Mock()
            code = main(
                ["register", "account", "--provider", "gmail",
                 "--account", "you@gmail.com", "--label", "work"]
            )
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["subject"] == "you@gmail.com"
        assert out["credential_ref"] == "google:you@gmail.com"

    def test_list_accounts(self, monkeypatch, capsys, account_response):
        self._env(monkeypatch)
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_request.return_value = _mock_response(
                200, {"accounts": [account_response], "count": 1}
            )
            mock_request.return_value.raise_for_status = Mock()
            code = main(["list", "accounts", "--provider", "gmail"])
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 1

    def test_verify_resolves_by_label(self, monkeypatch, capsys, account_response):
        self._env(monkeypatch)
        listing = _mock_response(
            200, {"accounts": [account_response], "count": 1}
        )
        verified = dict(account_response, status="active", last_verified_at="now")
        verify_resp = _mock_response(200, verified)

        with patch("httpx.AsyncClient.request", side_effect=[listing, verify_resp]) as mock_request:
            for response in (listing, verify_resp):
                if not isinstance(response.side_effect, list):
                    response.raise_for_status = Mock()
            code = main(
                ["verify", "account", "--provider", "gmail", "--label", "work"]
            )
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "active"
        # Second call (verify) hit the account's verify endpoint
        assert mock_request.call_args_list[1].args[0] == "POST"
        assert "/accounts/acc-uuid-1/verify" in mock_request.call_args_list[1].args[1]

    def test_remove_resolves_by_subject(self, monkeypatch, capsys):
        self._env(monkeypatch)
        listing = _mock_response(
            200, {"accounts": [{"id": "acc-uuid-1", "subject": "a@b.c"}], "count": 1}
        )
        empty = Mock()
        empty.status_code = 204
        empty.content = b""
        with patch("httpx.AsyncClient.request", return_value=None) as mock_request:
            mock_request.side_effect = [listing, empty]
            code = main(
                ["remove", "account", "--provider", "gmail", "--subject", "a@b.c"]
            )
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out == {"removed": "acc-uuid-1", "provider": "gmail"}
        assert mock_request.call_args_list[1].args[0] == "DELETE"

    def test_unresolved_label_errors(self, monkeypatch, capsys):
        self._env(monkeypatch)
        listing = _mock_response(200, {"accounts": [], "count": 0})
        with patch("httpx.AsyncClient.request", return_value=listing):
            code = main(
                ["show", "account", "--provider", "gmail", "--label", "nope"]
            )
        assert code == 1
        assert "no account with label 'nope'" in capsys.readouterr().err

    def test_missing_connection_env_errors(self, monkeypatch, capsys):
        monkeypatch.delenv("MEMOGARDEN_URL", raising=False)
        monkeypatch.delenv("MEMOGARDEN_API_KEY", raising=False)
        code = main(["list", "accounts", "--provider", "gmail"])
        assert code == 1
        assert "no API" in capsys.readouterr().err

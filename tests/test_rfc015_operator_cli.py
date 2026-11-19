"""
Tests for the RFC-015 operator surface: AuthManager JWT transport, the
session store, the Tier-0 session verbs, and the identity/authority
wrappers + CLI verbs (Tier 1-3).
"""

import argparse
import json
from unittest.mock import Mock, patch

import pytest

from mg_client.auth import AuthManager
from mg_client.cli import CliError, build_parser, main
from mg_client.identity import OperatorAPI
from mg_client.operators import OperatorReadsAPI
from mg_client.session import (
    clear_session,
    load_session,
    resolve_jwt,
    save_session,
    session_path,
)


def _mock_response(status=200, body=None):
    response = Mock()
    response.status_code = status
    response.json.return_value = body if body is not None else {}
    response.content = b"{}"
    return response


# ============================================================================
# AuthManager: JWT transport
# ============================================================================


class TestAuthManagerJwt:
    def test_jwt_headers_use_bearer(self):
        auth = AuthManager(jwt="eyJ.abc.def")
        assert auth.get_headers() == {"Authorization": "Bearer eyJ.abc.def"}
        assert auth.auth_type == "jwt"

    def test_api_key_headers_unchanged(self):
        auth = AuthManager(api_key="mg_sk_agent_abc123456")
        assert auth.get_headers() == {"X-API-Key": "mg_sk_agent_abc123456"}

    def test_explicit_auth_type_wins(self):
        auth = AuthManager(api_key="mg_sk_agent_abc123456", jwt="eyJ", auth_type="jwt")
        assert "Authorization" in auth.get_headers()

    def test_jwt_requires_token(self):
        with pytest.raises(ValueError, match="jwt is required"):
            AuthManager(jwt=None, auth_type="jwt")

    def test_inference_api_key(self):
        auth = AuthManager(api_key="mg_sk_agent_abc123456")
        assert auth.auth_type == "api_key"


# ============================================================================
# Session store
# ============================================================================


class TestSessionStore:
    @pytest.fixture
    def config_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        return tmp_path / "cfg"

    def test_save_and_load_roundtrip(self, config_dir):
        path = save_session("http://localhost:8600/", "tok-1", {"username": "admin"})
        assert path == config_dir / "operator-session.json"
        assert (path.stat().st_mode & 0o777) == 0o600
        session = load_session()
        assert session["url"] == "http://localhost:8600"  # trailing slash stripped
        assert session["access_token"] == "tok-1"
        assert session["user"] == {"username": "admin"}

    def test_load_missing_returns_none(self, config_dir):
        assert load_session() is None

    def test_clear_session(self, config_dir):
        save_session("http://x", "tok")
        assert clear_session() is True
        assert clear_session() is False
        assert load_session() is None

    def test_resolve_jwt_env_overrides_session(self, config_dir, monkeypatch):
        save_session("http://x", "session-tok")
        monkeypatch.setenv("MEMOGARDEN_JWT", "env-tok")
        jwt, url = resolve_jwt(None)
        assert jwt == "env-tok"
        assert url is None  # env token has no session url

    def test_resolve_jwt_session_fallback(self, config_dir, monkeypatch):
        monkeypatch.delenv("MEMOGARDEN_JWT", raising=False)
        save_session("http://x", "session-tok")
        jwt, url = resolve_jwt(None)
        assert jwt == "session-tok"
        assert url == "http://x"

    def test_resolve_jwt_explicit_flag(self, config_dir):
        save_session("http://x", "session-tok")
        jwt, _ = resolve_jwt("flag-tok")
        assert jwt == "flag-tok"


# ============================================================================
# Governed verbs fail fast without a session
# ============================================================================


class TestSessionGate:
    def _env(self, monkeypatch):
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        monkeypatch.delenv("MEMOGARDEN_JWT", raising=False)
        monkeypatch.delenv("MEMOGARDEN_API_KEY", raising=False)

    def test_list_agents_requires_login(self, monkeypatch, tmp_path, capsys):
        self._env(monkeypatch)
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        assert main(["list", "agents"]) == 1
        assert "memogarden login" in capsys.readouterr().err

    def test_register_scope_requires_login(self, monkeypatch, tmp_path, capsys):
        self._env(monkeypatch)
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        assert main(["register", "scope", "--name", "X"]) == 1
        assert "memogarden login" in capsys.readouterr().err

    def test_whoami_requires_any_credential(self, monkeypatch, tmp_path, capsys):
        self._env(monkeypatch)
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        assert main(["whoami"]) == 1


# ============================================================================
# OperatorAPI + OperatorReadsAPI wrappers
# ============================================================================


class TestOperatorAPI:
    def _api(self):
        return OperatorAPI("http://localhost:8600", AuthManager(jwt="tok"))

    async def test_login_posts_credentials(self):
        captured = {}

        async def fake_request(base_url, auth, method, path, payload, timeout):
            captured.update(base_url=base_url, auth=auth, method=method,
                            path=path, payload=payload)
            return {"access_token": "tok", "user": {"username": "admin"}}

        with patch("mg_client.identity.request_json", side_effect=fake_request):
            result = await self._api().login("admin", "pw")
        assert result["access_token"] == "tok"
        assert captured["path"] == "/auth/login"
        assert captured["payload"] == {"username": "admin", "password": "pw"}
        assert captured["auth"] is None  # login is unauthenticated

    async def test_mint_agent(self):
        captured = {}

        async def fake_request(base_url, auth, method, path, payload, timeout):
            captured.update(method=method, path=path, payload=payload)
            return {"agent_id": "core_1", "label": "readly"}

        with patch("mg_client.identity.request_json", side_effect=fake_request):
            result = await self._api().mint_agent("readly")
        assert result["label"] == "readly"
        assert captured["method"] == "POST"
        assert captured["path"] == "/agents"
        assert captured["payload"] == {"label": "readly"}

    async def test_unbind_sends_explicit_null(self):
        captured = {}

        async def fake_request(base_url, auth, method, path, payload, timeout):
            captured.update(method=method, path=path, payload=payload)
            return {}

        with patch("mg_client.identity.request_json", side_effect=fake_request):
            await self._api().unbind_key("key-1")
        assert captured["method"] == "PATCH"
        assert captured["path"] == "/api-keys/key-1"
        assert captured["payload"] == {"agent_id": None}  # present-null = unbind

    async def test_revoke_key_uses_delete(self):
        captured = {}

        async def fake_request(base_url, auth, method, path, payload, timeout):
            captured.update(method=method, path=path)
            return None

        with patch("mg_client.identity.request_json", side_effect=fake_request):
            await self._api().revoke_key("key-1")
        assert captured["method"] == "DELETE"
        assert captured["path"] == "/api-keys/key-1"

    async def test_issue_grant(self):
        captured = {}

        async def fake_request(base_url, auth, method, path, payload, timeout):
            captured.update(payload=payload)
            return {"grants": [{"grant_id": "g1"}]}

        with patch("mg_client.identity.request_json", side_effect=fake_request):
            await self._api().issue_grant(
                scope_uuid="abc", agent_id="core_9", expires_at="2027-01-01T00:00:00Z"
            )
        assert captured["payload"] == {
            "scope_uuid": "abc",
            "agent_id": "core_9",
            "expires_at": "2027-01-01T00:00:00Z",
        }


class TestOperatorReadsAPI:
    async def test_triage_query_params(self):
        captured = {}

        async def fake_request(base_url, auth, method, path, payload, timeout):
            captured.update(path=path)
            return {"facts": [], "total_facts": 0}

        with patch("mg_client.operators.request_json", side_effect=fake_request):
            await OperatorReadsAPI("http://x", AuthManager(jwt="t")).triage(
                scope="triage", account="UOB", limit=10, offset=5
            )
        assert captured["path"] == "/api/v1/triage?scope=triage&account=UOB&limit=10&offset=5"

    async def test_pending_review_params(self):
        captured = {}

        async def fake_request(base_url, auth, method, path, payload, timeout):
            captured.update(path=path)
            return {"events": []}

        with patch("mg_client.operators.request_json", side_effect=fake_request):
            await OperatorReadsAPI("http://x", AuthManager(jwt="t")).pending_review(
                window_days=7, limit=20
            )
        assert captured["path"] == "/api/v1/events/pending-review?window_days=7&limit=20"


# ============================================================================
# ProviderAPI additions (Tier 2)
# ============================================================================


class TestProviderTier2:
    async def test_run_due_posts_heartbeat(self):
        captured = {}

        class FakeClient:
            def __init__(self, response):
                self.response = response

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def request(self, method, url, json=None, headers=None):
                captured.update(method=method, url=url)
                return _mock_response(202, {"triggered": []})

        with patch("httpx.AsyncClient", return_value=FakeClient(None)):
            from mg_client.providers import ProviderAPI
            from mg_client.auth import AuthManager

            await ProviderAPI("http://x", AuthManager(api_key="mg_sk_abc123456")).run_due()
        assert captured["method"] == "POST"
        assert captured["url"] == "http://x/api/v1/providers/run"

    async def test_get_run_hits_runs_endpoint(self):
        captured = {}

        async def fake_inner(method, url, payload=None):
            captured.update(method=method, url=url)
            return {"id": "r1", "status": "success"}

        # providers.py uses its own _request; patch httpx request instead
        response = _mock_response(200, {"id": "r1", "status": "success"})
        response.raise_for_status = Mock()

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def request(self, *args, **kwargs):
                captured.update(method=args[0] if args else kwargs.get("method"),
                                url=args[1] if len(args) > 1 else kwargs.get("url"))
                return response

        with patch("httpx.AsyncClient", return_value=FakeClient()):
            from mg_client.providers import ProviderAPI
            from mg_client.auth import AuthManager

            result = await ProviderAPI(
                "http://x", AuthManager(api_key="mg_sk_abc123456")
            ).get_run("r1")
        assert result["id"] == "r1"
        assert captured["url"] == "http://x/api/v1/runs/r1"


# ============================================================================
# CLI end-to-end over mocked transport
# ============================================================================


class TestCliVerbs:
    def _patch_transport(self, routes):
        """Patch httpx.AsyncClient.request with a route table
        (method, path-suffix) -> (status, body)."""
        def make_request():
            async def request(self, method, url, json=None, headers=None):
                for (m, suffix), (status, body) in routes.items():
                    if method == m and url.endswith(suffix):
                        response = _mock_response(status, body)
                        response.raise_for_status = Mock()
                        if status >= 400:
                            response.raise_for_status.side_effect = None
                        return response
                raise AssertionError(f"no route for {method} {url}")
            return request

        return patch("httpx.AsyncClient.request", new=make_request())

    def test_change_password_flow(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        routes = {
            ("POST", "/auth/change-password"): (200, {"message": "Password updated"}),
        }
        with self._patch_transport(routes):
            code = main(["change-password", "--current", "Old1", "--new", "NewPass456"])
        assert code == 0

    def test_register_agent_mints(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        routes = {("POST", "/agents"): (201, {"agent_id": "core_1", "label": "readly"})}
        with self._patch_transport(routes):
            code = main(["register", "agent", "--label", "readly"])
        assert code == 0
        assert "readly" in capsys.readouterr().out

    def test_register_grant_resolves_scope_label(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        calls = []

        def make_request():
            async def request(self, method, url=None, **kwargs):
                calls.append((method, url, kwargs.get("json")))
                if url.endswith("/api/v1/scopes/overview"):
                    body = {"scopes": [{"uuid": "core_t1", "label": "Triage / Untrusted"}]}
                elif url.endswith("/grants"):
                    body = {"grants": [{"grant_id": "g1"}]}
                else:
                    raise AssertionError(f"unexpected {method} {url}")
                response = _mock_response(200, body)
                response.raise_for_status = Mock()
                return response
            return request

        with patch("httpx.AsyncClient.request", new=make_request()):
            code = main([
                "register", "grant", "--key", "k1", "--scope", "triage", "--scope", "Finances?nope",
            ])
        assert code == 1  # second ref unresolvable — label mismatch fails fast
        # Refs resolve ALL before any grant is issued (no partial grants):
        assert not any(m == "POST" and u.endswith("/grants") for m, u, _ in calls)

    def test_edit_account_sends_only_set_fields(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMOGARDEN_JWT", raising=False)
        monkeypatch.setenv("MEMOGARDEN_API_KEY", "mg_sk_abc123456")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        bodies = []

        def make_request():
            async def request(self, method, url=None, **kwargs):
                bodies.append((method, url, kwargs.get("json")))
                if method == "GET" and url.endswith("/accounts"):
                    response = _mock_response(200, {"accounts": [
                        {"id": "acc-1", "label": "nyjc", "subject": "ny@jc.edu"}
                    ]})
                elif method == "PATCH":
                    response = _mock_response(200, {"id": "acc-1", "status": "disabled"})
                else:
                    raise AssertionError(f"unexpected {method} {url}")
                response.raise_for_status = Mock()
                return response
            return request

        with patch("httpx.AsyncClient.request", new=make_request()):
            code = main([
                "edit", "account", "--provider", "gmail", "--label", "nyjc",
                "--status", "disabled", "--interval", "3540",
                "--config", "batch_limit=2500",
            ])
        assert code == 0
        patch_calls = [(m, u, b) for m, u, b in bodies if m == "PATCH"]
        assert len(patch_calls) == 1
        _, _, body = patch_calls[0]
        assert body == {"status": "disabled", "interval": 3540,
                        "config": {"batch_limit": 2500}}

    def _edit_account_policy_body(self, monkeypatch, tmp_path, flags):
        """Run `edit account` with the given policy flags; return the PATCH body."""
        monkeypatch.delenv("MEMOGARDEN_API_KEY", raising=False)
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        bodies = []

        def make_request():
            async def request(self, method, url=None, **kwargs):
                bodies.append((method, url, kwargs.get("json")))
                if method == "GET" and url.endswith("/accounts"):
                    response = _mock_response(200, {"accounts": [
                        {"id": "acc-1", "label": "nyjc", "subject": "ny@jc.edu"}
                    ]})
                elif method == "PATCH":
                    response = _mock_response(200, {"id": "acc-1"})
                else:
                    raise AssertionError(f"unexpected {method} {url}")
                response.raise_for_status = Mock()
                return response
            return request

        with patch("httpx.AsyncClient.request", new=make_request()):
            code = main([
                "edit", "account", "--provider", "gmail", "--label", "nyjc",
            ] + flags)
        assert code == 0
        patch_calls = [(m, u, b) for m, u, b in bodies if m == "PATCH"]
        assert len(patch_calls) == 1
        return patch_calls[0][2]

    def test_edit_account_default_scope_sends_policy(self, monkeypatch, tmp_path):
        body = self._edit_account_policy_body(
            monkeypatch, tmp_path, ["--default-scope", "NYJC"])
        assert body == {"scope_policy": {"rules": [], "default": "NYJC"}}

    def test_edit_account_default_scope_none_is_explicitly_unscoped(
        self, monkeypatch, tmp_path
    ):
        body = self._edit_account_policy_body(
            monkeypatch, tmp_path, ["--default-scope", "none"])
        assert body == {"scope_policy": {"rules": [], "default": None}}

    def test_edit_account_clear_scope_policy_sends_explicit_null(
        self, monkeypatch, tmp_path
    ):
        """--clear-scope-policy must reach the wire as a JSON null (the
        exclude_none regression guard — dropping the key would be a no-op
        PATCH, not an override removal)."""
        body = self._edit_account_policy_body(
            monkeypatch, tmp_path, ["--clear-scope-policy"])
        assert body == {"scope_policy": None}

    def test_relabel_amends_with_full_scope_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        bodies = []

        def make_request():
            async def request(self, method, url=None, **kwargs):
                body_json = kwargs.get("json") or {}
                bodies.append((method, url, body_json))
                if url.endswith("/mg"):
                    if body_json.get("op") == "get":
                        body = {"ok": True, "actor": "admin",
                            "timestamp": "2026-11-19T00:00:00Z",
                            "result": {
                            "uuid": "soil_1", "type": "Email",
                            "data": {"subject": "x"},
                            "metadata": {"scopes": ["triage"]},
                        }}
                    elif body_json.get("op") == "amend":
                        body = {"ok": True, "actor": "admin",
                            "timestamp": "2026-11-19T00:00:00Z",
                            "result": {"uuid": "soil_2"}}
                    else:
                        raise AssertionError(f"unexpected op {body_json.get('op')}")
                elif url.endswith("/api/v1/scopes/overview"):
                    body = {"scopes": [
                        {"uuid": "core_triage", "label": "Triage / Untrusted"},
                        {"uuid": "core_fin", "label": "Finances"},
                    ]}
                else:
                    raise AssertionError(f"unexpected {method} {url}")
                response = _mock_response(200, body)
                response.raise_for_status = Mock()
                return response
            return request

        with patch("httpx.AsyncClient.request", new=make_request()):
            code = main([
                "relabel", "fact", "--id", "soil_1",
                "--add", "Finances", "--remove", "triage",
            ])
        assert code == 0
        amends = [b for m, u, b in bodies if m == "POST" and u.endswith("/mg") and b.get("op") == "amend"]
        assert len(amends) == 1
        # Full desired list sent (amend overlays metadata wholesale):
        assert amends[0]["metadata"]["scopes"] == ["fin"]
        assert amends[0]["data"] == {"subject": "x"}

    def test_list_scopes_prints_overview(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")

        def make_request():
            async def request(self, method, url=None, **kwargs):
                body = {"enforcement": False, "scopes": [
                    {"uuid": "core_t1", "label": "Triage / Untrusted",
                     "fact_count": 3, "entity_counts": {}}
                ]}
                response = _mock_response(200, body)
                response.raise_for_status = Mock()
                return response
            return request

        with patch("httpx.AsyncClient.request", new=make_request()):
            code = main(["list", "scopes"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Triage / Untrusted" in out
        assert '"fact_count": 3' in out

    # ------------------------------------------------------------------
    # Scope visibility (RFC-011 visibility addendum): register knob,
    # edit flip, overview passthrough.
    # ------------------------------------------------------------------

    def _patched_request(self, capture, responder):
        def make_request():
            # httpx invokes request() with explicit kwargs (content=, json=, …)
            # — accept everything and pull out what the assertions need.
            async def request(self, *args, **kwargs):
                method = args[0] if args else kwargs.get("method")
                url = args[1] if len(args) > 1 else kwargs.get("url")
                json_body = kwargs.get("json")
                if json_body is not None:
                    capture.append((method, url, json_body))
                body = responder(url, json_body)
                response = _mock_response(200, body)
                response.raise_for_status = Mock()
                return response
            return request
        return make_request

    def test_register_scope_visibility_defaults_private(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        capture = []

        def responder(url, body):
            return {"ok": True, "actor": "core_op", "result": {"uuid": "core_new"}, "timestamp": "2026-11-19T00:00:00Z"}

        with patch("httpx.AsyncClient.request", new=self._patched_request(capture, responder)()):
            code = main(["register", "scope", "--name", "Household"])
        assert code == 0
        creates = [b for _, u, b in capture if u.endswith("/mg") and b.get("op") == "create"]
        assert creates[0]["data"]["visibility"] == "private"

    def test_register_scope_visibility_public(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        capture = []

        def responder(url, body):
            return {"ok": True, "actor": "core_op", "result": {"uuid": "core_new"}, "timestamp": "2026-11-19T00:00:00Z"}

        with patch("httpx.AsyncClient.request", new=self._patched_request(capture, responder)()):
            code = main(["register", "scope", "--name", "Household", "--visibility", "public"])
        assert code == 0
        creates = [b for _, u, b in capture if u.endswith("/mg") and b.get("op") == "create"]
        assert creates[0]["data"]["visibility"] == "public"

    def test_edit_scope_flips_visibility(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
        capture = []

        def responder(url, body):
            if str(url).endswith("/api/v1/scopes/overview"):
                return {"scopes": [{"uuid": "core_h1", "label": "Household",
                                    "visibility": "private"}]}
            return {"ok": True, "actor": "core_op", "result": {}, "timestamp": "2026-11-19T00:00:00Z"}

        with patch("httpx.AsyncClient.request", new=self._patched_request(capture, responder)()):
            code = main(["edit", "scope", "--name", "Household",
                         "--visibility", "public"])
        assert code == 0
        edits = [b for _, u, b in capture if u.endswith("/mg") and b.get("op") == "edit"]
        assert edits[0]["target"] == "h1"  # resolver strips the core_ prefix (DB convention)
        assert edits[0]["set"] == {"visibility": "public"}

    def test_edit_scope_unknown_name_fails(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("MEMOGARDEN_JWT", "tok")
        monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")

        def make_request():
            async def request(self, method, url=None, **kwargs):
                body = {"scopes": []}
                response = _mock_response(200, body)
                response.raise_for_status = Mock()
                return response
            return request

        with patch("httpx.AsyncClient.request", new=make_request()):
            assert main(["edit", "scope", "--name", "Nope",
                         "--visibility", "public"]) == 1
            assert "unknown scope 'Nope'" in capsys.readouterr().err

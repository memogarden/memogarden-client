"""Pytest configuration for MemoGarden client tests."""

import pytest


@pytest.fixture(autouse=True)
def isolated_session_env(tmp_path, monkeypatch):
    """Point the session store at a per-test dir and clear JWT env.

    Without this, tests running on the operator's machine pick up the
    real ~/.config/memogarden/operator-session.json — governed verbs
    would silently authenticate against the live server.
    """
    monkeypatch.setenv("MEMOGARDEN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("MEMOGARDEN_JWT", raising=False)


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )

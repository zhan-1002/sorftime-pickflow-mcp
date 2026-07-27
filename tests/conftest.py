"""Test isolation for the local SQLite cache."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_pickflow_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from pickflow_mcp_server import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "CACHE_DB", tmp_path / "aba_cache.db")

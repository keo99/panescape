"""Keep every test away from the real keyring, config and Unsplash key."""

import pytest


@pytest.fixture(autouse=True)
def _private_dirs(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("PANESCAPE_NO_KEYRING", "1")
    monkeypatch.delenv("PANESCAPE_UNSPLASH_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))

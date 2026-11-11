"""Keep every test away from the real keyring, config, app menu and Unsplash key."""

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _private_dirs(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("PANESCAPE_NO_KEYRING", "1")
    monkeypatch.delenv("PANESCAPE_UNSPLASH_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """Skip Python's shutdown clean-up once Qt has been used.

    PySide6 can crash while Python tears down its objects at exit ("Fatal
    Python error: bool_dealloc"), failing a run whose tests all passed. By now
    pytest has printed its report, so leave straight away with its exit code.
    """
    if "PySide6.QtCore" not in sys.modules:
        return
    status = getattr(config, "_panescape_exitstatus", None)
    if status is None:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(status))


def pytest_sessionfinish(session, exitstatus):
    session.config._panescape_exitstatus = exitstatus

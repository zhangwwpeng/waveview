"""Shared pytest fixtures for deterministic test isolation."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_host_font_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore host kitty.conf and WAVEVIEW_FONT so SVG snapshots stay deterministic."""
    monkeypatch.setenv("WAVEVIEW_KITTY_CONF", "none")
    monkeypatch.delenv("WAVEVIEW_FONT", raising=False)

"""Errors and warnings raised by waveview."""

from __future__ import annotations

import os
import warnings


class WaveError(ValueError):
    """A diagram description that cannot be drawn."""


class WaveWarning(UserWarning):
    """A diagram that draws, but is probably not what was meant."""


_PACKAGE_DIR = os.path.dirname(__file__)


def warn(message: str) -> None:
    # Point the warning at the user's line, not at waveview's internals.
    warnings.warn(message, WaveWarning, skip_file_prefixes=(_PACKAGE_DIR,))

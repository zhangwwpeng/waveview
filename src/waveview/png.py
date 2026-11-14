"""SVG -> PNG through resvg."""

from __future__ import annotations

import resvg_py


def svg_to_png(svg: str, scale: float = 2) -> bytes:
    # resvg loads the system fonts, so CJK labels render with e.g. PingFang.
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, zoom=scale))

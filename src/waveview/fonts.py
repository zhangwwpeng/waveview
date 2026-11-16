"""Font stack resolution with automatic Kitty ``kitty.conf`` detection."""

from __future__ import annotations

import glob
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Literal

from .errors import WaveError

DEFAULT_SANS = (
    "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Hiragino Sans GB', "
    "'Noto Sans CJK SC', 'Microsoft YaHei', Helvetica, Arial, sans-serif"
)
DEFAULT_MONO = (
    "'SF Mono', 'JetBrains Mono', Menlo, Consolas, 'DejaVu Sans Mono', "
    "'PingFang SC', 'Hiragino Sans GB', 'Noto Sans CJK SC', monospace"
)

_GENERIC_FAMILIES = frozenset(
    {
        "sans-serif",
        "serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-sans-serif",
        "ui-monospace",
        "-apple-system",
        "blinkmacsystemfont",
    }
)
_DISABLED_VALUES = frozenset({"", "0", "false", "no", "off", "none", "default"})
_CP_ITEM_RE = re.compile(r"^u\+([0-9a-f]+)(?:-u\+([0-9a-f]+))?$", re.IGNORECASE)
_CHOOSE_FAMILY_RE = re.compile(r'(?:^|\s)family=(?:"([^"]+)"|\'([^\']+)\'|([^\s"\']+))')
_CHOOSE_FULL_NAME_RE = re.compile(r'(?:^|\s)full_name=(?:"([^"]+)"|\'([^\']+)\'|([^\s"\']+))')
_CHOOSE_KV_RE = re.compile(r"(?:^|\s)(?:postscript_name|full_name|variable_name|style|features)=")
_MAX_INCLUDE_DEPTH = 8


@dataclass(frozen=True)
class FontSpec:
    """Resolved SVG font stacks and provenance for a Diagram."""

    sans: str
    mono: str
    families: tuple[str, ...] = ()
    source: Literal["default", "kitty", "env", "argument"] = "default"


def _strip_outer_quotes(text: str) -> str:
    s = text.strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    return s


def _clean_family_name(raw: str) -> str:
    cleaned = _strip_outer_quotes(raw).replace("'", "").replace('"', "").strip()
    return cleaned


def _format_family_token(family: str) -> str:
    cleaned = _clean_family_name(family)
    if not cleaned:
        return ""
    if cleaned.lower() in _GENERIC_FAMILIES:
        return cleaned
    return f"'{escape(cleaned, quote=False)}'"


def _dedup_families(families: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in families:
        cleaned = _clean_family_name(raw)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def build_font_stacks(
    families: Sequence[str],
    source: Literal["default", "kitty", "env", "argument"],
) -> FontSpec:
    """Prepend ``families`` ahead of the built-in sans/mono fallback chains."""
    deduped = _dedup_families(families)
    if not deduped:
        return FontSpec(DEFAULT_SANS, DEFAULT_MONO, (), "default")
    tokens = [tok for fam in deduped if (tok := _format_family_token(fam))]
    if not tokens:
        return FontSpec(DEFAULT_SANS, DEFAULT_MONO, (), "default")
    prefix = ", ".join(tokens)
    return FontSpec(
        sans=f"{prefix}, {DEFAULT_SANS}",
        mono=f"{prefix}, {DEFAULT_MONO}",
        families=tuple(deduped),
        source=source,
    )


def find_kitty_conf() -> Path | None:
    """Locate the active ``kitty.conf`` path, or ``None`` if disabled/absent."""
    override = os.environ.get("WAVEVIEW_KITTY_CONF")
    if override is not None:
        val = override.strip()
        if val.lower() in _DISABLED_VALUES:
            return None
        try:
            path = Path(val).expanduser()
            return path if path.is_file() else None
        except (OSError, RuntimeError):
            return None

    candidates: list[Path] = []
    kitty_dir = os.environ.get("KITTY_CONFIG_DIRECTORY", "").strip()
    if kitty_dir:
        candidates.append(Path(kitty_dir).expanduser() / "kitty.conf")
    xdg_dir = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg_dir:
        candidates.append(Path(xdg_dir).expanduser() / "kitty" / "kitty.conf")
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        home = None
    if home is not None:
        candidates.append(home / ".config" / "kitty" / "kitty.conf")
        if sys.platform == "darwin":
            candidates.append(home / "Library" / "Preferences" / "kitty" / "kitty.conf")

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _overlaps_cjk(cp_spec: str) -> bool:
    for item in cp_spec.split(","):
        part = item.strip()
        if not part:
            continue
        m = _CP_ITEM_RE.match(part)
        if not m:
            continue
        lo = int(m.group(1), 16)
        hi = int(m.group(2), 16) if m.group(2) else lo
        if min(lo, hi) <= 0x9FFF and max(lo, hi) >= 0x4E00:
            return True
    return False


def _parse_font_family_value(val: str) -> str | None:
    if val.lower() == "auto":
        return None
    m = _CHOOSE_FAMILY_RE.search(val)
    if m:
        chosen = next((g for g in m.groups() if g is not None), "").strip()
        return chosen or None
    if _CHOOSE_KV_RE.search(val):
        m_full = _CHOOSE_FULL_NAME_RE.search(val)
        if m_full:
            chosen = next((g for g in m_full.groups() if g is not None), "").strip()
            return chosen or None
        return None
    cleaned = _strip_outer_quotes(val)
    if not cleaned or cleaned.lower() == "auto":
        return None
    return cleaned


def _logical_kitty_lines(text: str) -> list[str]:
    lines: list[str] = []
    buf: list[str] = []
    for raw in text.splitlines():
        s = raw.rstrip("\r\n")
        if not buf:
            stripped = s.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            chunk = stripped
        else:
            chunk = s.lstrip()
            prev = buf[-1]
            if prev and not prev[-1].isspace() and not prev.endswith(",") and s[:1].isspace():
                chunk = " " + chunk
        if chunk.endswith("\\"):
            buf.append(chunk[:-1])
            continue
        if buf:
            lines.append("".join(buf) + chunk)
            buf.clear()
        else:
            lines.append(chunk)
    if buf:
        lines.append("".join(buf))
    return lines


def _resolve_include_paths(base_dir: Path, target: str, is_glob: bool) -> list[Path]:
    expanded = os.path.expanduser(os.path.expandvars(_strip_outer_quotes(target)))
    if not expanded:
        return []
    raw_path = Path(expanded)
    pattern = raw_path if raw_path.is_absolute() else base_dir / raw_path
    if is_glob or any(ch in expanded for ch in "*?["):
        return [Path(p) for p in sorted(glob.glob(str(pattern)))]
    return [pattern]


def parse_kitty_fonts(conf_path: Path) -> tuple[str, ...]:
    """Extract primary ``font_family`` and CJK ``symbol_map`` families from ``kitty.conf``."""
    primary: str | None = None
    cjk_list: list[str] = []
    active: set[Path] = set()

    def _walk(path: Path, depth: int) -> None:
        nonlocal primary
        if depth > _MAX_INCLUDE_DEPTH:
            return
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in active:
            return
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        active.add(resolved)
        try:
            for line in _logical_kitty_lines(text):
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                directive, val = parts[0].lower(), parts[1].strip()
                if directive in ("include", "globinclude"):
                    for inc in _resolve_include_paths(resolved.parent, val, directive == "globinclude"):
                        _walk(inc, depth + 1)
                elif directive == "font_family":
                    if val.lower() == "auto":
                        primary = None
                    else:
                        parsed = _parse_font_family_value(val)
                        if parsed is not None:
                            primary = parsed
                elif directive == "symbol_map":
                    norm_val = re.sub(r",\s+(?=u\+)", ",", val, flags=re.IGNORECASE)
                    sm_parts = norm_val.split(None, 1)
                    if len(sm_parts) != 2:
                        continue
                    cp_spec, fam_raw = sm_parts
                    if _overlaps_cjk(cp_spec):
                        fam = _clean_family_name(fam_raw)
                        if fam and fam.lower() != "auto":
                            cjk_list[:] = [x for x in cjk_list if x.lower() != fam.lower()]
                            cjk_list.append(fam)
        finally:
            active.remove(resolved)

    _walk(conf_path, 0)
    combined: list[str] = []
    if primary:
        combined.append(primary)
    combined.extend(reversed(cjk_list))
    return tuple(_dedup_families(combined))


def _split_families(raw: str) -> tuple[str, ...]:
    return tuple(_dedup_families(raw.split(",")))


def resolve_fonts(font: str | None = None) -> FontSpec:
    """Resolve effective fonts via ``argument > WAVEVIEW_FONT > kitty.conf > default``."""
    if font is not None:
        if not isinstance(font, str):
            raise WaveError(f"font 必须是字符串，收到的是 {font!r}")
        stripped = font.strip()
        if not stripped:
            raise WaveError("font 必须是非空字符串")
        low = stripped.lower()
        if low == "default":
            return FontSpec(DEFAULT_SANS, DEFAULT_MONO, (), "default")
        if low == "kitty":
            conf = find_kitty_conf()
            if conf is not None:
                fams = parse_kitty_fonts(conf)
                if fams:
                    return build_font_stacks(fams, "kitty")
            return FontSpec(DEFAULT_SANS, DEFAULT_MONO, (), "default")
        fams = _split_families(stripped)
        if not fams:
            raise WaveError("font 必须是非空字符串")
        return build_font_stacks(fams, "argument")

    env_font = os.environ.get("WAVEVIEW_FONT")
    if env_font and env_font.strip():
        ef = env_font.strip()
        low = ef.lower()
        if low == "default":
            return FontSpec(DEFAULT_SANS, DEFAULT_MONO, (), "default")
        if low != "kitty":
            fams = _split_families(ef)
            if fams:
                return build_font_stacks(fams, "env")

    conf = find_kitty_conf()
    if conf is not None:
        fams = parse_kitty_fonts(conf)
        if fams:
            return build_font_stacks(fams, "kitty")

    return FontSpec(DEFAULT_SANS, DEFAULT_MONO, (), "default")

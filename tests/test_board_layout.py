"""Presentation contracts that keep the kiosk readable on small screens."""

import re
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "bin/session-board.html").read_text()


def _contrast(a: str, b: str) -> float:
    def luminance(value: str) -> float:
        rgb = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_main_grid_can_shrink_below_its_preferred_tile_width():
    assert re.search(r"main\s*\{[^}]*minmax\(min\(100%,\s*\d+px\),\s*1fr\)", HTML)


def test_muted_text_remains_readable_on_the_dark_panel():
    colors = dict(re.findall(r"--(bg|muted|slate):\s*(#[0-9a-f]{6})", HTML))
    assert _contrast(colors["muted"], colors["bg"]) >= 4.5
    assert _contrast(colors["slate"], colors["bg"]) >= 4.5


def test_reduced_motion_disables_all_decorative_animation():
    assert re.search(r"prefers-reduced-motion:\s*reduce\)[^}]*\*::before[^}]*animation-duration:", HTML)


def test_stale_and_idle_tiles_keep_text_opacity():
    assert not re.search(r"#grid\.stale\s*\{[^}]*opacity:", HTML)
    assert not re.search(r"\.tile\.IDLE\s*\{[^}]*opacity:", HTML)

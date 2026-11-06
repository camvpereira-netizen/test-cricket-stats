"""The theme's text colours meet WCAG 2.2 AA contrast (4.5:1 for normal text) on their backgrounds."""
import tomllib
from pathlib import Path

import pytest

from ui import theme

CONFIG = tomllib.loads((Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml").read_text())["theme"]


def luminance(hex_colour: str) -> float:
    """WCAG relative luminance."""
    rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted([luminance(a), luminance(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_formula():
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21)
    assert contrast("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)


@pytest.mark.parametrize("fg, bg", [
    (CONFIG["textColor"], CONFIG["backgroundColor"]),
    (CONFIG["textColor"], CONFIG["secondaryBackgroundColor"]),
    (CONFIG["primaryColor"], CONFIG["backgroundColor"]),      # links and red text
    ("#FFFFFF", CONFIG["primaryColor"]),                      # text on red buttons
    ("#FFFFFF", theme.OUTFIELD),                              # banner text, light stripe
    (theme.BOARD_DIGITS, theme.BOARD),                        # scoreboard numbers
    (theme.BOARD_LABEL, theme.BOARD),                         # scoreboard labels
    (theme.OUTFIELD_DARK, CONFIG["backgroundColor"]),         # subheadings
    (theme.OUTFIELD_DARK, "#E6F0E1"),                         # chips
])
def test_text_contrast(fg, bg):
    assert contrast(fg, bg) >= 4.5, (fg, bg, round(contrast(fg, bg), 2))


def test_theme_and_config_agree():
    assert CONFIG["primaryColor"] == theme.BALL
    assert CONFIG["backgroundColor"] == theme.WHITES
    assert CONFIG["textColor"] == theme.INK

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Fonts guaranteed present on Windows (C:\Windows\Fonts) - libass resolves
# these by family name directly, no fontfile path needed.
AVAILABLE_FONTS = ["Arial", "Verdana", "Tahoma", "Georgia", "Calibri", "Segoe UI", "Impact"]


@dataclass
class CaptionStyle:
    """Burned-in caption appearance. Baked into a real .ass Style line by
    captions.export.write_ass() (with PlayResX/PlayResY set to the actual
    video's resolution, so FontSize maps directly to real on-screen pixels -
    see write_ass()'s docstring for why that matters). Defaults follow the
    standard high-contrast "social captions" look (bold white text, thick
    black outline, no background box) chosen for legibility over arbitrary
    video content."""

    font_family: str = "Arial"
    font_size: int = 48
    bold: bool = True
    primary_color: str = "#FFFFFF"  # text fill
    outline_color: str = "#000000"
    outline_width: int = 3
    position: Literal["bottom", "top"] = "bottom"
    margin_v: int = 60  # px from the top/bottom edge - keeps clear of platform UI chrome


def ass_color(hex_color: str) -> str:
    """#RRGGBB -> ASS/libass &HAABBGGRR (alpha 00 = fully opaque)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
    return f"&H00{b}{g}{r}".upper()

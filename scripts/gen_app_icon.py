#!/usr/bin/env python
"""Generate the Phoenix Cognition app icons (Phase 13 Step 5c).

Draws the mark — a bold orange spark/triangle with a green status dot on a dark
rounded tile — and writes, under ``phoenix/ui/static/``:

- ``phoenix-cognition.ico``  (multi-resolution; for the Windows desktop shortcut)
- ``apple-touch-icon.png``   (180x180; crisp iOS home-screen icon for the PWA)
- ``icon-512.png``           (512x512; PWA manifest maskable icon)

Reproducible: run ``python scripts/gen_app_icon.py`` after editing the design.
Requires Pillow (``pip install pillow``).
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("error: Pillow not installed; `pip install pillow`.", file=sys.stderr)
    raise SystemExit(3) from None

_STATIC = Path(__file__).resolve().parent.parent / "phoenix" / "ui" / "static"

_BG = (11, 13, 18, 255)  # #0b0d12
_ORANGE = (255, 122, 69, 255)  # #ff7a45
_DARK = (14, 17, 23, 255)  # inner notch
_GREEN = (62, 207, 142, 255)  # #3ecf8e


def _render(size: int) -> Image.Image:
    """Render the mark at ``size`` px, supersampled 4x for clean edges."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Dark rounded tile.
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=_BG)

    # Bold orange triangle (the spark), apex up.
    apex = (s * 0.50, s * 0.17)
    left = (s * 0.20, s * 0.70)
    right = (s * 0.80, s * 0.70)
    d.polygon([apex, left, right], fill=_ORANGE)

    # Inner dark notch -> reads as a chevron, survives downscaling.
    d.polygon(
        [(s * 0.50, s * 0.40), (s * 0.355, s * 0.66), (s * 0.645, s * 0.66)],
        fill=_DARK,
    )

    # Green status dot at the base.
    r = s * 0.075
    cx, cy = s * 0.50, s * 0.815
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_GREEN)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    _STATIC.mkdir(parents=True, exist_ok=True)
    base = _render(256)

    ico = _STATIC / "phoenix-cognition.ico"
    base.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    _render(180).save(_STATIC / "apple-touch-icon.png")
    _render(512).save(_STATIC / "icon-512.png")

    print(f"wrote {ico}")
    print(f"wrote {_STATIC / 'apple-touch-icon.png'}")
    print(f"wrote {_STATIC / 'icon-512.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

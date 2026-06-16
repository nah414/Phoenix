#!/usr/bin/env python
"""Generate the Phoenix Cognition app icons (Phase 13 Step 5c).

Draws a flaming phoenix (the mythical rising bird) on a dark rounded tile and
writes, under ``phoenix/ui/static/``:

- ``phoenix-cognition.ico``  (multi-resolution; for the Windows desktop shortcut)
- ``apple-touch-icon.png``   (180x180; iOS home-screen icon for the PWA)
- ``icon-512.png``           (512x512; PWA manifest maskable icon)

The phoenix is drawn from bezier-sampled polygons (no SVG rasterizer needed) and
supersampled for clean edges. Reproducible: ``python scripts/gen_app_icon.py``.
Requires Pillow (``pip install pillow``).
"""

# Coordinate literals mix int + float; tuple-invariance makes a strict point
# type fight not worth it for a build-time drawing script.
# mypy: disable-error-code="arg-type, list-item"

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    print("error: Pillow not installed; `pip install pillow`.", file=sys.stderr)
    raise SystemExit(3) from None

_STATIC = Path(__file__).resolve().parent.parent / "phoenix" / "ui" / "static"

# Palette (dark tile + a fire ramp).
_BG = (11, 13, 18, 255)  # #0b0d12
_RED = (214, 58, 31, 255)  # deep ember
_ORANGE = (255, 122, 69, 255)  # #ff7a45
_GOLD = (255, 196, 77, 255)  # leading-edge highlight
_GLOW = (255, 120, 60, 90)  # soft halo (low alpha)
_EYE = (11, 13, 18, 255)

Pt = tuple[float, float]


def _cubic(p0: Pt, p1: Pt, p2: Pt, p3: Pt, n: int = 28) -> list[Pt]:
    """Sample a cubic bezier into ``n+1`` points."""
    out: list[Pt] = []
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        x = u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0]
        y = u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]
        out.append((x, y))
    return out


def _mirror(pts: list[Pt], axis: float) -> list[Pt]:
    """Reflect points across the vertical line ``x = axis``."""
    return [(2 * axis - x, y) for (x, y) in pts]


def _wing(cx: float) -> list[Pt]:
    """Right-side phoenix wing (a swept flame wing with feather tongues)."""
    root_top = (cx + 18, 470)
    tip = (cx + 372, 250)
    # Outer leading edge: graceful upward sweep to the wingtip.
    path = _cubic(root_top, (cx + 96, 330), (cx + 252, 232), tip)
    # Trailing edge back to the body, broken into flame "feathers".
    feathers: list[Pt] = [
        (cx + 320, 372),
        (cx + 300, 300),  # feather 1 tip, notch
        (cx + 232, 432),
        (cx + 208, 360),  # feather 2
        (cx + 130, 470),
        (cx + 108, 408),  # feather 3
        (cx + 44, 520),  # back to body root
    ]
    return path + feathers


def _tail_plume(cx: float, dx: float, length: float, width: float, base_y: float = 612) -> list[Pt]:
    """A single downward flame plume of the tail."""
    base_l = (cx + dx - width, base_y)
    base_r = (cx + dx + width, base_y)
    tip = (cx + dx * 1.4, base_y + length)
    right = _cubic(base_r, (cx + dx + width, base_y + length * 0.55), tip, tip, 18)
    left = _cubic(tip, tip, (cx + dx - width, base_y + length * 0.55), base_l, 18)
    return [base_l, *right, *left]


def _phoenix_shapes(cx: float) -> list[tuple[list[Pt], tuple[int, int, int, int]]]:
    """Ordered (polygon, fill) pairs for the phoenix, back to front."""
    body = [
        *_cubic((cx - 56, 470), (cx - 70, 560), (cx - 40, 612), (cx, 632)),
        *_cubic((cx, 632), (cx + 40, 612), (cx + 70, 560), (cx + 56, 470)),
        *_cubic((cx + 56, 470), (cx + 40, 392), (cx + 36, 372), (cx + 26, 356)),
        *_cubic((cx - 26, 356), (cx - 36, 372), (cx - 40, 392), (cx - 56, 470)),
    ]
    head = [
        *_cubic((cx - 38, 356), (cx - 44, 300), (cx - 26, 268), (cx, 262)),
        *_cubic((cx, 262), (cx + 26, 268), (cx + 44, 300), (cx + 38, 356)),
    ]
    beak = [(cx + 30, 300), (cx + 96, 286), (cx + 34, 332)]
    crest_r = [(cx + 6, 268), (cx + 40, 188), (cx + 24, 258)]
    crest_c = [(cx - 12, 264), (cx, 156), (cx + 14, 262)]
    wing_r = _wing(cx)
    lead_r = _cubic((cx + 18, 470), (cx + 96, 330), (cx + 252, 232), (cx + 372, 250))
    lead_r += [(cx + 330, 268), (cx + 232, 270), (cx + 96, 372), (cx + 30, 466)]

    return [
        # tail plumes (orange) then red tips for a fire gradient
        (_tail_plume(cx, -86, 196, 40), _ORANGE),
        (_tail_plume(cx, 86, 196, 40), _ORANGE),
        (_tail_plume(cx, 0, 268, 52), _ORANGE),
        (_tail_plume(cx, -86, 90, 26, base_y=718), _RED),
        (_tail_plume(cx, 86, 90, 26, base_y=718), _RED),
        (_tail_plume(cx, 0, 124, 34, base_y=756), _RED),
        (body, _ORANGE),
        (_mirror(wing_r, cx), _ORANGE),
        (wing_r, _ORANGE),
        (head, _ORANGE),
        # gold leading edges + crest + beak
        (_mirror(lead_r, cx), _GOLD),
        (lead_r, _GOLD),
        (_mirror(crest_r, cx), _GOLD),
        (crest_c, _GOLD),
        (crest_r, _GOLD),
        (beak, _GOLD),
    ]


def _eye_box(cx: float) -> list[float]:
    return [cx + 6, 308, cx + 26, 328]


def _render(size: int) -> Image.Image:
    """Render the icon at ``size`` px, supersampled 4x."""
    scale = size * 4 / 1000.0
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=_BG)

    # Soft halo behind the bird.
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([s * 0.16, s * 0.16, s * 0.84, s * 0.86], fill=_GLOW)
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.05))
    img.alpha_composite(glow)

    # Draw the phoenix in a 1000x1000 logical space scaled onto the tile.
    art = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ad = ImageDraw.Draw(art)
    for pts, fill in _phoenix_shapes(500.0):
        ad.polygon([(x * scale, y * scale) for (x, y) in pts], fill=fill)
    ad.ellipse([v * scale for v in _eye_box(500.0)], fill=_EYE)
    img.alpha_composite(art)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    _STATIC.mkdir(parents=True, exist_ok=True)
    base = _render(256)
    ico = _STATIC / "phoenix-cognition.ico"
    base.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    _render(180).save(_STATIC / "apple-touch-icon.png")
    _render(512).save(_STATIC / "icon-512.png")
    print(f"wrote {ico}, apple-touch-icon.png, icon-512.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

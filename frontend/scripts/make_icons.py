"""Draw the Nivas icon (an original mark: a house roof over a bed) as SVG and as PNGs.

The SVG and the PNGs come from the SAME shapes on a 64-unit grid, so they always match. Pillow only;
no SVG renderer needed. Output: favicon.svg, apple-touch-icon.png (180), icon-192.png, icon-512.png.

From the project root in Git Bash (the tools container has Pillow, so nothing new is installed):

    docker compose run --rm -T --no-deps -v "$(pwd -W)/frontend:/web-src" tools \
        python /web-src/scripts/make_icons.py /web-src/public
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def oklch_to_srgb(l: float, c: float, h: float) -> tuple[int, int, int]:
    """The brand colour is defined in OKLCH (src/index.css --primary); convert it for the PNGs."""
    a, b = c * math.cos(math.radians(h)), c * math.sin(math.radians(h))
    l_ = l + 0.3963377774 * a + 0.2158037573 * b
    m_ = l - 0.1055613458 * a - 0.0638541728 * b
    s_ = l - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = l_**3, m_**3, s_**3
    rgb = (
        4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    )

    def gamma(x: float) -> int:
        x = min(max(x, 0.0), 1.0)
        x = 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
        return round(x * 255)

    return gamma(rgb[0]), gamma(rgb[1]), gamma(rgb[2])


BRAND = oklch_to_srgb(0.49, 0.2, 264)  # --primary, light mode
HEX = "#%02x%02x%02x" % BRAND
WHITE = (255, 255, 255)

# The mark on a 64 x 64 grid.
ROOF = [(12, 31), (32, 13), (52, 31)]  # a roof line, drawn as a thick stroke
ROOF_W = 5
HEADBOARD = (15, 34, 21, 52)  # x0, y0, x1, y1 (clear of the roof line)
MATTRESS = (15, 40, 49, 47)
FOOT = (44, 40, 49, 52)
PILLOW = (23, 35, 32, 40)


def svg() -> str:
    def rect(r: tuple[int, int, int, int], rx: float = 1.5) -> str:
        x0, y0, x1, y1 = r
        return f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{rx}" fill="#fff"/>'

    pts = " ".join(f"{x},{y}" for x, y in ROOF)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
        f"  <title>Nivas</title>\n"
        f'  <rect width="64" height="64" rx="14" fill="{HEX}"/>\n'
        f'  <polyline points="{pts}" fill="none" stroke="#fff" stroke-width="{ROOF_W}" stroke-linecap="round" stroke-linejoin="round"/>\n'
        f"  {rect(HEADBOARD)}\n  {rect(MATTRESS)}\n  {rect(FOOT)}\n  {rect(PILLOW, 2)}\n"
        "</svg>\n"
    )


def png(size: int, path: Path, *, rounded: bool) -> None:
    """Drawn at 4x and scaled down, for smooth edges. The touch icon is square: iOS rounds it itself."""
    big = size * 4
    k = big / 64
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(14 * k), fill=BRAND)
    else:
        d.rectangle((0, 0, big - 1, big - 1), fill=BRAND)
    roof = [(x * k, y * k) for x, y in ROOF]
    d.line(roof, fill=WHITE, width=round(ROOF_W * k), joint="curve")
    for x, y in (roof[0], roof[-1]):  # round line caps
        r = ROOF_W * k / 2
        d.ellipse((x - r, y - r, x + r, y + r), fill=WHITE)
    for box, rad in ((HEADBOARD, 1.5), (MATTRESS, 1.5), (FOOT, 1.5), (PILLOW, 2)):
        d.rounded_rectangle(tuple(v * k for v in box), radius=rad * k, fill=WHITE)
    img.resize((size, size), Image.LANCZOS).save(path, optimize=True)


out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
(out / "favicon.svg").write_text(svg(), encoding="utf-8")
png(180, out / "apple-touch-icon.png", rounded=False)
png(192, out / "icon-192.png", rounded=True)
png(512, out / "icon-512.png", rounded=True)
print("brand colour", HEX, "->", sorted(p.name for p in out.iterdir()))

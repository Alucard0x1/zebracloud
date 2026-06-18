#!/usr/bin/env python3
"""
Generate a vertical (portrait) badge image at a precise physical size.

Layout (top -> bottom), matching the project's ZPL badge in server.py:
  - Header band : 57.0 mm  -> solid colour (pre-printed zone)
  - Print area  : 95.4 mm  -> centred content, must NOT overflow this band
  - Remainder   : blank background down to the full image height

Print-area content (each on its own line, horizontally centred):
  1. Person name   (dummy)            - largest, bold
  2. Company name  (dummy)
  3. Category      (dummy, uppercase) - bold
  4. QR code       (real, dummy data) - centred square
  5. Eventcat      (dummy)            - from API field "eventcat"

Fitting guarantees: every line is shrunk to fit the usable width, and the
whole stack is scaled down if it would exceed the 95.4 mm print area, so
nothing overflows vertically or horizontally.

Defaults reproduce the requested image. Override via flags, e.g.:
    python make_label_image.py --name "Jane Smith" --no-guides -o badge.png
"""

from __future__ import annotations

import argparse
import os

from PIL import Image, ImageDraw, ImageFont
import qrcode

# ----------------------------------------------------------------------
# Geometry (mm). Edit here or override on the command line.
# ----------------------------------------------------------------------
WIDTH_MM = 101.6      # short side  -> horizontal (vertical output)
HEIGHT_MM = 304.88    # long side   -> vertical
HEADER_MM = 57.0      # coloured header band at the top
PRINT_AREA_MM = 95.4  # reserved content zone directly below the header
DPI = 203             # matches the Zebra ZD230/ZD421-203dpi printers

# Inner margins of the print area (mm)
SIDE_MARGIN_MM = 4.0
TOP_MARGIN_MM = 3.0
BOTTOM_MARGIN_MM = 3.0

# Colours
BAND_COLOR = "#00A651"   # green header band
BG_COLOR = "white"
TEXT_COLOR = "#111111"
GUIDE_COLOR = "#CCCCCC"   # thin outline showing the reserved print area

OUTPUT = "label.png"

# Dummy content. "eventcat" normally comes from the API field of the same name.
CONTENT = {
    "name": "John Doe",
    "company": "Acme Corporation",
    "category": "VISITOR",
    "qr_data": "JOHN-DOE-0001",
    "eventcat": "TECH SUMMIT 2026",
}

MM_PER_INCH = 25.4
WIN_FONTS = r"C:\Windows\Fonts"


def mm_to_px(mm: float, dpi: int) -> int:
    """Convert millimetres to whole pixels at the given DPI."""
    return round(mm / MM_PER_INCH * dpi)


# ----------------------------------------------------------------------
# Fonts
# ----------------------------------------------------------------------
def load_font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    """Load Arial (falling back to DejaVu / PIL default) at the given size."""
    size = max(6, int(size))
    names = (["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["arial.ttf", "DejaVuSans.ttf"])
    for name in names:
        for path in (os.path.join(WIN_FONTS, name), name):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int, int, int]:
    """Return (width, height, x0, y0) of the text's tight bounding box."""
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font, anchor="la")
    return x1 - x0, y1 - y0, x0, y0


def fit_font_to_width(draw, text, bold, size, max_w, min_size=8):
    """Largest font <= `size` whose rendered width fits `max_w`."""
    size = max(min_size, int(size))
    while size > min_size:
        font = load_font(bold, size)
        w, _, _, _ = measure(draw, text, font)
        if w <= max_w:
            return font
        size -= 2
    return load_font(bold, min_size)


# ----------------------------------------------------------------------
# QR code
# ----------------------------------------------------------------------
def make_qr(data: str, size_px: int) -> Image.Image:
    """Render `data` as a square QR image of `size_px`, black on white."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=2,  # quiet zone for reliable scanning
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size_px, size_px), Image.NEAREST)


# ----------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------
def build_plan(draw, content, scale, usable_w):
    """Build the render plan at a given scale; return (plan, total_height).

    Each spec row: (kind, data, bold, base_size_px, gap_before_px).
    """
    spec = [
        ("text", content["name"],     True,  110, 0),
        ("text", content["company"],  False, 56,  16),
        ("text", content["category"], True,  40,  16),
        ("qr",   content["qr_data"],  False, 300, 28),
        ("text", content["eventcat"], False, 40,  22),
    ]
    plan, total = [], 0.0
    for kind, data, bold, base, gap in spec:
        g = gap * scale
        if kind == "text":
            font = fit_font_to_width(draw, data, bold, base * scale, usable_w)
            w, h, x0, y0 = measure(draw, data, font)
            plan.append(("text", data, font, w, h, x0, y0, g))
            total += g + h
        else:
            qs = max(48, int(min(base * scale, usable_w)))
            plan.append(("qr", data, None, qs, qs, 0, 0, g))
            total += g + qs
    return plan, total


def create_label(width_mm, height_mm, header_mm, print_area_mm, dpi,
                 band_color, bg_color, text_color, content, guides, out_path):
    width_px = mm_to_px(width_mm, dpi)
    height_px = mm_to_px(height_mm, dpi)
    header_px = mm_to_px(header_mm, dpi)
    print_px = mm_to_px(print_area_mm, dpi)

    side_m = mm_to_px(SIDE_MARGIN_MM, dpi)
    top_m = mm_to_px(TOP_MARGIN_MM, dpi)
    bot_m = mm_to_px(BOTTOM_MARGIN_MM, dpi)

    zone_top = header_px
    usable_w = width_px - 2 * side_m
    usable_h = print_px - top_m - bot_m

    img = Image.new("RGB", (width_px, height_px), bg_color)
    draw = ImageDraw.Draw(img)

    # Header band across the top.
    if header_px > 0:
        draw.rectangle([0, 0, width_px - 1, header_px - 1], fill=band_color)

    # Optional thin outline showing the reserved print area.
    if guides:
        draw.rectangle([0, zone_top, width_px - 1, zone_top + print_px - 1],
                       outline=GUIDE_COLOR, width=2)

    # Fit content: keep scale 1.0 if it fits, else binary-search down.
    plan, total = build_plan(draw, content, 1.0, usable_w)
    scale = 1.0
    if total > usable_h:
        lo, hi = 0.1, 1.0
        for _ in range(22):
            mid = (lo + hi) / 2
            p, t = build_plan(draw, content, mid, usable_w)
            if t <= usable_h:
                plan, total, scale = p, t, mid
                lo = mid
            else:
                hi = mid

    # Vertically centre the block inside the print area.
    cx = width_px / 2
    y = zone_top + top_m + max(0.0, (usable_h - total) / 2)

    for item in plan:
        kind = item[0]
        gap = item[-1]
        y += gap
        if kind == "text":
            _, data, font, w, h, x0, y0, _ = item
            draw.text((cx - w / 2 - x0, y - y0), data,
                      font=font, fill=text_color, anchor="la")
            y += h
        else:  # qr
            _, data, _, qs, _, _, _, _ = item
            qr_img = make_qr(data, qs)
            img.paste(qr_img, (int(cx - qs / 2), int(round(y))))
            y += qs

    img.save(out_path, dpi=(dpi, dpi))

    # Report.
    print(f"Saved: {out_path}")
    print(f"  Canvas      : {width_px} x {height_px} px @ {dpi} dpi "
          f"({width_px / dpi * MM_PER_INCH:.1f} x {height_px / dpi * MM_PER_INCH:.1f} mm)")
    print(f"  Header band : {header_px} px = "
          f"{header_px / dpi * MM_PER_INCH:.1f} mm  fill={band_color}")
    print(f"  Print area  : y {zone_top}..{zone_top + print_px} "
          f"({print_px} px = {print_px / dpi * MM_PER_INCH:.1f} mm)")
    print(f"  Content fit : scale={scale:.2f}, stack={total:.0f} px, "
          f"usable={usable_h} px (overflow={'NO' if total <= usable_h else 'YES'})")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--width-mm", type=float, default=WIDTH_MM)
    p.add_argument("--height-mm", type=float, default=HEIGHT_MM)
    p.add_argument("--header-mm", type=float, default=HEADER_MM)
    p.add_argument("--print-area-mm", type=float, default=PRINT_AREA_MM)
    p.add_argument("--dpi", type=int, default=DPI)
    p.add_argument("--band-color", default=BAND_COLOR)
    p.add_argument("--bg-color", default=BG_COLOR)
    p.add_argument("--text-color", default=TEXT_COLOR)
    p.add_argument("--name", default=CONTENT["name"])
    p.add_argument("--company", default=CONTENT["company"])
    p.add_argument("--category", default=CONTENT["category"])
    p.add_argument("--qr-data", default=CONTENT["qr_data"])
    p.add_argument("--eventcat", default=CONTENT["eventcat"])
    p.add_argument("--no-guides", action="store_true",
                   help="hide the thin print-area outline")
    p.add_argument("-o", "--output", default=OUTPUT)
    return p.parse_args()


def main() -> None:
    a = parse_args()
    content = {
        "name": a.name,
        "company": a.company,
        "category": a.category,
        "qr_data": a.qr_data,
        "eventcat": a.eventcat,
    }
    create_label(
        width_mm=a.width_mm, height_mm=a.height_mm, header_mm=a.header_mm,
        print_area_mm=a.print_area_mm, dpi=a.dpi, band_color=a.band_color,
        bg_color=a.bg_color, text_color=a.text_color, content=content,
        guides=not a.no_guides, out_path=a.output,
    )


if __name__ == "__main__":
    main()

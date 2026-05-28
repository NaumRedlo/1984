"""Download official osu! mod badges from ppy/osu-web and convert to PNG.

Source: https://github.com/ppy/osu-web/tree/master/public/images/badges/mods

The SVGs are white-strokes-on-transparent glyphs designed to sit on top of
a colored disc. We render at 128×128 — caller resizes per use site.

Output: assets/icons/mods/<ACRONYM>.png (e.g. HD.png).

Run once:
    /tmp/fontmerge/bin/python3 -m scripts.download_mod_icons
"""

from __future__ import annotations

import os
import re
import sys
import urllib.request
from io import BytesIO

import cairosvg
from PIL import Image


BASE_URL = (
    "https://raw.githubusercontent.com/ppy/osu-web/master/"
    "public/images/badges/mods/"
)
OUT_DIR = "/home/naumredlo/1984/assets/icons/mods"
OUT_SIZE = 128


# Acronym → osu-web filename stem (without `mod-` prefix or `.svg`).
# Standard mods first, then lazer extensions.
MODS: dict[str, str] = {
    # ── Classic difficulty reduction ─────────────────────────────────
    "EZ":  "easy",
    "NF":  "no-fail",
    "HT":  "half-time",
    "DC":  "daycore",
    # ── Classic difficulty increase ──────────────────────────────────
    "HR":  "hard-rock",
    "SD":  "sudden-death",
    "PF":  "perfect",
    "DT":  "double-time",
    "NC":  "nightcore",
    "HD":  "hidden",
    "FL":  "flashlight",
    # ── Classic special ──────────────────────────────────────────────
    "RX":  "relax",
    "AP":  "autopilot",
    "SO":  "spun-out",
    "AT":  "autoplay",
    "CN":  "cinema",
    "TD":  "touch-device",
    "NM":  "no-mod",
    "SV2": "score-v2",
    # ── Lazer extras you might see on a score ────────────────────────
    "CL":  "classic",
    "MR":  "mirror",
    "BL":  "blinds",
    "TP":  "target-practice",
    "AL":  "alternate",
    "SG":  "single-tap",
    "BR":  "barrel-roll",
    "AD":  "approach-different",
    "TC":  "traceable",
    "DA":  "difficulty-adjust",
    "AS":  "adaptive-speed",
    "MU":  "muted",
    "WG":  "wiggle",
    "TR":  "transform",
    "WU":  "wind-up",
    "WD":  "wind-down",
    "GR":  "grow",
    "DF":  "deflate",
    "SI":  "spin-in",
    "FI":  "fade-in",
    "ST":  "strict-tracking",
    "NS":  "no-scope",
    "MG":  "magnetised",
    "RP":  "repel",
    "CO":  "cover",
    "BU":  "bubbles",
    "BM":  "bloom",
    "FR":  "freeze-frame",
    "DP":  "depth",
    "SY":  "synesthesia",
    "RD":  "random",
}


def _fetch_svg(name: str) -> bytes:
    url = f"{BASE_URL}mod-{name}.svg"
    req = urllib.request.Request(
        url, headers={"User-Agent": "project1984-icon-downloader/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp.read()


_VIEWBOX_RX = re.compile(
    rb'viewBox=["\']\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*["\']'
)


def _svg_viewbox(svg: bytes) -> tuple[float, float]:
    """Return (width, height) from the SVG's viewBox. Falls back to (1, 1)
    if absent, which will produce a square render — not great, but never
    crashes."""
    m = _VIEWBOX_RX.search(svg)
    if not m:
        return 1.0, 1.0
    _x, _y, w, h = (float(g) for g in m.groups())
    return max(w, 1.0), max(h, 1.0)


def _render_centered(svg: bytes, output_size: int) -> Image.Image:
    """Render SVG preserving aspect ratio, centred on a transparent square.

    Without this, non-square viewBoxes (e.g. mod-hidden.svg is 120×84) get
    stretched into a square — distorts the glyph and shifts it off centre.
    """
    vw, vh = _svg_viewbox(svg)
    scale = output_size / max(vw, vh)
    rw = max(1, int(round(vw * scale)))
    rh = max(1, int(round(vh * scale)))
    png_bytes = cairosvg.svg2png(
        bytestring=svg, output_width=rw, output_height=rh,
    )
    glyph = Image.open(BytesIO(png_bytes)).convert("RGBA")
    canvas = Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
    canvas.paste(glyph, ((output_size - rw) // 2, (output_size - rh) // 2), glyph)
    return canvas


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    failed: list[tuple[str, str]] = []
    written = 0

    for acronym, stem in MODS.items():
        out_path = os.path.join(OUT_DIR, f"{acronym}.png")
        try:
            svg = _fetch_svg(stem)
            glyph = _render_centered(svg, OUT_SIZE)
            glyph.save(out_path)
            written += 1
            print(f"  ✔ {acronym:4s} ← mod-{stem}.svg")
        except Exception as e:
            failed.append((acronym, stem))
            print(f"  ✘ {acronym:4s} ({stem}): {e}", file=sys.stderr)

    print()
    print(f"Wrote {written}/{len(MODS)} mod icons to {OUT_DIR}/")
    if failed:
        print(f"Failed: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
Theme constants, layout parameters, font/asset paths for 1984 card generators.
"""

import os

# Theme colours
BG_COLOR = (20, 20, 30)
HEADER_BG = (35, 35, 50)
ROW_EVEN = (25, 25, 38)
ROW_ODD = (30, 30, 44)
TEXT_PRIMARY = (220, 220, 230)
TEXT_SECONDARY = (140, 140, 160)
ACCENT_RED = (200, 50, 50)
ACCENT_GREEN = (80, 200, 80)
SECTION_BG = (28, 28, 42)
PANEL_BG = (30, 30, 48)

# Recent-score card accents — the bot's red 1984 palette (services/image/render/recent.py).
RECENT_ACCENT = ACCENT_RED         # headings, grade ring, outlines
RECENT_LINE = (232, 96, 96)        # performance line / highlight values (brighter red)
RECENT_TRACK = (54, 36, 42)        # progress-bar / ring track (dark red-tinted)
RECENT_PILL = (80, 40, 46)         # current-pp pill fill
RECENT_BG = (14, 12, 16)           # card background (near-black, faintly warm)
RECENT_PANEL = (28, 24, 30)        # inner panels

TOP_COLORS = {
    1: (255, 215, 0),
    2: (192, 192, 210),
    3: (205, 150, 80),
}

GRADE_COLORS = {
    "XH": (220, 220, 240),
    "X": (255, 215, 0),
    "SH": (220, 220, 240),
    "S": (255, 215, 0),
    "A": (80, 200, 80),
    "B": (80, 140, 220),
    "C": (200, 150, 50),
    "D": (200, 50, 50),
    "F": (100, 100, 100),
}

MOD_COLORS = {
    "HR": (200, 50, 50),
    "DT": (160, 80, 200),
    "NC": (160, 80, 200),
    "HD": (200, 140, 50),
    "SD": (140, 100, 60),
    "PF": (140, 100, 60),
    "NF": (60, 120, 200),
    "EZ": (100, 200, 180),
    "FL": (220, 200, 60),
    "HT": (100, 160, 100),
    "SO": (180, 180, 180),
    "CL": (180, 180, 200),
    "TD": (120, 180, 170),
}

# Every mod acronym we ship a glyph for (assets/icons/mods/<ACRONYM>.png).
# Used to greedily split a concatenated mod string like "HDDT" -> ["HD","DT"].
# All are two letters except SV2, so longest-match-first handles it.
MOD_ACRONYMS = frozenset({
    "AD", "AL", "AP", "AS", "AT", "BL", "BM", "BR", "BU", "CL", "CN", "CO",
    "DA", "DC", "DF", "DP", "DT", "EZ", "FI", "FL", "FR", "GR", "HD", "HR",
    "HT", "MG", "MR", "MU", "NC", "NF", "NM", "NS", "PF", "RD", "RP", "RX",
    "SD", "SG", "SI", "SO", "ST", "SV2", "SY", "TC", "TD", "TP", "TR", "WD",
    "WG", "WU",
})

MONTH_NAMES = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# Layout constants
CARD_WIDTH = 800
HEADER_HEIGHT = 36
ROW_HEIGHT = 60
FOOTER_HEIGHT = 30
PADDING_X = 30
VALUE_RIGHT_X = CARD_WIDTH - PADDING_X

# Font / asset paths
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets")
FONT_DIR = os.path.join(ASSETS_DIR, "fonts")

TORUS_BOLD = os.path.join(FONT_DIR, "TorusNotched-Bold.ttf")
TORUS_SEMI = os.path.join(FONT_DIR, "TorusNotched-SemiBold.ttf")
TORUS_REG = os.path.join(FONT_DIR, "TorusNotched-Regular.ttf")
# Huninn was a Cyrillic-capable font; we no longer ship the file (the
# MPLUSRounded1c fallback below covers the same scripts and more).
# `_find_font(HUNINN)` returns None on a deployment without the file,
# so the BaseCardRenderer init code below falls through to the regular
# Torus fonts; the constant is kept for the legacy import path.
HUNINN = os.path.join(FONT_DIR, "Huninn-Regular.ttf")

# CJK / extended-script fallback. TorusNotched covers Latin only (362
# glyphs); for user-supplied content with Cyrillic / Hiragana / Katakana
# / CJK / Greek / symbols, render-helpers in `services.image.text_render`
# fall through to these. M PLUS Rounded 1c ships 8201 glyphs across the
# scripts that actually appear in osu! map/player data. The visual style
# (rounded, friendly) blends with TorusNotched well enough that mixed-
# script strings don't look obviously stitched together.
MPLUS_BOLD = os.path.join(FONT_DIR, "MPLUSRounded1c-Bold.ttf")
MPLUS_REG  = os.path.join(FONT_DIR, "MPLUSRounded1c-Regular.ttf")

# Dedicated Cyrillic fallback (2026-07-02): ProximaSoft has full Cyrillic +
# ASCII coverage in one family and matches the brand's visual style better
# than MPLUS Rounded, which stays the fallback for scripts Proxima doesn't
# cover (CJK, Greek, etc). See services/image/text_render.py's cyrillic_fallback.
PROXIMA_BOLD = os.path.join(FONT_DIR, "ProximaSoft-Bold.ttf")
PROXIMA_SEMI = os.path.join(FONT_DIR, "ProximaSoft-SemiBold.ttf")
PROXIMA_REG  = os.path.join(FONT_DIR, "ProximaSoft-Regular.ttf")

FLAGS_DIR = os.path.join(ASSETS_DIR, "flags")
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")


FALLBACK_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

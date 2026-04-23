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
}

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
HUNINN = os.path.join(FONT_DIR, "Huninn-Regular.ttf")

FLAGS_DIR = os.path.join(ASSETS_DIR, "flags")
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")

FALLBACK_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

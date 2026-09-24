import os

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

RECENT_ACCENT = ACCENT_RED
RECENT_LINE = (232, 96, 96)
RECENT_TRACK = (54, 36, 42)
RECENT_PILL = (80, 40, 46)
RECENT_BG = (14, 12, 16)
RECENT_PANEL = (28, 24, 30)

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

MOD_TYPE_COLORS = {
    "reduction": (178, 255, 102),
    "increase": (255, 102, 102),
    "conversion": (140, 102, 255),
    "automation": (102, 204, 255),
    "fun": (255, 102, 171),
    "system": (255, 204, 34),
}

MOD_TYPES = {
    "reduction": ("DC", "EZ", "HT", "NF"),
    "increase": ("AC", "BL", "DT", "FI", "FL", "HD", "HR", "NC", "PF", "SD", "ST", "TC"),
    "conversion": ("AL", "CL", "CO", "DA", "MR", "RD", "SG", "TP"),
    "automation": ("AP", "AT", "CN", "RX", "SO"),
    "fun": ("AD", "AS", "BM", "BR", "BU", "DF", "DP", "FR", "GR", "MG", "MU", "NS",
            "RP", "SI", "SY", "TR", "WD", "WG", "WU"),
    "system": ("SV2", "TD"),
}

MOD_COLORS = {
    acronym: MOD_TYPE_COLORS[kind]
    for kind, acronyms in MOD_TYPES.items()
    for acronym in acronyms
}

MOD_INK = (34, 34, 34)

MAPPER_RING = (235, 64, 64)

STATUS_COLORS = {
    "ranked": (179, 255, 102),
    "approved": (179, 255, 102),
    "qualified": (102, 204, 255),
    "loved": (255, 102, 171),
    "pending": (255, 217, 102),
    "wip": (255, 153, 102),
    "graveyard": (0, 0, 0),
}
STATUS_INK = (70, 57, 63)
GRAVEYARD_INK = (112, 92, 101)
STATUS_DEFAULT = (110, 110, 130)
_STATUS_BY_NUMBER = {4: "loved", 3: "qualified", 2: "approved", 1: "ranked",
                     0: "pending", -1: "wip", -2: "graveyard"}

def status_name(status) -> str:
    if isinstance(status, int):
        return _STATUS_BY_NUMBER.get(status, "")
    return str(status or "").strip().lower()

def status_colours(status) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    name = status_name(status)
    if name not in STATUS_COLORS:
        return STATUS_DEFAULT, (255, 255, 255)
    return STATUS_COLORS[name], GRAVEYARD_INK if name == "graveyard" else STATUS_INK

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

CARD_WIDTH = 800
HEADER_HEIGHT = 36
ROW_HEIGHT = 60
FOOTER_HEIGHT = 30
PADDING_X = 30
VALUE_RIGHT_X = CARD_WIDTH - PADDING_X

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets")
FONT_DIR = os.path.join(ASSETS_DIR, "fonts")

TORUS_BOLD = os.path.join(FONT_DIR, "TorusNotched-Bold.ttf")
TORUS_SEMI = os.path.join(FONT_DIR, "TorusNotched-SemiBold.ttf")
TORUS_REG = os.path.join(FONT_DIR, "TorusNotched-Regular.ttf")

HUNINN = os.path.join(FONT_DIR, "Huninn-Regular.ttf")

MPLUS_BOLD = os.path.join(FONT_DIR, "MPLUSRounded1c-Bold.ttf")
MPLUS_REG  = os.path.join(FONT_DIR, "MPLUSRounded1c-Regular.ttf")

PROXIMA_BOLD = os.path.join(FONT_DIR, "ProximaSoft-Bold.ttf")
PROXIMA_SEMI = os.path.join(FONT_DIR, "ProximaSoft-SemiBold.ttf")
PROXIMA_REG  = os.path.join(FONT_DIR, "ProximaSoft-Regular.ttf")

FLAGS_DIR = os.path.join(ASSETS_DIR, "flags")
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")

FALLBACK_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

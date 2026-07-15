"""Shared design-system palette (2026-07-08, re-based on the profile card
2026-07-08b).

This is the bot's actual shipped "red 1984" palette, taken verbatim from
services/image/render/profile.py's `COL_*` constants (the profile dashboard
was already the most mature, proven card) — every other card renderer is
meant to migrate onto THESE names one at a time, not the other way around.
profile.py itself now imports from here rather than defining its own copies,
so this module is the single source of truth, not just a snapshot.

Layer hierarchy: BG (whole canvas) < CARD (the outer card fill) < PANEL
(nested sub-panels inside the card). Borders pair with the fill one level up
(CARD_BORDER rings CARD, PANEL_BORDER rings PANEL).
"""

# ── Base layers ──────────────────────────────────────────────────────────
BG = (14, 12, 16)
CARD = (23, 19, 24)
CARD_BORDER = (74, 52, 56)
PANEL = (30, 24, 30)
PANEL_BORDER = (64, 46, 50)

# ── Text ─────────────────────────────────────────────────────────────────
TEXT_PRIMARY = (236, 234, 238)
TEXT_MUTED = (156, 144, 150)

# ── Accent (coral — the bot's primary color) ────────────────────────────
ACCENT = (226, 72, 72)       # section titles / accents
ACCENT_PP = (240, 104, 104)  # pp values, country rank — a lighter variant

# ── Misc semantic colors ────────────────────────────────────────────────
POSITIVE = (122, 222, 142)   # green — positive deltas, FC/pass indicators
TRACK = (62, 48, 52)         # progress-bar / ring track background
DIVIDER = (68, 50, 54)       # hairline separators
HEART = (255, 110, 178)      # osu!supporter pink heart

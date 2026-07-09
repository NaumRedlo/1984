"""Shared design-system palette (2026-07-08).

The canonical color set for new/redesigned cards, meant to eventually
replace each renderer's own ad-hoc constants (services/image/constants.py's
RECENT_*/COL_* etc., services/image/render/*.py's local COL_*/_PANEL/_WHITE
groups) — migrated one card at a time, not all at once.

Layer hierarchy: card background < panel background < panel border.
Text hierarchy: primary > secondary > muted > faint (e.g. "BPM"-style labels).
Accent is the bot's coral brand color; ACCENT_PP is a lighter variant used
specifically for pp numbers so they read as a distinct, brighter value.
"""

# ── Base layers ──────────────────────────────────────────────────────────
BG_CARD = (16, 14, 21)         # #100e15 — card background
BG_PANEL = (27, 25, 34)        # #1b1922 — panel background
BORDER_PANEL = (42, 39, 50)    # #2a2732 — panel border

# ── Text ─────────────────────────────────────────────────────────────────
TEXT_PRIMARY = (239, 237, 243)     # #efedf3
TEXT_SECONDARY = (183, 179, 194)   # #b7b3c2
TEXT_MUTED = (115, 109, 128)       # #736d80
TEXT_FAINT = (96, 92, 108)         # #605c6c — dim captions, e.g. "BPM"

# ── Accent (coral — the bot's primary color) ────────────────────────────
ACCENT = (228, 93, 84)      # #e45d54
ACCENT_PP = (236, 106, 96)  # #ec6a60 — pp numbers (slightly lighter)

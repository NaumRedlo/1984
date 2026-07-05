"""
Pillow-based card generators (1984 dystopia theme).

BaseCardRenderer — shared primitives (fonts, header, footer, separators).
+ 5-page profile cards, compare card with avatars, recent cards.
"""

from utils.logger import get_logger

# Re-export from extracted modules for backward compatibility
from services.image.constants import (  # noqa: F401
    BG_COLOR, HEADER_BG, ROW_EVEN, ROW_ODD, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN, SECTION_BG, PANEL_BG,
    TOP_COLORS, GRADE_COLORS, MOD_COLORS, MONTH_NAMES,
    CARD_WIDTH, HEADER_HEIGHT, ROW_HEIGHT, FOOTER_HEIGHT, PADDING_X, VALUE_RIGHT_X,
    ASSETS_DIR, FONT_DIR, TORUS_BOLD, TORUS_SEMI, TORUS_REG, HUNINN,
    FLAGS_DIR, ICONS_DIR, FALLBACK_CANDIDATES,
)
from services.image.utils import (  # noqa: F401
    load_icon, load_flag, _find_font, _none_coro,
    _get_shared_session, close_shared_session, download_image,
    rounded_rect_crop, cover_center_crop, draw_cover_background, draw_line_graph,
    MAX_IMAGE_BYTES,
)
from services.image.base import BaseCardRenderer as _BaseCardRenderer  # noqa: F401
from services.image.render.profile import ProfileCardMixin
from services.image.render.titles import TitlesCardMixin
from services.image.render.top_plays import TopPlaysCardMixin
from services.image.render.recent import RecentCardMixin
from services.image.render.compare import CompareCardMixin
from services.image.render.map_card import MapCardMixin

logger = get_logger("services.image_gen")

# Re-export BaseCardRenderer for backward compatibility
BaseCardRenderer = _BaseCardRenderer


class _CardRendererMixin(ProfileCardMixin, TitlesCardMixin, TopPlaysCardMixin, RecentCardMixin, CompareCardMixin, MapCardMixin, _BaseCardRenderer):
    """Combines all domain-specific card-renderer mixins."""



from services.image.leaderboard import LeaderboardCardGenerator  # noqa: E402


class CardRenderer(_CardRendererMixin, LeaderboardCardGenerator):
    """Backward-compatible facade combining all card generators."""


card_renderer = CardRenderer()

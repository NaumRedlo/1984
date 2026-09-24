from utils.logger import get_logger

from services.image.constants import (
    BG_COLOR, HEADER_BG, ROW_EVEN, ROW_ODD, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN, SECTION_BG, PANEL_BG,
    TOP_COLORS, GRADE_COLORS, MOD_COLORS, MONTH_NAMES,
    CARD_WIDTH, HEADER_HEIGHT, ROW_HEIGHT, FOOTER_HEIGHT, PADDING_X, VALUE_RIGHT_X,
    ASSETS_DIR, FONT_DIR, SANS_BOLD, SANS_SEMI, SANS_REG,
    FLAGS_DIR, ICONS_DIR, FALLBACK_CANDIDATES,
)
from services.image.utils import (
    load_icon, load_flag, _find_font, _none_coro,
    _get_shared_session, close_shared_session, download_image,
    rounded_rect_crop, cover_center_crop, draw_cover_background,
    MAX_IMAGE_BYTES,
)
from services.image.base import BaseCardRenderer as _BaseCardRenderer
from services.image.render.profile import ProfileCardMixin
from services.image.render.titles import TitlesCardMixin
from services.image.render.top_plays import TopPlaysCardMixin
from services.image.render.recent import RecentCardMixin
from services.image.render.compare import CompareCardMixin
from services.image.render.map_card import MapCardMixin
from services.image.render.map_leaderboard import MapLeaderboardCardMixin

logger = get_logger("services.image_gen")

BaseCardRenderer = _BaseCardRenderer

class _CardRendererMixin(ProfileCardMixin, TitlesCardMixin, TopPlaysCardMixin, RecentCardMixin, CompareCardMixin, MapCardMixin, MapLeaderboardCardMixin, _BaseCardRenderer):
    pass

from services.image.leaderboard import LeaderboardCardGenerator

class CardRenderer(_CardRendererMixin, LeaderboardCardGenerator):
    pass

card_renderer = CardRenderer()

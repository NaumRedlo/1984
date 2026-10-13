"""Persistent eligibility for players authenticated outside Telegram chats."""
from sqlalchemy import exists, or_

from db.models.oauth_token import OAuthToken
from db.models.player import Player
from db.models.render_worker_token import RenderWorkerToken


def authenticated_player():
    """SQL predicate, including legacy tokens linked by Telegram identity."""
    return or_(
        exists().where(
            RenderWorkerToken.revoked_at.is_(None),
            or_(RenderWorkerToken.player_id == Player.id,
                RenderWorkerToken.issued_to == Player.telegram_id),
        ),
        exists().where(or_(OAuthToken.player_id == Player.id,
                           OAuthToken.telegram_id == Player.telegram_id)),
    )

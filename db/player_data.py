from sqlalchemy import delete, select

from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
from db.models.title_progress import UserTitleProgress
from db.models.user import User

async def clear_if_last(session, user) -> bool:
    if user.player_id is None:
        return False
    stays = (await session.execute(
        select(User.id).where(User.player_id == user.player_id, User.id != user.id, User.osu_user_id.isnot(None)).limit(1)
    )).first()
    if stays is not None:
        return False
    for model in (UserBestScore, UserMapAttempt, UserTitleProgress):
        await session.execute(delete(model).where(model.player_id == user.player_id))
    return True

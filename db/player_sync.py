import logging

from sqlalchemy import delete, event, inspect, select
from sqlalchemy.orm import Session

from db.models.chat_member import ChatMember
from db.models.player import OWN, PROGRESS, SHARED, Player
from db.models.user import User

logger = logging.getLogger(__name__)

_on = True

def switch_on(on: bool = True) -> None:
    global _on
    _on = on

def is_on() -> bool:
    return _on

def _detach(session, user) -> None:
    player_id = user.player_id
    if user.id is not None:
        session.execute(delete(ChatMember).where(ChatMember.user_id == user.id))
    user.player = None
    user.player_id = None
    if player_id is None:
        return
    stays = session.execute(select(ChatMember.id).where(ChatMember.player_id == player_id)).first()
    stays = stays or any(isinstance(found, ChatMember) and found.player is not None and found.player.id == player_id for found in session.new)
    if not stays:
        player = session.get(Player, player_id)
        if player is not None:
            player.telegram_id = None
            for name in (*PERSONAL, *PROGRESS):
                setattr(player, name, None)

def _waiting(session, **wanted):
    for found in session.new:
        if isinstance(found, Player) and all(getattr(found, name) == value for name, value in wanted.items()):
            return found
    return None

LATER_WINS = ("last_seen_at",)
PERSONAL = ("active_title_code", "share_replays")

def _bare(moment):
    return moment.replace(tzinfo=None) if getattr(moment, "tzinfo", None) is not None else moment

def _inherit(user, player) -> None:
    for name in (*PERSONAL, *PROGRESS):
        held = getattr(player, name)
        if held is None:
            continue
        mine = getattr(user, name)
        if name in LATER_WINS and mine is not None and _bare(mine) >= _bare(held):
            continue
        if mine != held:
            setattr(user, name, held)

def _link(session, user) -> None:
    player = _waiting(session, osu_user_id=user.osu_user_id) or session.execute(select(Player).where(Player.osu_user_id == user.osu_user_id)).scalar_one_or_none()
    if player is not None and player.telegram_id not in (None, user.telegram_id):
        logger.info("osu! %s is %s's; users row of telegram %s stays apart", user.osu_user_id, player.telegram_id, user.telegram_id)
        return
    if player is None or player.telegram_id is None:
        stored = session.execute(select(Player).where(Player.telegram_id == user.telegram_id)).scalars().all()
        other = _waiting(session, telegram_id=user.telegram_id) or next((found for found in stored if found.telegram_id == user.telegram_id and found is not player), None)
        if other is not None:
            logger.info("telegram %s already plays as another osu! account; users row for %s stays apart", user.telegram_id, user.osu_user_id)
            return
    if player is None:
        player = Player(osu_user_id=user.osu_user_id, telegram_id=user.telegram_id, **{name: getattr(user, name) for name in SHARED})
        session.add(player)
    else:
        if player.telegram_id is None:
            player.telegram_id = user.telegram_id
        _inherit(user, player)
        for name in SHARED:
            if (name in OWN and name not in PERSONAL) or getattr(player, name) is None:
                setattr(player, name, getattr(user, name))
    user.player = player
    session.add(ChatMember(chat_id=user.chat_id, player=player, user=user))

def _keep(session, user) -> None:
    if inspect(user).attrs.player.history.added:
        return
    player = session.get(Player, user.player_id) if user.player_id is not None else None
    if not user.osu_user_id:
        if player is not None:
            _detach(session, user)
        return
    if player is not None and player.osu_user_id != user.osu_user_id:
        _detach(session, user)
        player = None
    if player is None:
        _link(session, user)
        return
    state = inspect(user)
    changed = [name for name in SHARED if state.attrs[name].history.has_changes()]
    if not changed:
        return
    for name in changed:
        setattr(player, name, getattr(user, name))
    siblings = session.execute(select(User).where(User.player_id == player.id, User.id != user.id)).scalars().all()
    for sibling in siblings:
        for name in changed:
            if getattr(sibling, name) != getattr(user, name):
                setattr(sibling, name, getattr(user, name))

@event.listens_for(Session, "before_flush")
def sync_players(session, _context, _instances) -> None:
    if not _on:
        return
    users = [found for found in (*session.new, *session.dirty) if isinstance(found, User)]
    gone = [found for found in session.deleted if isinstance(found, User)]
    if not users and not gone:
        return
    with session.no_autoflush:
        for user in gone:
            if user.id is not None:
                session.execute(delete(ChatMember).where(ChatMember.user_id == user.id))
        for user in users:
            _keep(session, user)

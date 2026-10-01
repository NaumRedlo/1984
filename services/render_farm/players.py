from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import select

from db.models.best_score import UserBestScore
from db.models.chat_member import ChatMember
from db.models.player import Player
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from services.leaderboard.deltas import DELTA_CATEGORIES
from services.render_farm.community import TITLE_REGISTRY, TOP, _play, _profile, naive, person, stamp
from utils.timeutils import utcnow

PIN_EVERY = timedelta(days=30)

PINNED = "pinned"
TOO_SOON = "too soon"
NOT_REGISTERED = "not registered"
NOT_THERE = "not in that chat"

LINKED = "linked"
TAKEN = "taken"
GONE = "gone"

class Rowless:
    id = 0
    chat_id = None

    def __init__(self, player: Player) -> None:
        self._player = player

    @property
    def player_id(self) -> int:
        return self._player.id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._player, name, None)

def _freshest(rows: list) -> Any:
    return max(rows, key=lambda row: (naive(row.last_api_update) or datetime.min, row.id))

async def _rows_by_player(session, player_ids: Optional[list[int]] = None) -> dict[int, list]:
    wanted = select(User).where(User.player_id.isnot(None), User.osu_user_id.isnot(None))
    if player_ids is not None:
        wanted = wanted.where(User.player_id.in_(player_ids))
    grouped: dict[int, list] = {}
    for row in (await session.execute(wanted)).scalars().all():
        grouped.setdefault(row.player_id, []).append(row)
    return grouped

async def everyone(session, viewer: int, *, query: str = "", now: Optional[datetime] = None, signed: Iterable[int] = (),
                   me: Optional[int] = None) -> dict[str, Any]:
    players = {player.id: player for player in (await session.execute(select(Player))).scalars().all()}
    grouped = await _rows_by_player(session, list(players))
    wanted = query.strip().lower()
    chosen = {
        player_id: _freshest(rows)
        for player_id, rows in grouped.items()
        if not wanted or wanted in (players[player_id].osu_username or "").lower()
    }
    for player_id in signed:
        player = players.get(player_id)
        if player is not None and player_id not in grouped and (not wanted or wanted in (player.osu_username or "").lower()):
            chosen[player_id] = Rowless(player)
    titles: dict[int, set[str]] = {player_id: set() for player_id in chosen}
    best: dict[int, list] = {player_id: [] for player_id in chosen}
    if chosen:
        for row in (await session.execute(
            select(UserTitleProgress).where(UserTitleProgress.player_id.in_(list(chosen)), UserTitleProgress.unlocked.is_(True))
        )).scalars().all():
            if row.title_code in TITLE_REGISTRY:
                titles[row.player_id].add(row.title_code)
        for row in (await session.execute(
            select(UserBestScore).where(UserBestScore.player_id.in_(list(chosen))).order_by(UserBestScore.player_id, UserBestScore.pp.desc())
        )).scalars().all():
            best[row.player_id].append(row)

    people = []
    for player_id, row in chosen.items():
        card = person(
            row,
            titles=titles[player_id],
            top=[_play(found) for found in best[player_id][:TOP]],
            moved=[0] * len(DELTA_CATEGORIES),
            gained=[0.0] * len(DELTA_CATEGORIES),
            you=player_id == me or (bool(viewer) and players[player_id].telegram_id == viewer),
        )
        card["player"] = player_id
        people.append(card)
    people.sort(key=lambda card: card["pp"], reverse=True)
    return {"people": people, "at": stamp(naive(now) or utcnow())}

async def one(session, player_id: int, viewer: int, *, now: Optional[datetime] = None, signed: Iterable[int] = (),
              me: Optional[int] = None) -> Optional[dict[str, Any]]:
    player = await session.get(Player, player_id)
    rows = (await _rows_by_player(session, [player_id])).get(player_id)
    if player is None or (not rows and player_id not in signed and player_id != me):
        return None
    shown = _freshest(rows) if rows else Rowless(player)
    body = await _profile(session, shown, you=player_id == me or (bool(viewer) and player.telegram_id == viewer), now=now)
    body["player"] = player_id
    return body

async def _player_of(session, viewer) -> Optional[Player]:
    from services.render_farm.videos import player_of

    return await player_of(session, viewer)

def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

async def signed_in(session, osu_user: dict[str, Any]) -> Optional[Player]:
    osu_id = _number(osu_user.get("id"))
    if not osu_id:
        return None
    player = (await session.execute(select(Player).where(Player.osu_user_id == osu_id))).scalar_one_or_none()
    stats = osu_user.get("statistics") or {}
    country = ((osu_user.get("country") or {}).get("code") or osu_user.get("country_code") or "")[:2]
    seen = {
        "osu_username": str(osu_user.get("username") or "")[:255],
        "country": country or None,
        "player_pp": _number(stats.get("pp")),
        "global_rank": _number(stats.get("global_rank")),
        "accuracy": round(float(stats.get("hit_accuracy") or 0.0), 2),
        "play_count": _number(stats.get("play_count")),
        "play_time": _number(stats.get("play_time")),
        "ranked_score": _number(stats.get("ranked_score")),
        "total_hits": _number(stats.get("total_hits")),
        "total_score": _number(stats.get("total_score")),
        "avatar_url": osu_user.get("avatar_url"),
        "cover_url": (osu_user.get("cover") or {}).get("url") or osu_user.get("cover_url"),
    }
    if player is None:
        player = Player(osu_user_id=osu_id, telegram_id=None, **seen)
        session.add(player)
    elif not (await _rows_by_player(session, [player.id])).get(player.id):
        for name, value in seen.items():
            if value is not None:
                setattr(player, name, value)
    await session.flush()
    return player

async def link_telegram(session, player_id: int, telegram_id: int) -> str:
    player = await session.get(Player, player_id)
    if player is None:
        return GONE
    if player.telegram_id == telegram_id:
        return LINKED
    other = (await session.execute(select(Player.id).where(Player.telegram_id == telegram_id))).first()
    if player.telegram_id is not None or other is not None:
        return TAKEN
    player.telegram_id = telegram_id
    from db.models.oauth_token import OAuthToken

    kept = (await session.execute(select(OAuthToken).where(OAuthToken.player_id == player.id))).scalar_one_or_none()
    if kept is not None:
        other_token = (await session.execute(select(OAuthToken.id).where(OAuthToken.telegram_id == telegram_id))).first()
        if other_token is None:
            kept.telegram_id = telegram_id
        else:
            await session.delete(kept)
    await session.commit()
    return LINKED

def _pin_body(player: Optional[Player]) -> dict[str, Any]:
    if player is None or player.pinned_chat_id is None:
        return {"chat": None, "since": None, "free_at": None}
    since = naive(player.pinned_at)
    return {
        "chat": player.pinned_chat_id,
        "since": stamp(since),
        "free_at": stamp(since + PIN_EVERY) if since else None,
    }

async def pin_of(session, viewer: int) -> dict[str, Any]:
    return _pin_body(await _player_of(session, viewer))

async def pinned_chat(session, viewer: int) -> Optional[int]:
    player = await _player_of(session, viewer)
    return player.pinned_chat_id if player is not None else None

async def pin(session, viewer: int, chat_id: int, *, now: Optional[datetime] = None) -> tuple[str, dict[str, Any]]:
    moment = naive(now) or utcnow()
    player = await _player_of(session, viewer)
    if player is None:
        return NOT_REGISTERED, _pin_body(None)
    member = (await session.execute(
        select(ChatMember.id).where(ChatMember.player_id == player.id, ChatMember.chat_id == chat_id)
    )).first()
    if member is None or chat_id >= 0:
        return NOT_THERE, _pin_body(player)
    if player.pinned_chat_id == chat_id:
        return PINNED, _pin_body(player)
    since = naive(player.pinned_at)
    if player.pinned_chat_id is not None and since is not None and moment < since + PIN_EVERY:
        return TOO_SOON, _pin_body(player)
    player.pinned_chat_id = chat_id
    player.pinned_at = moment
    await session.commit()
    return PINNED, _pin_body(player)

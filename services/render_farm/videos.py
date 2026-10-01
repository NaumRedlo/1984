import json
import re
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import delete, func, select

from db.models.chat_member import ChatMember
from db.models.player import Player
from db.models.shared_video import SharedVideo, VideoDelivery
from services.render_farm.community import naive, stamp
from utils.timeutils import utcnow

SHARED = "shared"
EVERYONE = "everyone"
NOBODY = "nobody"
ACCEPTS = (SHARED, EVERYONE, NOBODY)

AT_ONCE = 20
INBOX_MOST = 100
DAILY = 60
SETTINGS_MOST = 16 * 1024

SENT = "sent"
NO_ONE = "no one"
YOURSELF = "yourself"
CLOSED = "closed"
FULL = "full"
LIMIT = "limit"

KINDS = ("video", "animation", "document")

_HASH = re.compile(r"^[0-9a-f]{32}$")

def accept_of(player: Player) -> str:
    return player.accept_videos if player.accept_videos in ACCEPTS else SHARED

def lets(accept: str, shared: bool) -> bool:
    return accept == EVERYONE or (accept == SHARED and shared)

def _hash(value: Any) -> Optional[str]:
    said = str(value or "").strip().lower()
    return said if _HASH.match(said) else None

def _words(value: Any, most: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value.strip()[:most] or None

def described(meta: dict) -> dict[str, Any]:
    mods = meta.get("mods")
    mods = ",".join(str(mod).strip().upper()[:4] for mod in mods if str(mod).strip())[:64] if isinstance(mods, list) else None
    settings = meta.get("settings")
    kept = None
    if isinstance(settings, dict):
        text = json.dumps(settings, separators=(",", ":"))
        kept = text if len(text) <= SETTINGS_MOST else None
    return {
        "player": _words(meta.get("player"), 255),
        "song": _words(meta.get("song"), 512),
        "version": _words(meta.get("version"), 255),
        "mods": mods or None,
        "map_hash": _hash(meta.get("map_hash")),
        "settings": kept,
    }

def media_of(sent: Any) -> tuple[Optional[str], Any]:
    for kind in KINDS:
        media = getattr(sent, kind, None)
        if media is not None and getattr(media, "file_id", None):
            return kind, media
    return None, None

async def player_of(session, who) -> Optional[Player]:
    player_id = getattr(who, "player_id", None)
    if player_id is not None:
        return await session.get(Player, player_id)
    telegram_id = getattr(who, "telegram_id", who)
    if not telegram_id:
        return None
    return (await session.execute(select(Player).where(Player.telegram_id == telegram_id))).scalar_one_or_none()

async def keep(session, owner: int, kind: str, media: Any, meta: dict) -> int:
    unique = getattr(media, "file_unique_id", None)
    video = None
    if unique:
        video = (await session.execute(
            select(SharedVideo).where(SharedVideo.owner == owner, SharedVideo.file_unique_id == unique)
        )).scalars().first()
    if video is None:
        video = SharedVideo(owner=owner, file_id=media.file_id)
        session.add(video)
    thumb = getattr(media, "thumbnail", None)
    video.kind = kind
    video.file_id = media.file_id
    video.file_unique_id = unique
    video.thumb_id = getattr(thumb, "file_id", None)
    video.size = getattr(media, "file_size", None)
    video.duration = getattr(media, "duration", None)
    video.width = getattr(media, "width", None)
    video.height = getattr(media, "height", None)
    for name, value in described(meta).items():
        if value is not None or getattr(video, name) is None:
            setattr(video, name, value)
    await session.commit()
    return video.id

async def owned(session, telegram_id: int, video_id: int) -> Optional[SharedVideo]:
    video = await session.get(SharedVideo, video_id)
    return video if video is not None and video.owner == telegram_id else None

async def attach_replay(session, telegram_id: int, video_id: int, replay_hash: str) -> bool:
    video = await owned(session, telegram_id, video_id)
    if video is None:
        return False
    video.replay_hash = replay_hash
    await session.commit()
    return True

async def mates(session, player_id: int) -> set[int]:
    mine = select(ChatMember.chat_id).where(ChatMember.player_id == player_id)
    found = await session.execute(
        select(ChatMember.player_id).where(ChatMember.chat_id.in_(mine), ChatMember.player_id != player_id).distinct()
    )
    return {row[0] for row in found.all()}

def _face(player: Player) -> dict[str, Any]:
    return {
        "player": player.id,
        "name": player.osu_username,
        "country": (player.country or "").upper(),
        "avatar": player.avatar_url or f"https://a.ppy.sh/{player.osu_user_id}",
    }

def has_app(player: Player, linked: Iterable[int], signed: Iterable[int] = ()) -> bool:
    return (player.telegram_id is not None and player.telegram_id in linked) or player.id in signed

async def receivers(session, who, linked: Iterable[int], signed: Iterable[int] = ()) -> Optional[list[dict[str, Any]]]:
    me = await player_of(session, who)
    if me is None:
        return None
    here, known = set(linked), set(signed)
    near = await mates(session, me.id)
    found = []
    for player in (await session.execute(select(Player).where(Player.id != me.id))).scalars().all():
        shared = player.id in near
        if not has_app(player, here, known) or not lets(accept_of(player), shared):
            continue
        found.append({**_face(player), "shared": shared})
    found.sort(key=lambda face: (not face["shared"], face["name"].lower()))
    return found

async def share(session, telegram_id: int, video_id: int, to: list[int], linked: Iterable[int], signed: Iterable[int] = (), *,
                now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    moment = naive(now) or utcnow()
    me = await player_of(session, telegram_id)
    video = await owned(session, telegram_id, video_id)
    if me is None or video is None:
        return None
    here, known = set(linked), set(signed)
    near = await mates(session, me.id)
    today = (await session.execute(
        select(func.count(VideoDelivery.id)).where(VideoDelivery.sender_id == me.id, VideoDelivery.sent_at >= moment - timedelta(days=1))
    )).scalar() or 0
    sent: list[int] = []
    refused: list[dict[str, Any]] = []
    for player_id in list(dict.fromkeys(to))[:AT_ONCE]:
        why = None
        player = await session.get(Player, player_id)
        if player is None:
            why = NO_ONE
        elif player.id == me.id:
            why = YOURSELF
        elif not has_app(player, here, known) or not lets(accept_of(player), player.id in near):
            why = CLOSED
        if why is None:
            already = (await session.execute(
                select(VideoDelivery).where(VideoDelivery.video_id == video.id, VideoDelivery.recipient_id == player_id)
            )).scalars().first()
            if already is not None:
                already.sent_at = moment
                already.seen_at = None
                sent.append(player_id)
                continue
            waiting = (await session.execute(
                select(func.count(VideoDelivery.id)).where(VideoDelivery.recipient_id == player_id)
            )).scalar() or 0
            if today >= DAILY:
                why = LIMIT
            elif waiting >= INBOX_MOST:
                why = FULL
        if why is not None:
            refused.append({"player": player_id, "why": why})
            continue
        session.add(VideoDelivery(video_id=video.id, sender_id=me.id, recipient_id=player_id, sent_at=moment))
        today += 1
        sent.append(player_id)
    await session.commit()
    return {"sent": sent, "refused": refused}

def _row(delivery: VideoDelivery, video: SharedVideo, sender: Optional[Player]) -> dict[str, Any]:
    settings = None
    if video.settings:
        try:
            settings = json.loads(video.settings)
        except ValueError:
            settings = None
    return {
        "id": delivery.id,
        "from": _face(sender) if sender is not None else None,
        "player": video.player or "",
        "song": video.song or "",
        "version": video.version or "",
        "mods": [mod for mod in (video.mods or "").split(",") if mod],
        "map_hash": video.map_hash or "",
        "duration": int(video.duration or 0),
        "size": int(video.size or 0),
        "width": int(video.width or 0),
        "height": int(video.height or 0),
        "thumb": bool(video.thumb_id),
        "replay": bool(video.replay_hash),
        "settings": settings,
        "sent_at": stamp(naive(delivery.sent_at)),
        "seen": delivery.seen_at is not None,
    }

async def inbox(session, who, *, now: Optional[datetime] = None) -> dict[str, Any]:
    me = await player_of(session, who)
    rows: list[dict[str, Any]] = []
    if me is not None:
        found = (await session.execute(
            select(VideoDelivery, SharedVideo)
            .join(SharedVideo, SharedVideo.id == VideoDelivery.video_id)
            .where(VideoDelivery.recipient_id == me.id)
            .order_by(VideoDelivery.sent_at.desc(), VideoDelivery.id.desc())
        )).all()
        senders = {delivery.sender_id for delivery, _ in found}
        faces = {
            player.id: player
            for player in (await session.execute(select(Player).where(Player.id.in_(senders)))).scalars().all()
        } if senders else {}
        rows = [_row(delivery, video, faces.get(delivery.sender_id)) for delivery, video in found]
    return {
        "registered": me is not None,
        "accept": accept_of(me) if me is not None else SHARED,
        "videos": rows,
        "at": stamp(naive(now) or utcnow()),
    }

async def accept(session, who, value: str) -> Optional[str]:
    me = await player_of(session, who)
    if me is None or value not in ACCEPTS:
        return None
    me.accept_videos = value
    await session.commit()
    return value

async def received(session, who, delivery_id: int) -> Optional[tuple[VideoDelivery, SharedVideo, Optional[Player]]]:
    me = await player_of(session, who)
    delivery = await session.get(VideoDelivery, delivery_id)
    if me is None or delivery is None or delivery.recipient_id != me.id:
        return None
    video = await session.get(SharedVideo, delivery.video_id)
    if video is None:
        return None
    return delivery, video, await session.get(Player, delivery.sender_id)

async def seen(session, who, delivery_id: int, *, now: Optional[datetime] = None) -> bool:
    found = await received(session, who, delivery_id)
    if found is None:
        return False
    if found[0].seen_at is None:
        found[0].seen_at = naive(now) or utcnow()
        await session.commit()
    return True

async def drop(session, who, delivery_id: int) -> bool:
    found = await received(session, who, delivery_id)
    if found is None:
        return False
    await session.execute(delete(VideoDelivery).where(VideoDelivery.id == delivery_id))
    await session.commit()
    return True

def caption(video: SharedVideo, sender: Optional[Player]) -> str:
    line = video.song or ""
    if video.version:
        line = f"{line} [{video.version}]".strip()
    first = " — ".join(part for part in (video.player or "", line) if part)
    return "\n".join(part for part in (first, sender.osu_username if sender is not None else "") if part)[:1024]

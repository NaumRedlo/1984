import hashlib
import os
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete, func, select

from config.settings import PLAYER_REPLAYS_DIR, PLAYER_REPLAYS_EACH, PLAYER_REPLAYS_STORAGE_MOST
from db.models.player import Player
from db.models.shared_replay import SharedReplay
from services.render_farm import videos
from services.render_farm.community import naive, stamp
from utils.logger import get_logger
from utils.osr import header
from utils.timeutils import utcnow

logger = get_logger("services.render_farm.replays")

LISTED = 200
CHAT = "chat"
ALL = "all"

KEPT = "kept"
KNOWN = "known"
NOT_REGISTERED = "not registered"
NOT_SHARING = "not sharing"
NOT_A_REPLAY = "not a replay"
NOT_YOURS = "not yours"
FULL = "full"

def path_of(replay_hash: str, folder: Optional[str] = None) -> str:
    return os.path.join(folder or PLAYER_REPLAYS_DIR, f"{replay_hash}.osr")

def _used(folder: str) -> int:
    if not os.path.isdir(folder):
        return 0
    total = 0
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_file():
                total += entry.stat().st_size
    return total

def _words(value: Any) -> Optional[str]:
    return value.strip()[:255] or None if isinstance(value, str) else None

async def _forget_files(session, hashes: list[str], folder: Optional[str]) -> None:
    for replay_hash in set(hashes):
        left = (await session.execute(select(func.count(SharedReplay.id)).where(SharedReplay.replay_hash == replay_hash))).scalar() or 0
        if left == 0:
            try:
                os.unlink(path_of(replay_hash, folder))
            except OSError:
                pass

async def state(session, who) -> Optional[dict[str, Any]]:
    player = await videos.player_of(session, who)
    if player is None:
        return None
    count = (await session.execute(select(func.count(SharedReplay.id)).where(SharedReplay.player_id == player.id))).scalar() or 0
    return {"on": bool(player.share_replays), "name": player.osu_username, "count": int(count), "most": PLAYER_REPLAYS_EACH}

async def switch(session, who, on: bool, *, folder: Optional[str] = None) -> Optional[dict[str, Any]]:
    player = await videos.player_of(session, who)
    if player is None:
        return None
    player.share_replays = on
    if not on:
        hashes = [row[0] for row in (await session.execute(select(SharedReplay.replay_hash).where(SharedReplay.player_id == player.id))).all()]
        await session.execute(delete(SharedReplay).where(SharedReplay.player_id == player.id))
        await session.flush()
        await _forget_files(session, hashes, folder)
    await session.commit()
    return await state(session, who)

async def keep(session, who, data: bytes, named: Optional[dict] = None, *, osu=None, folder: Optional[str] = None,
               most: Optional[int] = None, each: Optional[int] = None, now: Optional[datetime] = None) -> str:
    folder = folder or PLAYER_REPLAYS_DIR
    most = PLAYER_REPLAYS_STORAGE_MOST if most is None else most
    each = PLAYER_REPLAYS_EACH if each is None else each
    player = await videos.player_of(session, who)
    if player is None:
        return NOT_REGISTERED
    if not player.share_replays:
        return NOT_SHARING
    head = header(data)
    if head is None or head.mode != 0:
        return NOT_A_REPLAY
    if (head.player or "").strip().casefold() != (player.osu_username or "").strip().casefold():
        return NOT_YOURS
    replay_hash = hashlib.md5(data).hexdigest()
    already = (await session.execute(
        select(SharedReplay.id).where(SharedReplay.player_id == player.id, SharedReplay.replay_hash == replay_hash)
    )).first()
    if already is not None:
        return KNOWN
    path = path_of(replay_hash, folder)
    if not os.path.exists(path):
        if _used(folder) + len(data) > most:
            logger.warning("shared replays are full: %s refused", replay_hash)
            return FULL
        os.makedirs(folder, exist_ok=True)
        partial = f"{path}.part"
        with open(partial, "wb") as out:
            out.write(data)
        os.replace(partial, path)
    named = named if isinstance(named, dict) else {}
    artist, title, version = _words(named.get("artist")), _words(named.get("title")), _words(named.get("version"))
    set_id = named.get("beatmapset")
    set_id = int(set_id) if isinstance(set_id, int) and not isinstance(set_id, bool) and 0 < set_id < 1 << 31 else None
    if not (artist and title):
        known = (await session.execute(
            select(SharedReplay).where(SharedReplay.beatmap_md5 == head.beatmap_md5, SharedReplay.title.isnot(None))
        )).scalars().first()
        if known is not None:
            artist, title, version, set_id = known.artist, known.title, known.version, known.beatmapset_id
        elif osu is not None:
            try:
                found = await osu.lookup_beatmap_by_checksum(head.beatmap_md5)
            except Exception as exc:
                logger.info("no name for the map %s: %s", head.beatmap_md5, exc)
                found = None
            if isinstance(found, dict):
                beatmapset = found.get("beatmapset") or {}
                artist, title, version = _words(beatmapset.get("artist")), _words(beatmapset.get("title")), _words(found.get("version"))
                said = found.get("beatmapset_id") or beatmapset.get("id")
                set_id = int(said) if str(said or "").isdigit() else None
    session.add(SharedReplay(
        player_id=player.id, replay_hash=replay_hash, beatmap_md5=head.beatmap_md5, size=len(data), mods=head.mods, score=head.score,
        combo=head.combo, count_300=head.count300, count_100=head.count100, count_50=head.count50, count_miss=head.misses,
        perfect=head.perfect, played_at=head.played_at or naive(now) or utcnow(), artist=artist, title=title, version=version, beatmapset_id=set_id,
    ))
    await session.flush()
    extra = (await session.execute(
        select(SharedReplay).where(SharedReplay.player_id == player.id).order_by(SharedReplay.played_at.desc(), SharedReplay.id.desc()).offset(each)
    )).scalars().all()
    gone = [row.replay_hash for row in extra]
    for row in extra:
        await session.delete(row)
    await session.flush()
    await _forget_files(session, gone, folder)
    await session.commit()
    return KNOWN if replay_hash in gone else KEPT

async def listed(session, who, scope: str = CHAT, *, limit: int = LISTED) -> Optional[dict[str, Any]]:
    me = await videos.player_of(session, who)
    if me is None:
        return None
    wanted = select(SharedReplay, Player).join(Player, Player.id == SharedReplay.player_id).where(Player.share_replays.is_(True), Player.id != me.id)
    if scope != ALL:
        near = await videos.mates(session, me.id)
        if not near:
            return {"replays": [], "at": stamp(utcnow())}
        wanted = wanted.where(Player.id.in_(near))
    found = (await session.execute(wanted.order_by(SharedReplay.played_at.desc(), SharedReplay.id.desc()).limit(limit))).all()
    seen: set[str] = set()
    rows = []
    for replay, player in found:
        if replay.replay_hash in seen:
            continue
        seen.add(replay.replay_hash)
        rows.append({
            "hash": replay.replay_hash,
            "player": player.osu_username,
            "player_id": player.id,
            "map_hash": replay.beatmap_md5,
            "artist": replay.artist or "",
            "title": replay.title or "",
            "version": replay.version or "",
            "beatmapset": replay.beatmapset_id,
            "played_at": stamp(naive(replay.played_at)),
            "size": int(replay.size or 0),
        })
    return {"replays": rows, "at": stamp(utcnow())}

async def readable(session, who, replay_hash: str) -> bool:
    me = await videos.player_of(session, who)
    if me is None:
        return False
    found = (await session.execute(
        select(SharedReplay.id).join(Player, Player.id == SharedReplay.player_id)
        .where(SharedReplay.replay_hash == replay_hash, Player.share_replays.is_(True))
    )).first()
    return found is not None

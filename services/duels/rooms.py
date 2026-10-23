from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from db.models.duel import DuelRoom, DuelRoomCapacity
from utils.timeutils import utcnow

BOT_ACCOUNT = "bancho"
HELD = ("reserved", "creating", "open", "closing", "unknown")
LEASE_SECONDS = 20

def room_name(duel_id: str, first: str, second: str) -> str:
    short = duel_id.replace("-", "")[:8]
    return f"Dossier {short}: {first} vs {second}"[:50]

async def _capacity(session, limit: int, now: datetime) -> DuelRoomCapacity:
    touched = await session.execute(
        update(DuelRoomCapacity).where(DuelRoomCapacity.bot_account == BOT_ACCOUNT).values(capacity=limit, updated_at=now)
    )
    if touched.rowcount == 0:
        session.add(DuelRoomCapacity(bot_account=BOT_ACCOUNT, capacity=limit, reserved_count=0, updated_at=now))
        await session.flush()
    return await session.scalar(select(DuelRoomCapacity).where(DuelRoomCapacity.bot_account == BOT_ACCOUNT).with_for_update())

async def held(session) -> int:
    return await session.scalar(
        select(func.count()).select_from(DuelRoom).where(DuelRoom.origin == "bot", DuelRoom.status.in_(HELD))
    ) or 0

async def reserve(factory, duel_id: str, name: str, limit: int, now: Optional[datetime] = None) -> Optional[int]:
    now = now or utcnow()
    try:
        return await _reserve(factory, duel_id, name, limit, now)
    except IntegrityError:
        return await _reserve(factory, duel_id, name, limit, now)

async def _reserve(factory, duel_id: str, name: str, limit: int, now: datetime) -> Optional[int]:
    async with factory() as session:
        async with session.begin():
            capacity = await _capacity(session, limit, now)
            taken = await held(session)
            if taken >= capacity.capacity:
                capacity.reserved_count = taken
                capacity.updated_at = now
                return None
            room = DuelRoom(duel_id=duel_id, room_name=name, origin="bot", status="reserved", created_at=now)
            session.add(room)
            await session.flush()
            capacity.reserved_count = taken + 1
            capacity.updated_at = now
            return room.id

async def _move(factory, room_id: int, allowed: tuple[str, ...], target: str, now: Optional[datetime] = None,
                **fields: Any) -> bool:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            room = await session.scalar(select(DuelRoom).where(DuelRoom.id == room_id).with_for_update())
            if room is None or room.status not in allowed:
                return False
            room.status = target
            for key, value in fields.items():
                setattr(room, key, value)
            if target in ("closed", "released"):
                room.closed_at = now
            return True

async def creating(factory, room_id: int) -> bool:
    return await _move(factory, room_id, ("reserved",), "creating")

async def created(factory, room_id: int, match_id: int) -> bool:
    return await _move(factory, room_id, ("creating", "unknown"), "open", match_id=match_id, channel=f"#mp_{match_id}")

async def lost_track(factory, room_id: int) -> bool:
    return await _move(factory, room_id, ("creating",), "unknown")

async def released_unused(factory, room_id: int) -> bool:
    return await _move(factory, room_id, ("reserved",), "released")

async def closing(factory, room_id: int) -> bool:
    return await _move(factory, room_id, ("open", "unknown"), "closing")

async def closed(factory, room_id: int, now: Optional[datetime] = None) -> bool:
    return await _move(factory, room_id, ("open", "closing", "unknown"), "closed", now)

async def absent(factory, room_id: int, now: Optional[datetime] = None) -> bool:
    return await _move(factory, room_id, ("unknown",), "released", now)

async def by_name(factory, name: str) -> Optional[DuelRoom]:
    async with factory() as session:
        return await session.scalar(
            select(DuelRoom).where(DuelRoom.room_name == name, DuelRoom.status.in_(HELD)).order_by(DuelRoom.id.desc())
        )

async def take_lease(factory, room_id: int, owner: str, now: Optional[datetime] = None) -> Optional[int]:
    now = now or utcnow()
    async with factory() as session:
        async with session.begin():
            room = await session.scalar(select(DuelRoom).where(DuelRoom.id == room_id).with_for_update())
            if room is None:
                return None
            if room.lease_owner not in (None, owner) and room.lease_until is not None and room.lease_until > now:
                return None
            if room.lease_owner != owner:
                room.lease_epoch += 1
                room.lease_owner = owner
            room.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            return room.lease_epoch

async def still_owner(factory, room_id: int, epoch: int) -> bool:
    async with factory() as session:
        current = await session.scalar(select(DuelRoom.lease_epoch).where(DuelRoom.id == room_id))
        return current == epoch

async def open_room(factory, gateway, duel_id: str, name: str, limit: int, timeout: float = 30.0) -> tuple[str, Optional[int]]:
    room_id = await reserve(factory, duel_id, name, limit)
    if room_id is None:
        return "wait", None
    if not await creating(factory, room_id):
        return "lost", None
    match_id = await gateway.make(name, timeout)
    if match_id is None:
        await lost_track(factory, room_id)
        return "unknown", None
    await created(factory, room_id, match_id)
    return "open", match_id

def late_creations(factory, gateway):
    async def heard(event) -> None:
        if event.kind != "created":
            return
        room = await by_name(factory, event.data["name"])
        if room is not None and room.status == "unknown" and await created(factory, room.id, event.data["match"]):
            await gateway.join(f"#mp_{event.data['match']}")
    return heard

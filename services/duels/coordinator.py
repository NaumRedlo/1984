import secrets
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import select

from db.models.duel import Duel, DuelAttempt, DuelBonus, DuelCommand, DuelDeadline, DuelParticipant, DuelPool, DuelPoolEntry, DuelRoom, DuelRound
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from db.models.player import Player
from services.duels import catalog, generator, outbox, rooms, series, states, verifier
from services.duels.bancho_text import Said
from services.duels.features import FEATURE_VERSION
from services.duels.generator import SLOT_MODS, Candidate, Skill
from services.duels.room_control import RoomControl
from services.duels.rules import PARAMETERS, SLOTS, in_catalogue
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("duels.coordinator")

CATALOG_VERSION = "catalog-v1"
CLOCK_SLACK = 10

class NotYet(Exception):
    pass

async def measured_profiles(factory, people: list[DuelParticipant]) -> tuple[dict[str, Skill], dict[str, Skill]]:
    from services.duels import portfolio

    found = []
    async with factory() as session:
        async with session.begin():
            for person in sorted(people, key=lambda p: p.side or ""):
                await portfolio.from_attempts(session, person.player_id)
                skills, _ = portfolio.profile(await portfolio.observations_of(session, person.player_id), utcnow())
                found.append(skills)
    return found[0], found[1]

async def catalogue(session) -> list[Candidate]:
    rows = (await session.execute(
        select(DuelMapAssignment, DuelMapRevision)
        .join(DuelMapRevision, (DuelMapRevision.beatmap_id == DuelMapAssignment.beatmap_id)
              & (DuelMapRevision.hash == DuelMapAssignment.revision_hash))
        .where(DuelMapAssignment.slot.is_not(None))
    )).all()
    found = []
    for assignment, revision in rows:
        level = catalog.playable(assignment, revision)
        if level is None:
            continue
        from services.duels import balance
        opinion = await balance.weigh(session, int(assignment.beatmap_id))
        extra = catalog.secondary_of(assignment) if assignment.state == catalog.HUMAN_APPROVED else None
        for slot, secondary in ((assignment.slot, False), (extra, True)):
            if slot not in SLOT_MODS:
                continue
            analysis = await session.scalar(select(DuelMapAnalysis).where(DuelMapAnalysis.revision_hash == revision.hash,
                                                                         DuelMapAnalysis.mods == "".join(SLOT_MODS[slot]),
                                                                         DuelMapAnalysis.feature_version == FEATURE_VERSION))
            if analysis is None or not in_catalogue(analysis.stars):
                continue
            peak = None
            windows = (analysis.features or {}).get("aim_windows") or {}
            if windows.get("2s") and windows.get("10s"):
                peak = windows["2s"].get("max", 0.0) / max(windows["10s"].get("p90", 0.0), 0.01)
            found.append(Candidate(feedback=balance.cost_of(opinion["q_bad"]), beatmap_id=int(assignment.beatmap_id),
                                   beatmapset_id=int(revision.beatmapset_id or assignment.beatmap_id), mapper=revision.mapper or "", slot=slot,
                                   stars=float(analysis.stars), level=level, confidence=assignment.confidence, revision=revision.hash,
                                   peak_ratio=peak, secondary=secondary))
    return found

class Coordinator:
    def __init__(self, factory, gateway, osu, profiles: Callable = measured_profiles, budget: Optional[verifier.Budget] = None,
                 room_limit: int = PARAMETERS["created_room_limit"], now: Callable[[], datetime] = utcnow):
        self.factory = factory
        self.gateway = gateway
        self.osu = osu
        self.profiles = profiles
        self.budget = budget or verifier.Budget()
        self.room_limit = room_limit
        self.now = now
        self.controls: dict[int, RoomControl] = {}
        self.seated: dict[int, set[int]] = {}
        self.present: Callable[[int], bool] = lambda player_id: True
        self.witness: Callable[[str], dict[int, dict[str, Any]]] = lambda duel_id: {}

    def install(self) -> None:
        outbox.HANDLERS.update({
            "generate_pool": self.generate_pool,
            "open_room": self.open_room,
            "setup_round": self.setup_round,
            "start_round": self.start_round,
            "abort_round": self.abort_round,
            "verify_round": self.verify_round,
            "close_room": self.close_room,
            "bonus_round": self.bonus_round,
            "adopt_room": self.adopt_room,
            "verify_bonus": self.verify_bonus,
            "reinvite_room": self.reinvite_room,
        })
        self.gateway.listen(self.heard)
        self.gateway.listen(rooms.late_creations(self.factory, self.gateway))

    async def _people(self, duel_id: str) -> list[tuple[DuelParticipant, Player]]:
        async with self.factory() as session:
            return list((await session.execute(
                select(DuelParticipant, Player).join(Player, Player.id == DuelParticipant.player_id)
                .where(DuelParticipant.duel_id == duel_id).order_by(DuelParticipant.side)
            )).all())

    async def _room(self, duel_id: str) -> Optional[DuelRoom]:
        async with self.factory() as session:
            return await session.scalar(select(DuelRoom).where(DuelRoom.duel_id == duel_id, DuelRoom.status == "open")
                                        .order_by(DuelRoom.id.desc()))

    def _control(self, match_id: int) -> RoomControl:
        if match_id not in self.controls:
            self.controls[match_id] = RoomControl(self.gateway, match_id)
        return self.controls[match_id]

    async def generate_pool(self, command: dict[str, Any]) -> None:
        duel_id = command["payload"]["duel"]
        people = [participant for participant, _ in await self._people(duel_id)]
        async with self.factory() as session:
            duel = await session.get(Duel, duel_id)
            candidates = await catalogue(session)
        if duel is None or duel.state not in (states.ACCEPTED, states.GENERATING_POOL):
            return
        first, second = await self.profiles(self.factory, people)
        pool = generator.generate(candidates, first, second, duel.seed)
        await series.pool_built(self.factory, duel_id, pool, CATALOG_VERSION, FEATURE_VERSION)

    async def open_room(self, command: dict[str, Any]) -> None:
        duel_id = command["payload"]["duel"]
        async with self.factory() as session:
            duel = await session.get(Duel, duel_id)
        if duel is None or duel.state != states.WAITING_ROOM:
            return
        people = await self._people(duel_id)
        names = [player.osu_username for _, player in people]
        held = await self._room(duel_id)
        if held is None:
            outcome, match_id = await rooms.open_room(self.factory, self.gateway, duel_id, rooms.room_name(duel_id, *names),
                                                      self.room_limit)
            if outcome != "open":
                raise NotYet(outcome)
        else:
            match_id = held.match_id
        password = secrets.token_urlsafe(6)
        async with self.factory() as session:
            async with session.begin():
                room = await session.scalar(select(DuelRoom).where(DuelRoom.match_id == match_id))
                room.capabilities = {"password": password}
        await self._control(match_id).setup(password, names)
        await series.room_ready(self.factory, duel_id)

    async def reinvite_room(self, command: dict[str, Any]) -> None:
        duel_id = command["payload"]["duel"]
        async with self.factory() as session:
            duel = await session.get(Duel, duel_id)
        held = await self._room(duel_id)
        if duel is None or duel.state != states.WAITING_PLAYERS or held is None or held.match_id is None:
            return
        seated = self.seated.get(held.match_id, set())
        names = [player.osu_username for participant, player in await self._people(duel_id) if participant.player_id not in seated]
        await self._control(held.match_id).invite(names)

    async def heard(self, event: Said) -> None:
        if not event.channel.startswith("#mp_"):
            return
        match_id = int(event.channel[4:])
        async with self.factory() as session:
            room = await session.scalar(select(DuelRoom).where(DuelRoom.match_id == match_id, DuelRoom.status == "open"))
            if room is None:
                return
            duel = await session.get(Duel, room.duel_id)
            people = (await session.execute(
                select(DuelParticipant, Player).join(Player, Player.id == DuelParticipant.player_id)
                .where(DuelParticipant.duel_id == room.duel_id)
            )).all()
        by_name = {player.osu_username.lower(): participant for participant, player in people}
        seated = self.seated.setdefault(match_id, set())
        who = by_name.get(str(event.data.get("user", "")).lower())
        if event.kind == "joined" and who is not None:
            seated.add(who.player_id)
            if duel.state == states.WAITING_PLAYERS and len(seated) == len(people) and all(self.present(p.player_id) for p, _ in people):
                await series.players_present(self.factory, duel.id)
        elif event.kind == "left" and who is not None:
            seated.discard(who.player_id)
            if duel.state not in states.TERMINAL:
                await series.player_lost(self.factory, duel.id, who.player_id)
            elif duel.state == states.COMPLETED:
                await self._bonus_step(duel.id, "setup", "cancelled", None)
                if not seated:
                    await self._later(duel.id, 0)
        elif event.kind == "started" and duel.state == states.STARTING:
            await series.round_started(self.factory, duel.id, match_id)
        elif event.kind == "finished_all" and duel.state == states.PLAYING:
            await series.round_finished(self.factory, duel.id)
        elif event.kind == "finished_all" and duel.state == states.COMPLETED:
            await self._bonus_step(duel.id, "setup", "verifying", "verify_bonus")
        elif event.kind == "aborted" and duel.state == states.ABORTING:
            await series.aborted(self.factory, duel.id)
        elif event.kind == "closed":
            await rooms.closed(self.factory, room.id)

    async def _round(self, duel_id: str) -> tuple[DuelRound, DuelPoolEntry, list[tuple[DuelParticipant, Player]]]:
        async with self.factory() as session:
            current = await session.scalar(select(DuelRound).where(DuelRound.duel_id == duel_id).order_by(DuelRound.ordinal.desc()))
            entry = await session.scalar(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id)
                                         .where(DuelPool.duel_id == duel_id, DuelPoolEntry.slot == current.slot))
        return current, entry, await self._people(duel_id)

    async def setup_round(self, command: dict[str, Any]) -> None:
        duel_id = command["payload"]["duel"]
        room = await self._room(duel_id)
        if room is None:
            raise NotYet("no_room")
        current, entry, people = await self._round(duel_id)
        control = self._control(room.match_id)
        free = current.slot == "TB"
        mods = list(SLOT_MODS[current.slot])
        if not await control.prepare(int(entry.beatmap_id), mods, free):
            raise NotYet("room_did_not_answer")
        problems = await control.check(int(entry.beatmap_id), {int(p.osu_user_id) for p, _ in people}, set(mods), free)
        if problems:
            raise NotYet(",".join(problems))
        await series.round_configured(self.factory, duel_id)

    async def start_round(self, command: dict[str, Any]) -> None:
        room = await self._room(command["payload"]["duel"])
        if room is None:
            raise NotYet("no_room")
        await self._control(room.match_id).start()

    async def abort_round(self, command: dict[str, Any]) -> None:
        room = await self._room(command["payload"]["duel"])
        if room is None:
            raise NotYet("no_room")
        if not await self._control(room.match_id).abort():
            raise NotYet("abort_unconfirmed")

    async def close_room(self, command: dict[str, Any]) -> None:
        room = await self._room(command["payload"]["duel"])
        if room is None:
            return
        await rooms.closing(self.factory, room.id)
        if await self._control(room.match_id).close():
            await rooms.closed(self.factory, room.id)
        else:
            raise NotYet("close_unconfirmed")

    async def verify_round(self, command: dict[str, Any]) -> None:
        duel_id = command["payload"]["duel"]
        current, entry, people = await self._round(duel_id)
        async with self.factory() as session:
            attempt = await session.get(DuelAttempt, command["payload"]["attempt"])
        events, cursor = await verifier.history(self.osu, int(attempt.match_id), attempt.last_event_id, self.budget)
        async with self.factory() as session:
            async with session.begin():
                kept = {}
                for event in events:
                    row = await verifier.keep(session, int(attempt.match_id), event)
                    if (event.get("game") or {}).get("id") is not None:
                        kept[int(event["game"]["id"])] = row.id
                row = await session.get(DuelAttempt, attempt.id)
                if cursor is not None:
                    row.last_event_id = cursor
        games = verifier.games(events)
        mods = frozenset(SLOT_MODS[current.slot])
        commanded = attempt.commanded_at or attempt.start_at or self.now()
        expected = verifier.Expected(int(entry.beatmap_id), frozenset(int(p.osu_user_id) for p, _ in people), mods, current.slot == "TB",
                                     commanded - timedelta(seconds=CLOCK_SLACK))
        candidates = [game for game in games.values() if game.get("beatmap_id") == int(entry.beatmap_id)]
        if not candidates:
            raise NotYet("no_game_yet")
        game = max(candidates, key=lambda found: int(found["id"]))
        verdict = verifier.judge(game, expected)
        if verdict.state == "pending":
            raise NotYet("game_unfinished")
        async with self.factory() as session:
            async with session.begin():
                if not await verifier.bind(session, attempt.id, int(game["id"])):
                    raise NotYet("game_taken")
        await self._compare_witness(duel_id, int(entry.beatmap_id), people, verdict)
        await series.round_verdict(self.factory, duel_id, {
            "state": verdict.state, "winner": verdict.winner, "scores": {str(k): v for k, v in verdict.scores.items()},
            "mods": {str(k): v for k, v in verdict.mods.items()}, "reasons": verdict.reasons, "game_id": int(game["id"]),
            "details": {str(k): v for k, v in verdict.details.items()},
            "evidence": kept.get(int(game["id"])),
        })

    async def _bonus_step(self, duel_id: str, source: str, target: str, then: Optional[str]) -> bool:
        async with self.factory() as session:
            async with session.begin():
                extra = await session.scalar(select(DuelBonus).where(DuelBonus.duel_id == duel_id).with_for_update())
                if extra is None or extra.state != source:
                    return False
                extra.state = target
                if target in ("cancelled", "completed"):
                    extra.finished_at = self.now()
                if then is not None:
                    session.add(DuelCommand(duel_id=duel_id, kind=then, payload={"duel": duel_id}, next_attempt_at=self.now(),
                                            created_at=self.now()))
                return True

    async def bonus_round(self, command: dict[str, Any]) -> None:
        duel_id = command["payload"]["duel"]
        room = await self._room(duel_id)
        if room is None:
            raise NotYet("no_room")
        async with self.factory() as session:
            entry = await session.scalar(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id)
                                         .where(DuelPool.duel_id == duel_id, DuelPoolEntry.slot == "TB"))
        control = self._control(room.match_id)
        if not await control.prepare(int(entry.beatmap_id), ["NF"], True):
            raise NotYet("room_did_not_answer")
        async with self.factory() as session:
            async with session.begin():
                extra = await session.scalar(select(DuelBonus).where(DuelBonus.duel_id == duel_id).with_for_update())
                extra.commanded_at = self.now()
        await control.start(10)

    async def verify_bonus(self, command: dict[str, Any]) -> None:
        duel_id = command["payload"]["duel"]
        room = await self._room(duel_id)
        async with self.factory() as session:
            extra = await session.scalar(select(DuelBonus).where(DuelBonus.duel_id == duel_id))
            entry = await session.scalar(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id)
                                         .where(DuelPool.duel_id == duel_id, DuelPoolEntry.slot == "TB"))
        if extra is None or extra.state != "verifying" or room is None:
            return
        people = await self._people(duel_id)
        events, _ = await verifier.history(self.osu, int(room.match_id), None, self.budget)
        games = [game for game in verifier.games(events).values() if game.get("beatmap_id") == int(entry.beatmap_id)]
        if not games:
            raise NotYet("no_game_yet")
        game = max(games, key=lambda found: int(found["id"]))
        expected = verifier.Expected(int(entry.beatmap_id), frozenset(int(p.osu_user_id) for p, _ in people), frozenset({"NF"}), True,
                                     (extra.commanded_at or self.now()) - timedelta(seconds=CLOCK_SLACK))
        verdict = verifier.judge(game, expected)
        if verdict.state == "pending":
            raise NotYet("game_unfinished")
        winner = next((p.player_id for p, _ in people if int(p.osu_user_id) == verdict.winner), None)
        async with self.factory() as session:
            async with session.begin():
                row = await session.scalar(select(DuelBonus).where(DuelBonus.duel_id == duel_id).with_for_update())
                row.state = "completed" if verdict.state in ("valid", "tie") else "cancelled"
                row.game_id = int(game["id"])
                row.scores = {str(k): v for k, v in verdict.scores.items()}
                row.winner_id = winner
                row.finished_at = self.now()
        await self._later(duel_id, PARAMETERS["room_linger_seconds"])

    async def _later(self, duel_id: str, seconds: float) -> None:
        async with self.factory() as session:
            async with session.begin():
                duel = await session.get(Duel, duel_id)
                if duel is None:
                    return
                if seconds <= 0:
                    session.add(DuelCommand(duel_id=duel_id, kind="close_room", payload={"duel": duel_id}, next_attempt_at=self.now(),
                                            created_at=self.now()))
                else:
                    session.add(DuelDeadline(duel_id=duel_id, state_version=duel.state_version, phase=duel.phase or 0, kind="room_linger",
                                             expires_at=self.now() + timedelta(seconds=seconds)))

    async def adopt_room(self, command: dict[str, Any]) -> None:
        duel_id = command["payload"]["duel"]
        async with self.factory() as session:
            room = await session.get(DuelRoom, command["payload"]["room"])
            duel = await session.get(Duel, duel_id)
        if room is None or duel is None or duel.state != states.WAITING_ROOM:
            return
        control = self._control(int(room.match_id))
        trusted = await control.can_referee()
        async with self.factory() as session:
            async with session.begin():
                row = await session.get(DuelRoom, room.id)
                row.status = "open" if trusted else "released"
                row.capabilities = {**(row.capabilities or {}), "referee": trusted}
        if not trusted:
            await series.note(self.factory, duel_id, "external_room_refused", {"match": int(room.match_id)})
            return
        names = [player.osu_username for _, player in await self._people(duel_id)]
        password = secrets.token_urlsafe(6)
        async with self.factory() as session:
            async with session.begin():
                row = await session.get(DuelRoom, room.id)
                row.capabilities = {**(row.capabilities or {}), "password": password}
        await control.setup(password, names)
        await series.room_ready(self.factory, duel_id)

    async def _compare_witness(self, duel_id: str, beatmap_id: int, people, verdict) -> None:
        seen = self.witness(duel_id)
        for participant, _ in people:
            state = seen.get(participant.player_id)
            if not state:
                continue
            official = verdict.scores.get(int(participant.osu_user_id))
            reasons = []
            if state.get("beatmap_id") is not None and int(state["beatmap_id"]) != beatmap_id:
                reasons.append("beatmap")
            if official is not None and state.get("score") is not None:
                said = float(state["score"])
                if abs(said - official) > max(1.0, 0.02 * max(official, said)):
                    reasons.append("score")
            if reasons:
                await series.note(self.factory, duel_id, "witness_mismatch", {
                    "player": participant.player_id, "reasons": reasons, "witness": {"beatmap_id": state.get("beatmap_id"),
                                                                                   "score": state.get("score")},
                    "official": {"beatmap_id": beatmap_id, "score": official}})

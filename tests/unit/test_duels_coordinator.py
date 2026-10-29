from datetime import timedelta
from dataclasses import replace

import pytest

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel import Duel, DuelRoom
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from db.models.player import Player
from services.duels import balance, deadlines, outbox, series, service, states
from services.duels.bancho_text import parse_line, said
from services.duels.coordinator import Coordinator
from services.duels.features import FEATURE_VERSION
from services.duels.generator import SLOT_MODS, Skill
from services.duels.rules import SLOTS
from services.duels.verifier import Budget
from utils.timeutils import utcnow

def _bancho(channel: str, text: str):
    return said(parse_line(f":BanchoBot!cho@ppy.sh PRIVMSG {channel} :{text}"))

class FakeBancho:
    def __init__(self):
        self.listeners = []
        self.said = []
        self.joined = []
        self.map = None
        self.mods = []
        self.made = 0

    def listen(self, listener):
        self.listeners.append(listener)

    async def emit(self, channel, text):
        for listener in list(self.listeners):
            await listener(_bancho(channel, text))

    async def join(self, channel):
        self.joined.append(channel)

    async def make(self, name, timeout):
        self.made += 1
        return 500

    async def say(self, target, text):
        self.said.append(text)
        if text.startswith("!mp map"):
            self.map = int(text.split()[2])
            await self.emit(target, f"Changed beatmap to https://osu.ppy.sh/b/{self.map} Some - Song [Hard]")
        elif text.startswith("!mp mods"):
            self.mods = [m for m in text.split()[2:] if m != "Freemod"]
            names = {"NF": "NoFail", "HD": "Hidden", "HR": "HardRock", "DT": "DoubleTime"}
            await self.emit(target, "Enabled " + ", ".join(names[m] for m in self.mods) + ", disabled FreeMod")
        elif text == "!mp settings":
            names = {"NF": "NoFail", "HD": "Hidden", "HR": "HardRock", "DT": "DoubleTime"}
            for line in ("Room name: Dossier, History: https://osu.ppy.sh/mp/500",
                         f"Beatmap: https://osu.ppy.sh/b/{self.map} Some - Song [Hard]",
                         "Team mode: HeadToHead, Win condition: ScoreV2",
                         "Active mods: " + ", ".join(names[m] for m in self.mods),
                         "Players: 2",
                         "Slot 1  Ready     https://osu.ppy.sh/u/101 one              ",
                         "Slot 2  Ready     https://osu.ppy.sh/u/102 two              "):
                await self.emit(target, line)
        elif text.startswith("!mp start"):
            await self.emit(target, "The match has started!")

class FakeOsu:
    def __init__(self):
        self.events = []

    async def get_match(self, match_id, after=None, limit=None):
        return {"events": [event for event in self.events if after is None or event["id"] > after][:limit]}

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'coordinator.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add_all([Player(id=1, osu_user_id=101, osu_username="one"), Player(id=2, osu_user_id=102, osu_username="two")])
        number = 0
        for slot in SLOTS:
            for stars in (5.2, 5.3, 5.4, 5.5, 5.6):
                number += 1
                digest = f"{number:032x}"
                session.add(DuelMapRevision(beatmap_id=number, beatmapset_id=number, hash=digest, status="ok", ranked_status="ranked",
                                            mapper=f"mapper{number}"))
                session.add(DuelMapAssignment(beatmap_id=number, slot=slot, state="HUMAN_APPROVED", revision_hash=digest, admitted=True))
                session.add(DuelMapAnalysis(revision_hash=digest, mods="".join(SLOT_MODS[slot]), engine_version="test",
                                            feature_version=FEATURE_VERSION, model_version="none", stars=stars, features={}))
        await session.commit()
    yield made
    await engine.dispose()

@pytest.fixture(autouse=True)
def unseen_candidates(monkeypatch):
    from services.duels import discovery
    from services.duels.coordinator import catalogue

    async def found(factory, osu, aims, seed):
        async with factory() as session:
            known = await catalogue(session)
        return [replace(candidate, beatmap_id=candidate.beatmap_id + 1000,
                        beatmapset_id=candidate.beatmapset_id + 1000, source="model") for candidate in known]
    monkeypatch.setattr(discovery, "candidates", found)

    async def every(factory):
        return frozenset(SLOTS)
    monkeypatch.setattr(balance, "trusted_slots", every)

async def _fives(factory, people):
    skills = {slot: Skill(5.5, 0.5) for slot in SLOTS}
    return skills, skills

async def _state(factory, duel_id):
    async with factory() as session:
        return (await session.get(Duel, duel_id)).state

async def test_a_round_goes_from_challenge_to_an_official_point_through_bancho(factory, monkeypatch):
    monkeypatch.setattr(outbox, "HANDLERS", {})
    bancho, osu = FakeBancho(), FakeOsu()
    coordinator = Coordinator(factory, bancho, osu, profiles=_fives, budget=Budget(per_minute=1000))
    coordinator.install()

    duel = await service.invite(factory, 1, 2, "invite-e2e")
    await service.accept(factory, duel["id"], 2, "accept-e2e")
    assert await outbox.sweep(factory) == 1
    assert await _state(factory, duel["id"]) == states.WAITING_ROOM
    assert await outbox.sweep(factory) == 1
    assert await _state(factory, duel["id"]) == states.WAITING_PLAYERS
    assert bancho.made == 1 and "!mp set 0 3 2" in bancho.said and "!mp invite one" in bancho.said
    async with factory() as session:
        room = await session.scalar(select(DuelRoom))
        assert (room.status, room.match_id, len(room.capabilities["password"]) >= 8) == ("open", 500, True)

    await bancho.emit("#mp_500", "one joined in slot 1.")
    assert await _state(factory, duel["id"]) == states.WAITING_PLAYERS
    asked = await series.reinvite(factory, duel["id"], 1, "reinvite-e2e")
    assert asked["actions"] == ["reinvite", "surrender"]
    before = len(bancho.said)
    assert await outbox.sweep(factory) == 1
    assert bancho.said[before:] == ["!mp invite two"]
    await bancho.emit("#mp_500", "two joined in slot 2.")
    assert await _state(factory, duel["id"]) == states.PREPARING

    async with factory() as session:
        shown = await series.view(session, await session.get(Duel, duel["id"]), 1)
        outsider = await series.view(session, await session.get(Duel, duel["id"]), None)
    assert shown["room"]["match_id"] == 500 and shown["room"]["password"] and outsider["room"] is None
    for player in (1, 2):
        await series.pool_ack(factory, duel["id"], player, shown["pool"]["manifest"], f"ack-{player}-e2e")
    assert await _state(factory, duel["id"]) == states.DRAFTING

    assert sum(e["source"] == "model" for e in shown["pool"]["entries"]) in (4, 5)
    side = {p["side"]: p["player"] for p in shown["participants"]}
    free = [s for s in SLOTS if s != "TB"]
    for turn, who in enumerate(series.BANS):
        await series.draft(factory, duel["id"], side[who], free[turn], f"ban-{turn}-e2e")
    picked = free[4]
    await series.draft(factory, duel["id"], side["B"], picked, "pick-e2e")
    assert await _state(factory, duel["id"]) == states.ROUND_SETUP
    assert await outbox.sweep(factory) == 1
    assert await _state(factory, duel["id"]) == states.WAITING_READY
    assert bancho.mods == list(SLOT_MODS[picked])

    version = (await service.read(factory, duel["id"], 1))["version"]
    for player in (1, 2):
        await series.ready(factory, duel["id"], player, f"ready-{player}-e2e", expected_version=version)
    assert await outbox.sweep(factory) == 1
    assert await _state(factory, duel["id"]) == states.PLAYING

    await bancho.emit("#mp_500", "The match has finished!")
    assert await _state(factory, duel["id"]) == states.VERIFYING
    assert await outbox.sweep(factory) == 0
    async with factory() as session:
        from db.models.duel import DuelCommand
        waiting = await session.scalar(select(DuelCommand).where(DuelCommand.kind == "verify_round"))
        assert (waiting.status, waiting.last_error) == ("pending", "NotYet: no_game_yet")
        waiting.next_attempt_at = utcnow() - timedelta(seconds=1)
        await session.commit()

    start = (utcnow() + timedelta(seconds=1)).isoformat() + "Z"
    osu.events = [{"id": 1, "detail": {"type": "match-created"}},
                  {"id": 2, "detail": {"type": "other"}, "game": {
                      "id": 9001, "beatmap_id": bancho.map, "mode": "osu", "mode_int": 0, "scoring_type": "scorev2",
                      "team_type": "head-to-head", "start_time": start, "end_time": start, "mods": list(SLOT_MODS[picked]),
                      "scores": [{"user_id": 101, "score": 700000, "mods": []}, {"user_id": 102, "score": 650000, "mods": []}]}}]
    assert await outbox.sweep(factory) == 1
    async with factory() as session:
        after = await series.view(session, await session.get(Duel, duel["id"]), 1)
    assert after["state"] == states.SELECTING
    assert after["rounds"][0]["winner"] == 1
    assert after["wins"][next(p["side"] for p in after["participants"] if p["player"] == 1)] == 1


async def test_a_map_with_a_secondary_slot_is_offered_twice_and_counted_apart(factory):
    from services.duels import catalog, desk
    from services.duels.coordinator import catalogue
    from db.models.duel import DuelRole

    async with factory() as session:
        held = await session.get(DuelMapAssignment, 1)
        held.secondary_slots = ["HD2"]
        session.add(DuelMapAnalysis(revision_hash=held.revision_hash, mods="NFHD", engine_version="test", feature_version=FEATURE_VERSION,
                                    model_version="none", stars=5.6, features={}))
        session.add(DuelRole(player_id=1, role="BALANCE_REVIEWER"))
        await session.commit()
    async with factory() as session:
        found = [c for c in await catalogue(session) if c.beatmap_id == 1]
    assert sorted((c.slot, c.secondary) for c in found) == [("HD2", True), (found[0].slot if not found[0].secondary else found[1].slot, False)]
    coverage = await desk.coverage(factory, 1)
    assert sum(coverage["secondary"]["HD2"]) == 1 and coverage["secondary"]["HD2"][desk.band_of(5.6)] == 1
    assert catalog.secondary_of(held) == "HD2"

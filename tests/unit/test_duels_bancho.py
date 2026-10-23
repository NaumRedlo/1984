import asyncio
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel import Duel, DuelRoom
from services.duels import rooms
from services.duels.bancho import Gateway
from services.duels.bancho_text import Settings, mods_line, parse_line, problems, said, setup_lines

NOW = datetime(2026, 10, 23, 12, 0, 0)
BOT = ":BanchoBot!cho@ppy.sh PRIVMSG"

def _said(target: str, text: str):
    return said(parse_line(f"{BOT} {target} :{text}"))

def test_irc_lines_split_into_prefix_command_params_and_text():
    line = parse_line(":BanchoBot!cho@ppy.sh PRIVMSG #mp_123 :The match has started!\r\n")
    assert (line.nick, line.command, line.params, line.text) == ("BanchoBot", "PRIVMSG", ("#mp_123",), "The match has started!")
    assert parse_line("PING :cho.ppy.sh").text == "cho.ppy.sh"
    assert parse_line(":cho.ppy.sh 001 DossierBot :Welcome").command == "001"
    assert parse_line("") is None and parse_line("\r\n") is None

def test_only_banchobot_speaks_for_the_room():
    assert said(parse_line(":mallory!cho@ppy.sh PRIVMSG #mp_1 :The match has finished!")) is None
    assert said(parse_line(":BanchoBot!cho@ppy.sh NOTICE #mp_1 :The match has finished!")) is None
    assert _said("#mp_1", "The match has finished!").kind == "finished_all"

@pytest.mark.parametrize("text, kind, data", [
    ("Created the tournament match https://osu.ppy.sh/mp/114812345 Dossier 1a2b3c4d: Naum vs ennui", "created",
     {"match": 114812345, "name": "Dossier 1a2b3c4d: Naum vs ennui"}),
    ("Naum Redlo joined in slot 1.", "joined", {"user": "Naum Redlo", "slot": 1}),
    ("ennui joined in slot 2 for team blue.", "joined", {"user": "ennui", "slot": 2, "team": "blue"}),
    ("ennui moved to slot 3", "moved", {"user": "ennui", "slot": 3}),
    ("ennui left the game.", "left", {"user": "ennui"}),
    ("Changed beatmap to https://osu.ppy.sh/b/2345678 xi - FREEDOM DiVE [FOUR DIMENSIONS]", "map_set",
     {"beatmap": 2345678, "title": "xi - FREEDOM DiVE [FOUR DIMENSIONS]"}),
    ("Beatmap changed to: xi - Blue Zenith [FOUR DIMENSIONS] (https://osu.ppy.sh/b/658127)", "map_changed",
     {"beatmap": 658127, "title": "xi - Blue Zenith [FOUR DIMENSIONS]"}),
    ("The match has started!", "started", {}),
    ("Naum Redlo finished playing (Score: 812440, PASSED).", "finished", {"user": "Naum Redlo", "score": 812440, "state": "PASSED"}),
    ("ennui finished playing (Score: 0, FAILED).", "finished", {"user": "ennui", "score": 0, "state": "FAILED"}),
    ("All players are ready", "all_ready", {}),
    ("Aborted the match", "aborted", {}),
    ("Closed the match", "closed", {}),
    ("Match starts in 5 seconds", "countdown", {"seconds": 5}),
    ("Enabled NoFail, Hidden, disabled FreeMod", "mods_set", {"mods": "NoFail, Hidden"}),
    ("Changed match settings to 2 slots, HeadToHead, ScoreV2", "settings_set", {"size": 2, "team": "HeadToHead", "score": "ScoreV2"}),
    ("You cannot create any more tournament matches. Please close any previous tournament matches you have open.", "refused", {}),
    ("Something nobody has seen yet", "unknown", {}),
])
def test_banchobot_sentences_become_events(text, kind, data):
    event = _said("#mp_1", text)
    assert event.kind == kind
    assert event.data == data

def test_settings_are_gathered_and_checked_against_what_the_round_needs():
    collected = Settings()
    for text in ("Room name: Dossier 1a2b3c4d: Naum vs ennui, History: https://osu.ppy.sh/mp/114812345",
                 "Beatmap: https://osu.ppy.sh/b/658127 xi - Blue Zenith [FOUR DIMENSIONS]",
                 "Team mode: HeadToHead, Win condition: ScoreV2",
                 "Active mods: NoFail, Hidden",
                 "Players: 2",
                 "Slot 1  Not Ready https://osu.ppy.sh/u/2 Naum Redlo       [Host]",
                 "Slot 2  Ready     https://osu.ppy.sh/u/3 ennui            "):
        assert collected.take(_said("#mp_114812345", text))
    assert collected.complete and collected.match == 114812345
    assert collected.slots[0]["tags"] == ["Host"] and collected.slots[1]["user"] == "ennui"
    assert problems(collected, 658127, {2, 3}, {"NF", "HD"}, False) == []
    assert problems(collected, 1, {2, 4}, {"NF"}, True) == ["beatmap", "mods", "freemod", "stranger", "missing"]
    collected.win = "Score"
    assert problems(collected, 658127, {2, 3}, {"NF", "HD"}, False) == ["score_mode"]

def test_room_commands_follow_the_rules():
    assert setup_lines("kx4-river", ["Naum Redlo", "ennui"]) == [
        "!mp set 0 3 2", "!mp password kx4-river", "!mp lock", "!mp invite Naum_Redlo", "!mp invite ennui"]
    assert mods_line(["NF", "DT"]) == "!mp mods NF DT"
    assert mods_line(["NF"], free=True) == "!mp mods NF Freemod"

class FakeBancho:
    def __init__(self, script=None):
        self.sent: list[str] = []
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.script = script or {}
        self.opened = 0

    def __call__(self):
        return self

    async def open(self):
        self.opened += 1

    async def readline(self):
        return await self.incoming.get()

    async def write(self, line):
        self.sent.append(line)
        if line.startswith("NICK"):
            await self.incoming.put(":cho.ppy.sh 001 DossierBot :Welcome to Bancho")
        for trigger, reply in self.script.items():
            if line.startswith(trigger):
                if reply is not None:
                    await self.incoming.put(reply)

    async def close(self):
        pass

async def _running(gateway):
    task = asyncio.create_task(gateway.session())
    await asyncio.wait_for(gateway.connected.wait(), 1)
    return task

async def test_the_gateway_logs_in_answers_pings_and_rejoins_its_rooms():
    fake = FakeBancho()
    gateway = Gateway("DossierBot", "secret", fake, send_every=0)
    await gateway.join("#mp_5")
    task = await _running(gateway)
    await fake.incoming.put("PING :cho.ppy.sh")
    await asyncio.sleep(0.05)
    assert fake.sent[:3] == ["PASS secret", "NICK DossierBot", "USER DossierBot 0 * :DossierBot"]
    assert "JOIN #mp_5" in fake.sent and "PONG :cho.ppy.sh" in fake.sent
    await fake.incoming.put(None)
    await asyncio.wait_for(task, 1)
    assert not gateway.connected.is_set()

async def test_a_wrong_irc_password_stops_the_gateway_for_good():
    fake = FakeBancho({"NICK": ":cho.ppy.sh 464 DossierBot :Bad authentication token."})
    gateway = Gateway("DossierBot", "wrong", fake, send_every=0)
    await asyncio.wait_for(gateway.run(asyncio.Event()), 2)
    assert fake.opened == 1

async def test_outgoing_lines_keep_their_pace():
    fake = FakeBancho()
    gateway = Gateway("DossierBot", "secret", fake, send_every=0.05)
    task = await _running(gateway)
    for number in range(3):
        await gateway.say("#mp_1", f"line {number}")
    await asyncio.sleep(0.3)
    task.cancel()
    assert [line for line in fake.sent if line.startswith("PRIVMSG")] == [f"PRIVMSG #mp_1 :line {n}" for n in range(3)]

async def test_making_a_room_waits_for_its_name_and_never_asks_twice():
    name = "Dossier 1a2b3c4d: Naum vs ennui"
    fake = FakeBancho({"PRIVMSG BanchoBot :!mp make": f"{BOT} DossierBot :Created the tournament match https://osu.ppy.sh/mp/77 {name}"})
    gateway = Gateway("DossierBot", "secret", fake, send_every=0)
    heard = []

    async def listener(event):
        heard.append(event.kind)

    gateway.listen(listener)
    task = await _running(gateway)
    assert await gateway.make(name, timeout=1) == 77
    await asyncio.sleep(0.05)
    assert "JOIN #mp_77" in fake.sent and heard == ["created"]

    silent = FakeBancho({"PRIVMSG BanchoBot :!mp make": None})
    quiet = Gateway("DossierBot", "secret", silent, send_every=0)
    other = await _running(quiet)
    assert await quiet.make("Dossier 00000000: a vs b", timeout=0.1) is None
    assert sum(line.startswith("PRIVMSG BanchoBot :!mp make") for line in silent.sent) == 1
    task.cancel()
    other.cancel()

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rooms.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        for number in range(8):
            session.add(Duel(id=f"duel-{number}", mode="direct", state="WAITING_ROOM", rules_version="duels-v1", seed="s"))
        await session.commit()
    yield made
    await engine.dispose()

async def test_four_rooms_are_reserved_and_the_fifth_waits_even_when_asked_at_once(factory):
    results = await asyncio.gather(*[rooms.reserve(factory, f"duel-{n}", f"room {n}", 4, NOW) for n in range(6)])
    assert sum(result is not None for result in results) == 4
    assert await rooms.reserve(factory, "duel-7", "room 7", 4, NOW) is None
    async with factory() as session:
        assert await rooms.held(session) == 4

async def test_an_unanswered_make_keeps_its_place_until_reconciled(factory):
    first = await rooms.reserve(factory, "duel-0", "room 0", 1, NOW)
    assert await rooms.creating(factory, first)
    assert await rooms.lost_track(factory, first)
    assert await rooms.reserve(factory, "duel-1", "room 1", 1, NOW) is None
    assert await rooms.absent(factory, first)
    assert await rooms.reserve(factory, "duel-1", "room 1", 1, NOW) is not None

async def test_a_room_lives_from_reservation_to_closing_in_order(factory):
    room = await rooms.reserve(factory, "duel-0", "room 0", 4, NOW)
    assert not await rooms.created(factory, room, 9)
    assert await rooms.creating(factory, room) and await rooms.created(factory, room, 9)
    assert not await rooms.creating(factory, room)
    assert await rooms.closing(factory, room) and await rooms.closed(factory, room)
    async with factory() as session:
        row = await session.get(DuelRoom, room)
        assert (row.status, row.match_id, row.channel) == ("closed", 9, "#mp_9")
        assert await rooms.held(session) == 0

async def test_a_late_reply_to_make_binds_the_room_by_its_name(factory):
    class Joins:
        def __init__(self):
            self.joined = []

        async def join(self, channel):
            self.joined.append(channel)

    joins = Joins()
    room = await rooms.reserve(factory, "duel-0", "Dossier 0: a vs b", 4, NOW)
    await rooms.creating(factory, room)
    await rooms.lost_track(factory, room)
    heard = rooms.late_creations(factory, joins)
    await heard(_said("DossierBot", "Created the tournament match https://osu.ppy.sh/mp/88 Dossier 0: a vs b"))
    async with factory() as session:
        assert (await session.get(DuelRoom, room)).status == "open"
    assert joins.joined == ["#mp_88"]

async def test_opening_a_room_uses_the_gateway_once_and_waits_when_full(factory):
    class Maker:
        def __init__(self, answer):
            self.answer = answer
            self.asked = 0

        async def make(self, name, timeout):
            self.asked += 1
            return self.answer

    assert await rooms.open_room(factory, Maker(5), "duel-0", "room 0", 2) == ("open", 5)
    silent = Maker(None)
    assert await rooms.open_room(factory, silent, "duel-1", "room 1", 2) == ("unknown", None)
    full = Maker(6)
    assert await rooms.open_room(factory, full, "duel-2", "room 2", 2) == ("wait", None)
    assert (silent.asked, full.asked) == (1, 0)

async def test_room_leases_move_to_a_new_owner_only_after_they_expire(factory):
    room = await rooms.reserve(factory, "duel-0", "room 0", 4, NOW)
    first = await rooms.take_lease(factory, room, "worker-a", NOW)
    assert first == 1 and await rooms.take_lease(factory, room, "worker-a", NOW + timedelta(seconds=5)) == 1
    assert await rooms.take_lease(factory, room, "worker-b", NOW + timedelta(seconds=10)) is None
    second = await rooms.take_lease(factory, room, "worker-b", NOW + timedelta(seconds=30))
    assert second == 2
    assert not await rooms.still_owner(factory, room, first) and await rooms.still_owner(factory, room, second)

def test_room_names_carry_the_duel_and_fit_bancho():
    name = rooms.room_name("1a2b3c4d-5e6f-7a8b-9c0d-112233445566", "Naum Redlo", "a" * 60)
    assert name.startswith("Dossier 1a2b3c4d: Naum Redlo vs ") and len(name) == 50

class ScriptedRoom:
    def __init__(self, replies):
        self.replies = replies
        self.said: list[tuple[str, str]] = []
        self.listeners = []
        self.joined = []

    def listen(self, listener):
        self.listeners.append(listener)

    async def join(self, channel):
        self.joined.append(channel)

    async def say(self, target, text):
        self.said.append((target, text))
        for prefix, answers in self.replies.items():
            if text.startswith(prefix):
                for answer in answers:
                    for listener in self.listeners:
                        await listener(_said(target, answer))

SETTINGS = ["Room name: Dossier 0: a vs b, History: https://osu.ppy.sh/mp/42",
            "Beatmap: https://osu.ppy.sh/b/658127 xi - Blue Zenith [FOUR DIMENSIONS]",
            "Team mode: HeadToHead, Win condition: ScoreV2",
            "Active mods: NoFail",
            "Players: 2",
            "Slot 1  Ready     https://osu.ppy.sh/u/2 a                [Host]",
            "Slot 2  Ready     https://osu.ppy.sh/u/3 b               "]

async def test_room_control_prepares_a_round_checks_it_and_stops_it():
    from services.duels.room_control import RoomControl

    room = ScriptedRoom({
        "!mp map": ["Changed beatmap to https://osu.ppy.sh/b/658127 xi - Blue Zenith [FOUR DIMENSIONS]"],
        "!mp mods": ["Enabled NoFail, disabled FreeMod"],
        "!mp settings": SETTINGS,
        "!mp abort": ["Aborted the match"],
        "!mp close": ["Closed the match"],
    })
    control = RoomControl(room, 42)
    await control.setup("pw", ["a", "b"])
    assert await control.prepare(658127, ["NF"], False)
    assert await control.check(658127, {2, 3}, {"NF"}, False) == []
    assert await control.check(658127, {2, 4}, {"NF", "HD"}, False) == ["mods", "stranger", "missing"]
    await control.start()
    assert await control.abort() and await control.close()
    assert [text for _, text in room.said] == ["!mp set 0 3 2", "!mp password pw", "!mp lock", "!mp invite a", "!mp invite b",
                                               "!mp map 658127 0", "!mp mods NF", "!mp settings", "!mp settings", "!mp start 5",
                                               "!mp abort", "!mp close"]
    assert {target for target, _ in room.said} == {"#mp_42"}

async def test_a_room_without_referee_rights_is_not_taken_over():
    from services.duels import room_control
    from services.duels.room_control import RoomControl

    room_control.SETTINGS_WAIT = 0.05
    try:
        silent = RoomControl(ScriptedRoom({}), 43)
        assert not await silent.can_referee()
        trusted = RoomControl(ScriptedRoom({"!mp settings": [line.replace("mp/42", "mp/44") for line in SETTINGS]}), 44)
        assert await trusted.can_referee()
        assert silent.gateway.joined == ["#mp_43"]
    finally:
        room_control.SETTINGS_WAIT = 10.0

async def test_room_control_ignores_other_rooms_and_passes_on_live_events():
    from services.duels.room_control import RoomControl

    room = ScriptedRoom({})
    control = RoomControl(room, 42)
    await control.heard(_said("#mp_41", "The match has started!"))
    await control.heard(_said("#mp_42", "The match has started!"))
    assert control.events.qsize() == 1 and (await control.events.get()).kind == "started"

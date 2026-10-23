from datetime import timedelta

import pytest
from sqlalchemy import func, select

from db.models.duel import Duel, DuelBonus, DuelCommand, DuelEvent, DuelNotification, DuelRoom
from db.models.player import Player
from services.duels import deadlines, notify, series, service, states
from services.duels.errors import DuelError
from tests.unit.test_duels_series import Clock, _ban_all, _drafting, _play, _view, factory

async def _kinds(factory):
    async with factory() as session:
        return (await session.scalars(select(DuelCommand.kind).order_by(DuelCommand.id))).all()

async def _open_room(factory, duel_id, match_id=500):
    async with factory() as session:
        session.add(DuelRoom(duel_id=duel_id, room_name="Dossier x: one vs two", match_id=match_id, origin="bot", status="open",
                             capabilities={"password": "pw"}))
        await session.commit()

async def test_a_cancelled_duel_closes_its_open_room(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _open_room(factory, duel_id)
    await series.give_up(factory, duel_id, 1, clock.op(), now=clock.tick())
    assert (await _kinds(factory))[-1] == "close_room"

async def test_a_natural_three_nil_offers_a_bonus_tiebreaker_and_keeps_the_room(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _open_room(factory, duel_id)
    await _ban_all(factory, clock, duel_id)
    for _ in range(3):
        done = await _play(factory, clock, duel_id, "A")
    assert done["state"] == states.COMPLETED and done["bonus"]["state"] == "offered"
    assert "close_room" not in await _kinds(factory)
    assert "bonus" in (await _view(factory, duel_id, 1))["actions"] and "bonus" in (await _view(factory, duel_id, 2))["actions"]
    with pytest.raises(DuelError):
        await series.bonus(factory, duel_id, 1, True, clock.op(), tb_mods=["DT"], now=clock.tick())
    await series.bonus(factory, duel_id, 1, True, clock.op(), tb_mods=["HD"], now=clock.tick())
    await series.bonus(factory, duel_id, 2, True, clock.op(), now=clock.tick())
    after = await _view(factory, duel_id, 1)
    assert after["bonus"]["state"] == "setup" and after["wins"] == {"A": 3, "B": 0} and after["state"] == states.COMPLETED
    assert (await _kinds(factory))[-1] == "bonus_round"

async def test_an_unanswered_bonus_closes_the_room_after_thirty_seconds(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _open_room(factory, duel_id)
    await _ban_all(factory, clock, duel_id)
    for _ in range(3):
        await _play(factory, clock, duel_id, "B")
    await deadlines.sweep(factory, clock.tick(31))
    assert (await _view(factory, duel_id, 1))["bonus"]["state"] == "cancelled"
    assert (await _kinds(factory))[-1] == "close_room"

async def test_a_declined_bonus_closes_the_room_at_once(factory):
    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _open_room(factory, duel_id)
    await _ban_all(factory, clock, duel_id)
    for _ in range(3):
        await _play(factory, clock, duel_id, "A")
    await series.bonus(factory, duel_id, 2, False, clock.op(), now=clock.tick())
    assert (await _view(factory, duel_id, 1))["bonus"]["state"] == "cancelled"
    assert (await _kinds(factory))[-1] == "close_room"

async def test_both_players_must_name_the_same_room_before_the_bot_takes_it(factory):
    clock = Clock()
    invited = await service.invite(factory, 1, 2, clock.op(), now=clock.now)
    duel_id = invited["id"]
    await service.accept(factory, duel_id, 2, clock.op(), now=clock.tick())
    from tests.unit.test_duels_series import _pool
    await series.pool_built(factory, duel_id, _pool(), "c", "f", clock.tick())
    with pytest.raises(DuelError) as wrong:
        await series.external_room(factory, duel_id, 1, "https://example.com/mp/5", clock.op(), now=clock.tick())
    assert wrong.value.code == "ROOM_CONTROL_REQUIRED"
    await series.external_room(factory, duel_id, 1, "https://osu.ppy.sh/community/matches/123456", clock.op(), now=clock.tick())
    assert "adopt_room" not in await _kinds(factory)
    await series.external_room(factory, duel_id, 2, "https://osu.ppy.sh/mp/123456", clock.op(), now=clock.tick())
    assert (await _kinds(factory))[-1] == "adopt_room"
    assert series.match_of("123456") == 123456 and series.match_of("osu.ppy.sh/mp/x") is None

async def test_an_adopted_room_needs_referee_rights(factory, monkeypatch):
    from services.duels import outbox
    from services.duels.coordinator import Coordinator
    from tests.unit.test_duels_bancho import ScriptedRoom, SETTINGS

    clock = Clock()
    invited = await service.invite(factory, 1, 2, clock.op())
    duel_id = invited["id"]
    await service.accept(factory, duel_id, 2, clock.op())
    from tests.unit.test_duels_series import _pool
    await series.pool_built(factory, duel_id, _pool(), "c", "f")
    for player in (1, 2):
        await series.external_room(factory, duel_id, player, "42", clock.op())
    monkeypatch.setattr(outbox, "HANDLERS", {})
    room = ScriptedRoom({"!mp settings": SETTINGS})
    coordinator = Coordinator(factory, room, None)
    coordinator.install()
    assert await outbox.sweep(factory) >= 1
    assert (await _view(factory, duel_id, 1))["state"] == states.WAITING_PLAYERS
    async with factory() as session:
        adopted = await session.scalar(select(DuelRoom).where(DuelRoom.match_id == 42))
        assert (adopted.status, adopted.origin, adopted.capabilities["referee"]) == ("open", "external", True)

def test_notices_are_worded_for_each_moment():
    assert "kotofey" in notify.wording({"kind": "invitation"}, "ru", "kotofey", None, "", None)
    assert notify.wording({"kind": "room_ready"}, "en", "x", "Dossier 1: a vs b", "", None).count("Dossier 1: a vs b") == 1
    assert "3:1" in notify.wording({"kind": "finished"}, "ru", "x", None, "3:1", True)
    assert "три ничьи" in notify.wording({"kind": "cancelled", "reason": "unresolved_tie"}, "ru", "x", None, "", None)
    assert "техническая" in notify.wording({"kind": "cancelled", "reason": "disconnect"}, "ru", "x", None, "", None)
    assert "&lt;b&gt;" in notify.wording({"kind": "accepted"}, "en", "<b>", None, "", None)
    assert notify.wording({"kind": "nothing"}, "en", "x", None, "", None) is None

async def test_telegram_notices_go_to_linked_players_and_retry_on_failure(factory):
    async with factory() as session:
        (await session.get(Player, 2)).telegram_id = 222
        await session.commit()
    await service.invite(factory, 1, 2, "invite-notify")
    sent, broken = [], [True]

    async def send(chat, text):
        if broken[0]:
            raise RuntimeError("telegram is away")
        sent.append((chat, text))

    async def language(_telegram_id):
        return "ru"

    assert await notify.deliver(factory, send, language) == 0
    broken[0] = False
    assert await notify.deliver(factory, send, language) == 1
    assert sent[0][0] == 222 and "one" in sent[0][1]
    assert await notify.deliver(factory, send, language) == 0
    unread = await notify.unread(factory, 2)
    assert [item["payload"]["kind"] for item in unread] == ["invitation"]
    assert await notify.seen(factory, 2, [item["id"] for item in unread]) == 1
    assert await notify.seen(factory, 1, [item["id"] for item in unread]) == 0
    assert await notify.unread(factory, 2) == []

async def test_witness_that_disagrees_with_the_official_score_is_written_down(factory):
    from services.duels.coordinator import Coordinator
    from services.duels.verifier import Verdict

    invited = await service.invite(factory, 1, 2, "invite-witness")
    coordinator = Coordinator(factory, None, None)
    coordinator.witness = lambda duel_id: {1: {"beatmap_id": 5, "score": 900000}, 2: {"beatmap_id": 6, "score": 500000}}
    people = await coordinator._people(invited["id"])
    await coordinator._compare_witness(invited["id"], 5, people, Verdict("valid", scores={101: 901000, 102: 600000}))
    async with factory() as session:
        noted = (await session.scalars(select(DuelEvent).where(DuelEvent.type == "witness_mismatch"))).all()
    assert len(noted) == 1 and noted[0].payload["player"] == 2 and noted[0].payload["reasons"] == ["beatmap", "score"]

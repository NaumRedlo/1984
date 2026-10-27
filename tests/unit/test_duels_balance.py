from datetime import timedelta

import pytest
from sqlalchemy import func, select

from db.models.duel import (Duel, DuelAttempt, DuelAudit, DuelBalanceReview, DuelModelVersion, DuelParticipant, DuelPool, DuelPoolEntry,
                            DuelRole, DuelRound)
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from db.models.player import Player
from services.duels import balance, generator, series, service, states, training
from services.duels.errors import DuelError
from services.duels.features import FEATURE_VERSION
from tests.unit.test_duels_series import Clock, _ban_all, _drafting, _play, _view, factory

async def _played_series(factory, clock):
    duel_id = await _drafting(factory, clock)
    await _ban_all(factory, clock, duel_id)
    await _play(factory, clock, duel_id, "A")
    return duel_id, await _view(factory, duel_id, 1)

async def test_feedback_belongs_to_participants_and_replaces_itself_with_history(factory):
    clock = Clock()
    duel_id, duel = await _played_series(factory, clock)
    slot = duel["rounds"][0]["slot"]
    first = await balance.feedback(factory, duel_id, 1, slot, "off", "hard", "fair", False, "mostly streams")
    assert first["played"] and first["version"] == 1
    again = await balance.feedback(factory, duel_id, 1, slot, "fits", "fine", None, False, None)
    assert again["version"] == 2 and again["map"]["voices"] == 1 and again["map"]["negatives"] == 0
    with pytest.raises(DuelError) as stranger:
        await balance.feedback(factory, duel_id, 9, slot, "off", None, None, False, None)
    assert stranger.value.code == "NOT_PARTICIPANT"
    with pytest.raises(DuelError):
        await balance.feedback(factory, duel_id, 1, slot, "terrible", None, None, False, None)
    banned = next(e["slot"] for e in duel["pool"]["entries"] if e["banned_by"])
    preview = await balance.feedback(factory, duel_id, 2, banned, "off", None, None, False, None)
    assert not preview["played"]
    assert preview["map"]["q_bad"] == pytest.approx((2 + 0.25) / (4 + 0.25))

def test_a_hard_map_is_not_a_wrong_slot():
    assert balance.cost_of(0.5) == 0.0 and balance.cost_of(0.9) == pytest.approx(0.2)

async def _voices(factory, beatmap, labels, played=True):
    async with factory() as session:
        for number, label in enumerate(labels):
            player = Player(id=100 + number, osu_user_id=10_000 + number, osu_username=f"voice{number}")
            session.add(player)
            await session.flush()
            duel = Duel(id=f"v{beatmap}-{number}", mode="direct", state=states.COMPLETED, rules_version="duels-v1", seed="s")
            session.add(duel)
            await session.flush()
            session.add(DuelParticipant(duel_id=duel.id, player_id=player.id, osu_user_id=player.osu_user_id, side="A"))
            pool = DuelPool(duel_id=duel.id, manifest_hash="m", catalog_version="c", analyzer_version="f", profile_versions={})
            session.add(pool)
            await session.flush()
            session.add(DuelPoolEntry(pool_id=pool.id, slot="NM2", beatmap_id=beatmap, revision_hash="r", mods=["NF"], metrics_snapshot={},
                                      review_state="confident"))
            round_ = DuelRound(duel_id=duel.id, ordinal=1, slot="NM2", state="done")
            session.add(round_)
            await session.flush()
            if played:
                session.add(DuelAttempt(round_id=round_.id, attempt_no=1, expected_mods=["NF"], validity="valid"))
            await session.commit()
        session.add(DuelMapAssignment(beatmap_id=beatmap, slot="NM2", state="AUTO_ASSIGNED", confidence=0.9, margin=0.3, admitted=True))
        await session.commit()
    for number, label in enumerate(labels):
        await balance.feedback(factory, f"v{beatmap}-{number}", 100 + number, "NM2", label, None, None, False, None)

async def test_three_independent_wrong_slot_votes_ask_for_a_review(factory):
    await _voices(factory, 777, ["off", "off", "off"])
    async with factory() as session:
        reviews = (await session.scalars(select(DuelBalanceReview))).all()
        assert [(r.beatmap_id, r.reason) for r in reviews] == [(777, "feedback")]
        assert (await session.get(DuelMapAssignment, 777)).state == "AUTO_ASSIGNED"

async def test_five_played_votes_mostly_against_take_the_map_out_until_review(factory):
    await _voices(factory, 778, ["off", "off", "off", "off", "off", "fits"])
    async with factory() as session:
        assert (await session.get(DuelMapAssignment, 778)).state == "QUARANTINED"
        assert await session.scalar(select(func.count()).select_from(DuelBalanceReview).where(DuelBalanceReview.decision.is_(None))) == 1

async def test_reviewing_needs_the_role_and_is_audited(factory):
    await _voices(factory, 779, ["off", "off", "off"])
    with pytest.raises(balance.Forbidden):
        await balance.review_map(factory, 1, 779, "REJECT", "not aim at all")
    async with factory() as session:
        (await session.get(Player, 1)).telegram_id = 555
        await session.commit()
    with pytest.raises(balance.Forbidden):
        await balance.grant(factory, 2, 2, balance.BALANCE_REVIEWER, "please")
    await balance.grant(factory, 1, 2, balance.BALANCE_REVIEWER, "trusted reviewer", owners=(555,))
    after = await balance.review_map(factory, 2, 779, "REJECT", "streams, not jumps")
    assert after["state"] == "REJECTED"
    with pytest.raises(balance.Forbidden):
        await balance.operator_cancel(factory, 2, "x", "reviewers do not cancel")
    async with factory() as session:
        rows = (await session.scalars(select(DuelAudit).order_by(DuelAudit.id))).all()
        audit = [(row.action, row.role) for row in rows]
        assert audit == [("grant", "OWNER"), ("map_reject", "BALANCE_REVIEWER")]
        assert rows[1].before["state"] != rows[1].after["state"] == "REJECTED", "the journal keeps what the map was before"
        assert (await session.scalar(select(DuelBalanceReview.decision))) == "REJECT"
    assert [r["beatmap_id"] for r in await balance.open_reviews(factory, 2)] == []

async def test_a_role_goes_to_the_player_by_osu_id_or_name_and_can_be_taken_back(factory):
    assert await balance.find_player(factory, "102") == 2
    assert await balance.find_player(factory, "TWO") == 2
    assert await balance.find_player(factory, "2") is None
    assert await balance.find_player(factory, "nobody") is None
    async with factory() as session:
        (await session.get(Player, 1)).telegram_id = 555
        await session.commit()
    await balance.grant(factory, 1, 2, balance.BALANCE_REVIEWER, "", owners=(555,))
    with pytest.raises(balance.Forbidden):
        await balance.revoke(factory, 2, 2, balance.BALANCE_REVIEWER, "")
    await balance.revoke(factory, 1, 2, balance.BALANCE_REVIEWER, "granted by mistake", owners=(555,))
    async with factory() as session:
        assert await session.get(DuelRole, (2, balance.BALANCE_REVIEWER)) is None
    with pytest.raises(DuelError):
        await balance.revoke(factory, 1, 2, balance.BALANCE_REVIEWER, "", owners=(555,))

async def test_bad_feedback_raises_a_map_s_price_in_the_generator():
    plain = generator.Candidate(1, 1, "m", "NM1", 5.0, "confident", 0.9, "r")
    disliked = generator.Candidate(2, 2, "m", "NM1", 5.0, "confident", 0.9, "r", feedback=0.2)
    profile = {slot: generator.Skill(5.0, 0.4) for slot in generator.SLOTS if slot != "TB"}
    first, second = (generator.cost(generator.parts(c, profile, profile, 0.0)) for c in (plain, disliked))
    assert second == pytest.approx(first + 0.2)

async def test_an_operator_fixes_a_stuck_round_from_the_official_game(factory):
    from services.duels.verifier import Budget

    clock = Clock()
    duel_id = await _drafting(factory, clock)
    await _ban_all(factory, clock, duel_id)
    duel = await _view(factory, duel_id, 1)
    _, side = series.turn_side(len(duel["draft"]))
    await series.draft(factory, duel_id, next(p["player"] for p in duel["participants"] if p["side"] == side),
                       [e["slot"] for e in duel["pool"]["entries"] if e["slot"] != "TB" and not e["banned_by"]][0], clock.op(), now=clock.tick())
    await series.round_configured(factory, duel_id, clock.tick())
    for player in (1, 2):
        await series.ready(factory, duel_id, player, clock.op(), now=clock.tick())
    await series.round_started(factory, duel_id, 900, clock.tick())
    await series.round_finished(factory, duel_id, clock.tick())
    async with factory() as session:
        (await session.get(Player, 1)).telegram_id = 555
        entry = await session.scalar(select(DuelPoolEntry).join(DuelPool, DuelPool.id == DuelPoolEntry.pool_id).where(
            DuelPool.duel_id == duel_id, DuelPoolEntry.slot == (await _view(factory, duel_id, 1))["rounds"][0]["slot"]))
        await session.commit()
    start = (clock.now + timedelta(seconds=1)).isoformat() + "Z"

    class Osu:
        async def get_match(self, match_id, after=None, limit=None):
            if after is not None:
                return {"events": []}
            return {"events": [{"id": 1, "game": {"id": 42, "beatmap_id": int(entry.beatmap_id), "mode": "osu", "mode_int": 0,
                                                  "scoring_type": "scorev2", "team_type": "head-to-head", "start_time": start,
                                                  "end_time": start, "mods": list(entry.mods),
                                                  "scores": [{"user_id": 101, "score": 5}, {"user_id": 102, "score": 9}]}}]}

    with pytest.raises(balance.Forbidden):
        await balance.manual_finalize(factory, 2, duel_id, 900, 42, "stuck", Osu(), Budget(per_minute=100))
    fixed = await balance.manual_finalize(factory, 1, duel_id, 900, 42, "the history call timed out", Osu(), Budget(per_minute=100),
                                          owners=(555,))
    assert fixed["rounds"][0]["winner"] == 2 and fixed["state"] == states.SELECTING
    with pytest.raises(DuelError):
        await balance.manual_finalize(factory, 1, duel_id, 900, 42, "again", Osu(), Budget(per_minute=100), owners=(555,))

async def _labelled(factory, count):
    from tests.unit.test_duels_analysis import _features

    async with factory() as session:
        for number in range(count):
            digest = f"{number:032x}"
            kind, slot = ("stream", "NM2") if number % 2 else ("jumps", "NM1")
            session.add(DuelMapRevision(beatmap_id=5000 + number, beatmapset_id=9000 + number, hash=digest, status="ok", ranked_status="ranked"))
            session.add(DuelMapAssignment(beatmap_id=5000 + number, slot=slot, state="HUMAN_APPROVED", revision_hash=digest, admitted=True))
            session.add(DuelMapAnalysis(revision_hash=digest, mods="NF", engine_version="t", feature_version=FEATURE_VERSION, model_version="m",
                                        stars=5.0, features=_features(kind, number)))
        await session.commit()

async def test_a_shadow_model_is_trained_only_with_enough_new_labels_and_published_by_a_person(factory):
    clock = Clock()
    await _labelled(factory, 20)
    assert await training.maybe_train(factory, clock.now, steps=60) is None
    await _labelled_more(factory)
    made = await training.maybe_train(factory, clock.now, steps=60)
    assert made is not None and await training.maybe_train(factory, clock.now + timedelta(hours=1), steps=60) is None
    async with factory() as session:
        shadow = await session.get(DuelModelVersion, made)
        assert made == "kamerton-1" and shadow.state == "shadow" and shadow.report["test"]["count"] > 0 and list(shadow.report["years"]) == ["queue"] and shadow.labels == 60
        (await session.get(Player, 1)).telegram_id = 555
        await session.commit()
    assert await balance.published(factory) is None
    await balance.publish(factory, 1, made, "beats the old one on held-out maps", owners=(555,))
    assert (await balance.published(factory)).version == made
    with pytest.raises(DuelError):
        await balance.delete_model(factory, 1, made, "in use", owners=(555,))
    with pytest.raises(DuelError):
        await balance.rollback(factory, 1, "nothing earlier", owners=(555,))
    await balance.train(factory, "kamerton-9", {}, {}, 0, clock.now)
    with pytest.raises(balance.Forbidden):
        await balance.delete_model(factory, 2, "kamerton-9", "not mine to delete")
    await balance.delete_model(factory, 1, "kamerton-9", "", owners=(555,))
    async with factory() as session:
        assert await session.get(DuelModelVersion, "kamerton-9") is None
        assert await training._number(session) == 10
    await balance.train(factory, "kamerton-10", {"paths": []}, {}, 0, clock.now)
    with pytest.raises(DuelError):
        await balance.publish(factory, 1, "kamerton-10", "built for another analyser", owners=(555,))
    assert (await balance.working_model(factory)).version == made
    async with factory() as session:
        (await session.get(DuelModelVersion, made)).body = {**(await session.get(DuelModelVersion, made)).body, "features": "features-v0"}
        await session.commit()
    assert await balance.working_model(factory) is None

async def test_training_on_the_same_maps_makes_no_new_version(factory):
    await _labelled(factory, 20)
    assert not await training.unchanged(factory)
    first = await training.train(factory, steps=20)
    assert first[0] == "kamerton-1" and await training.unchanged(factory)
    assert await training.train(factory, steps=20) is None
    await _labelled_more(factory)
    assert not await training.unchanged(factory)
    assert (await training.train(factory, steps=20))[0] == "kamerton-2"

async def test_two_trainings_at_once_make_one_version(factory):
    import asyncio

    await _labelled(factory, 20)
    first, second = await asyncio.gather(training.train(factory, steps=20), training.train(factory, steps=20))
    assert sorted([first is None, second is None]) == [False, True]
    assert (first or second)[0] == "kamerton-1"

async def _labelled_more(factory):
    from tests.unit.test_duels_analysis import _features

    async with factory() as session:
        for number in range(20, 60):
            digest = f"{number:032x}"
            kind, slot = ("stream", "NM2") if number % 2 else ("jumps", "NM1")
            session.add(DuelMapRevision(beatmap_id=5000 + number, beatmapset_id=9000 + number, hash=digest, status="ok", ranked_status="ranked"))
            session.add(DuelMapAssignment(beatmap_id=5000 + number, slot=slot, state="HUMAN_APPROVED", revision_hash=digest, admitted=True))
            session.add(DuelMapAnalysis(revision_hash=digest, mods="NF", engine_version="t", feature_version=FEATURE_VERSION, model_version="m",
                                        stars=5.0, features=_features(kind, number)))
        await session.commit()

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel_catalog import DuelAssignmentLog, DuelMapAssignment, DuelMapRevision
from db.models.player import Player
from services.duels import catalog, generator
from services.duels.catalog import CatalogRefusal
from services.duels.generator import Candidate, Skill
from services.duels.rules import SLOTS

NOW = datetime(2026, 10, 23, 12, 0, 0)

@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add(Player(id=1, osu_user_id=11, osu_username="reviewer"))
        await session.commit()
        yield session
    await engine.dispose()

async def _refused(call) -> str:
    with pytest.raises(CatalogRefusal) as caught:
        await call
    return caught.value.code

async def test_a_proposal_only_assigns_a_slot_above_the_thresholds(session):
    assert (await catalog.propose(session, 1, "NM2", 0.85, 0.25, "m1", NOW))["state"] == "AUTO_ASSIGNED"
    assert (await catalog.propose(session, 2, "NM2", 0.65, 0.12, "m1", NOW))["slot"] == "NM2"
    low = await catalog.propose(session, 3, "NM2", 0.55, 0.30, "m1", NOW)
    assert (low["state"], low["slot"]) == ("UNASSIGNED", None)
    assert catalog.confidence_level(0.85, 0.25) == "confident"
    assert catalog.confidence_level(0.85, 0.15) == "uncertain"
    assert catalog.confidence_level(0.95, 0.05) is None
    assert await _refused(catalog.propose(session, 4, "NM5", 0.9, 0.5, "m1", NOW)) == "UNKNOWN_SLOT"

async def test_an_assigned_slot_never_moves_by_model_or_by_reviewer(session):
    await catalog.propose(session, 1, "NM1", 0.9, 0.3, "m1", NOW)
    assert await _refused(catalog.propose(session, 1, "DT1", 0.99, 0.9, "m2", NOW)) == "SLOT_FIXED"
    renewed = await catalog.propose(session, 1, "NM1", 0.7, 0.15, "m2", NOW)
    assert (renewed["slot"], renewed["version"], renewed["confidence"]) == ("NM1", "m2", 0.7)
    assert await _refused(catalog.review(session, 1, "APPROVE", 1, "looks like aim", slot="DT1", now=NOW)) == "SLOT_FIXED"
    approved = await catalog.review(session, 1, "APPROVE", 1, "aim map", now=NOW)
    assert approved["state"] == "HUMAN_APPROVED"
    assert await _refused(catalog.propose(session, 1, "NM1", 0.9, 0.3, "m3", NOW)) == "DECIDED_BY_HUMAN"

async def test_a_newly_published_model_may_move_only_an_unsure_slot(session):
    await catalog.propose(session, 1, "NM1", 0.65, 0.12, "kamerton-1", NOW)
    await catalog.propose(session, 2, "NM1", 0.9, 0.3, "kamerton-1", NOW)
    moved = await catalog.propose(session, 1, "NM3", 0.9, 0.3, "kamerton-2", NOW, unsure_again=True)
    assert (moved["slot"], moved["state"], moved["version"]) == ("NM3", "AUTO_ASSIGNED", "kamerton-2")
    assert await _refused(catalog.propose(session, 2, "NM3", 0.9, 0.3, "kamerton-2", NOW, unsure_again=True)) == "SLOT_FIXED"
    await catalog.propose(session, 3, "NM2", 0.65, 0.12, "kamerton-1", NOW)
    await catalog.review(session, 3, "APPROVE", 1, "streams", now=NOW)
    assert await _refused(catalog.propose(session, 3, "NM4", 0.9, 0.3, "kamerton-2", NOW, unsure_again=True)) == "DECIDED_BY_HUMAN"

async def test_a_reviewer_moves_a_wrong_slot_and_the_move_is_a_human_label(session):
    await catalog.propose(session, 1, "NM4", 0.65, 0.12, "kamerton-1", NOW)
    moved = await catalog.review(session, 1, "MOVE", 1, "it is alternate, not technical", slot="NM3", now=NOW)
    assert (moved["slot"], moved["state"]) == ("NM3", "HUMAN_APPROVED")
    assert await _refused(catalog.propose(session, 1, "NM4", 0.9, 0.3, "kamerton-2", NOW, unsure_again=True)) == "DECIDED_BY_HUMAN"
    assert await _refused(catalog.review(session, 1, "MOVE", 1, "no such slot", slot="XX1", now=NOW)) == "UNKNOWN_SLOT"
    await catalog.propose(session, 5, "NM1", 0.9, 0.3, "kamerton-1", NOW)
    spare = await catalog.review(session, 5, "MOVE", 1, "a free mod pick, not for duels yet", slot="FM2", now=NOW)
    assert (spare["slot"], spare["state"]) == ("FM2", "HUMAN_APPROVED")
    made, _ = await catalog.revision(session, 5, "e" * 32, beatmapset_id=50, ranked_status="ranked", now=NOW)
    made.status = "ok"
    await catalog.admit(session, 5, "e" * 32, True, now=NOW)
    assert catalog.playable(await session.get(DuelMapAssignment, 5), made) is None
    await catalog.propose(session, 2, "HD2", 0.9, 0.3, "kamerton-1", NOW)
    await catalog.review(session, 2, "REJECT", 1, "fits no slot", now=NOW)
    assert await _refused(catalog.review(session, 2, "MOVE", 1, "changed my mind", slot="NM1", now=NOW)) == "REJECTED_STAYS"

async def test_a_rejected_map_stays_out_and_every_decision_is_logged(session):
    await catalog.propose(session, 1, "HD2", 0.9, 0.3, "m1", NOW)
    await catalog.review(session, 1, "REJECT", 1, "reading is trivial", now=NOW)
    assert await _refused(catalog.review(session, 1, "APPROVE", 1, "changed my mind", now=NOW)) == "REJECTED_STAYS"
    assert await _refused(catalog.propose(session, 1, "HD2", 0.9, 0.3, "m2", NOW)) == "DECIDED_BY_HUMAN"
    unassigned = await catalog.propose(session, 2, "HR2", 0.5, 0.0, "m1", NOW)
    assert unassigned["slot"] is None
    labelled = await catalog.review(session, 2, "APPROVE", 1, "hand label", slot="HR2", now=NOW)
    assert (labelled["slot"], labelled["state"]) == ("HR2", "HUMAN_APPROVED")
    actions = (await session.scalars(select(DuelAssignmentLog.action).order_by(DuelAssignmentLog.id))).all()
    assert actions == ["propose", "reject", "propose", "approve"]

async def test_a_new_file_revision_withdraws_admission_until_analysed(session):
    await catalog.propose(session, 1, "DT2", 0.9, 0.3, "m1", NOW)
    made, fresh = await catalog.revision(session, 1, "a" * 32, beatmapset_id=10, ranked_status="ranked", mapper="Meg", now=NOW)
    assert fresh and made.status == "pending"
    await catalog.admit(session, 1, "a" * 32, True, now=NOW)
    row = await session.get(DuelMapAssignment, 1)
    assert row.admitted and catalog.playable(row, made) == "confident"
    same, again = await catalog.revision(session, 1, "a" * 32, now=NOW)
    assert not again and same.id == made.id
    changed, _ = await catalog.revision(session, 1, "b" * 32, beatmapset_id=10, ranked_status="ranked", now=NOW)
    row = await session.get(DuelMapAssignment, 1)
    assert (row.slot, row.revision_hash, row.admitted) == ("DT2", "b" * 32, False)
    assert catalog.playable(row, changed) is None
    await catalog.admit(session, 1, "b" * 32, False, "timing points out of order", now=NOW)
    assert (await session.get(DuelMapAssignment, 1)).state == "QUARANTINED"

async def test_only_ranked_admitted_assigned_maps_are_playable(session):
    await catalog.propose(session, 1, "NM3", 0.9, 0.3, "m1", NOW)
    loved, _ = await catalog.revision(session, 1, "c" * 32, ranked_status="loved", now=NOW)
    await catalog.admit(session, 1, "c" * 32, True, now=NOW)
    assert catalog.playable(await session.get(DuelMapAssignment, 1), loved) is None
    assert await session.scalar(select(func.count()).select_from(DuelMapRevision)) == 1

async def test_an_automatic_slot_plays_only_while_kamerton_is_trusted_there(session):
    await catalog.propose(session, 2, "NM3", 0.95, 0.5, "m1", NOW)
    ranked, _ = await catalog.revision(session, 2, "d" * 32, ranked_status="ranked", now=NOW)
    await catalog.admit(session, 2, "d" * 32, True, now=NOW)
    row = await session.get(DuelMapAssignment, 2)
    assert catalog.playable(row, ranked) == "confident"
    assert catalog.playable(row, ranked, frozenset({"NM3"})) == "confident"
    assert catalog.playable(row, ranked, frozenset({"NM1"})) is None
    await catalog.review(session, 2, "APPROVE", 1, "fits", now=NOW)
    assert catalog.playable(await session.get(DuelMapAssignment, 2), ranked, frozenset()) == "human"

async def test_locking_marks_maps_once(session):
    await catalog.propose(session, 1, "NM4", 0.9, 0.3, "m1", NOW)
    await catalog.lock(session, [1], NOW)
    await catalog.lock(session, [1], NOW)
    assert (await session.get(DuelMapAssignment, 1)).locked_at == NOW
    assert await session.scalar(select(func.count()).select_from(DuelAssignmentLog).where(DuelAssignmentLog.action == "lock")) == 1

def _profile(theta: float, u: float = 0.4) -> dict[str, Skill]:
    return {slot: Skill(theta, u) for slot in SLOTS if slot != "TB"}

def _catalogue(per_slot: int = 6, around: float = 5.0, step: float = 0.1, level: str = "confident") -> list[Candidate]:
    found = []
    number = 0
    for slot in SLOTS:
        for index in range(per_slot):
            number += 1
            found.append(Candidate(beatmap_id=number, beatmapset_id=number, mapper=f"mapper{number}", slot=slot,
                                   stars=around - step * per_slot / 2 + step * index, level=level, confidence=0.9,
                                   revision=f"{number:032x}"))
    return found

def test_targets_lean_toward_the_weaker_player_and_drop_for_uncertainty():
    aims = generator.targets(_profile(5.0), _profile(6.0))
    assert aims["NM1"] == pytest.approx(5.3) and aims["TB"] == pytest.approx(5.3)
    unsure = generator.targets(_profile(5.0, u=1.2), _profile(6.0))
    assert unsure["NM1"] == pytest.approx(5.05)

def test_reach_and_familiarity_follow_the_formulas():
    assert generator.reach(Skill(5.0, 0.2), 5.0) == pytest.approx(0.5)
    assert generator.reach(Skill(5.0, 0.2), 5.35) == pytest.approx(1 / (1 + 2.718281828459045))
    assert generator.familiarity(None, None) == 0.0
    assert generator.familiarity(14, None) == pytest.approx(0.5)
    assert generator.familiarity(None, 0) == pytest.approx(0.5)

def test_a_full_pool_has_nine_slots_nine_maps_and_a_stable_manifest():
    pool = generator.generate(_catalogue(), _profile(5.0), _profile(5.2), "seed-1")
    assert pool.status == "ready" and pool.level == 0
    assert [choice.candidate.slot for choice in pool.entries] == list(SLOTS)
    assert len({choice.candidate.beatmap_id for choice in pool.entries}) == 9
    again = generator.generate(_catalogue(), _profile(5.0), _profile(5.2), "seed-1")
    assert again.manifest_hash == pool.manifest_hash

def test_swapping_the_players_keeps_the_pool_and_its_price():
    first = generator.generate(_catalogue(), _profile(4.8), _profile(5.4), "seed-2")
    swapped = generator.generate(_catalogue(), _profile(5.4), _profile(4.8), "seed-2")
    assert swapped.manifest_hash == first.manifest_hash and swapped.cost == pytest.approx(first.cost)

def test_different_seeds_can_choose_among_the_best_pools():
    hashes = {generator.generate(_catalogue(), _profile(5.0), _profile(5.0), f"seed-{n}").manifest_hash for n in range(30)}
    assert len(hashes) > 1

def test_a_slot_with_no_maps_at_all_makes_the_pool_unavailable():
    maps = [candidate for candidate in _catalogue() if candidate.slot != "HR2"]
    pool = generator.generate(maps, _profile(5.0), _profile(5.0), "seed")
    assert (pool.status, pool.deficits) == ("unavailable", ["HR2"])

def test_fewer_than_five_maps_in_a_slots_corridor_leave_no_pool():
    maps = [candidate for candidate in _catalogue() if candidate.slot != "HR2" or candidate.beatmap_id % 6 < 4]
    pool = generator.generate(maps, _profile(5.0), _profile(5.0), "seed")
    assert (pool.status, pool.deficits) == ("unavailable", ["HR2"])
    assert generator.generate(_catalogue(per_slot=5), _profile(5.0), _profile(5.0), "seed").status == "ready"

def test_recently_played_maps_wait_until_the_first_relaxation():
    maps = [Candidate(c.beatmap_id, c.beatmapset_id, c.mapper, c.slot, c.stars, c.level, c.confidence, c.revision,
                      played_days=(2.0, None) if c.slot == "NM1" else (None, None)) for c in _catalogue()]
    pool = generator.generate(maps, _profile(5.0), _profile(5.0), "seed")
    assert (pool.status, pool.level) == ("ready", 1)

def test_one_mapper_everywhere_needs_the_second_relaxation():
    maps = [Candidate(c.beatmap_id, c.beatmapset_id, "Same", c.slot, c.stars, c.level, c.confidence, c.revision) for c in _catalogue()]
    pool = generator.generate(maps, _profile(5.0), _profile(5.0), "seed")
    assert (pool.status, pool.level) == ("ready", 2)
    assert pool.cost >= generator.MAPPER_EXTRA * 7

def test_far_maps_need_the_wide_corridor_and_both_players_consent():
    maps = _catalogue(around=5.6, step=0.01)
    pool = generator.generate(maps, _profile(5.0), _profile(5.0), "seed")
    assert (pool.status, pool.level, pool.needs_consent) == ("ready", 3, True)

def test_maps_beyond_every_corridor_turn_into_a_range_offer():
    pool = generator.generate(_catalogue(around=7.0, step=0.01), _profile(5.0), _profile(5.0), "seed")
    assert pool.status == "range_offered" and pool.level == 4
    assert pool.offer["NM1"][0] == pytest.approx(6.57, abs=0.02)
    assert set(pool.deficits) == set(SLOTS)

def test_uncertain_maps_stay_out_of_the_tiebreaker_and_at_most_two_in_a_pool():
    sure = [c for c in _catalogue() if c.slot in ("NM1", "NM2", "TB")]
    unsure = [Candidate(c.beatmap_id, c.beatmapset_id, c.mapper, c.slot, c.stars, "uncertain", 0.65, c.revision)
              for c in _catalogue() if c.slot not in ("NM1", "NM2", "TB")]
    pool = generator.generate(sure + unsure, _profile(5.0), _profile(5.0), "seed")
    assert pool.status == "unavailable" and set(pool.deficits) == set(SLOTS) - {"NM1", "NM2", "TB"}
    mixed = [c for c in _catalogue() if c.slot != "DT2"] + [
        Candidate(c.beatmap_id, c.beatmapset_id, c.mapper, c.slot, c.stars, "uncertain", 0.65, c.revision)
        for c in _catalogue() if c.slot == "DT2"]
    ready = generator.generate(mixed, _profile(5.0), _profile(5.0), "seed")
    assert ready.status == "ready"
    assert sum(choice.candidate.level == "uncertain" for choice in ready.entries) == 1
    only_unsure_tb = [c for c in _catalogue() if c.slot != "TB"] + [
        Candidate(c.beatmap_id, c.beatmapset_id, c.mapper, c.slot, c.stars, "uncertain", 0.65, c.revision)
        for c in _catalogue() if c.slot == "TB"]
    lonely = generator.generate(only_unsure_tb, _profile(5.0), _profile(5.0), "seed")
    assert (lonely.status, lonely.deficits) == ("unavailable", ["TB"])

def test_a_thousand_pools_never_break_the_hard_rules():
    maps = _catalogue(per_slot=12, step=0.05)
    for number in range(1000):
        pool = generator.generate(maps, _profile(4.8 + (number % 7) * 0.05), _profile(5.0 + (number % 5) * 0.05), f"s{number}")
        assert pool.status == "ready"
        assert [choice.candidate.slot for choice in pool.entries] == list(SLOTS)
        assert len({choice.candidate.beatmap_id for choice in pool.entries}) == 9


async def test_a_person_gives_a_map_a_secondary_slot_and_an_older_app_keeps_it(session):
    await catalog.propose(session, 7, "NM1", 0.7, 0.05, "kamerton-1", NOW)
    both = await catalog.review(session, 7, "APPROVE", 1, "jumps that also read well with HR", slot="NM1", now=NOW, secondary="HR1")
    assert (both["slot"], both["secondary"], both["state"]) == ("NM1", "HR1", "HUMAN_APPROVED")
    kept = await catalog.review(session, 7, "APPROVE", 1, "approved again by an older app", now=NOW)
    assert kept["secondary"] == "HR1", "a review without the field leaves the secondary slot alone"
    assert await _refused(catalog.review(session, 7, "APPROVE", 1, "same as primary", now=NOW, secondary="NM1")) == "UNKNOWN_SECONDARY"
    assert await _refused(catalog.review(session, 7, "APPROVE", 1, "no such slot", now=NOW, secondary="XX9")) == "UNKNOWN_SECONDARY"
    moved = await catalog.review(session, 7, "MOVE", 1, "really an HR map", slot="HR1", now=NOW)
    assert (moved["slot"], moved["secondary"]) == ("HR1", None), "the secondary that became primary is dropped"
    cleared = await catalog.review(session, 7, "APPROVE", 1, "one slot is enough", now=NOW, secondary=None)
    assert cleared["secondary"] is None

async def test_a_person_grades_the_map_in_its_slot_and_names_up_to_three_reasons(session):
    await catalog.propose(session, 8, "NM1", 0.7, 0.05, "kamerton-1", NOW)
    plain = await catalog.review(session, 8, "APPROVE", 1, "an older app says nothing of the tier", slot="NM1", now=NOW)
    assert (plain["tier"], plain["reasons"]) == ("fits", []), "a map approved without a tier fits its slot"
    graded = await catalog.review(session, 8, "APPROVE", 1, "textbook jumps", now=NOW, tier="exemplary", reasons=["jumps", "turns", "jumps"])
    assert (graded["tier"], graded["reasons"]) == ("exemplary", ["jumps", "turns"])
    kept = await catalog.review(session, 8, "APPROVE", 1, "approved again by an older app", now=NOW)
    assert (kept["tier"], kept["reasons"]) == ("exemplary", ["jumps", "turns"])
    assert await _refused(catalog.review(session, 8, "APPROVE", 1, "no such tier", now=NOW, tier="great")) == "UNKNOWN_TIER"
    assert await _refused(catalog.review(session, 8, "APPROVE", 1, "no such reason", now=NOW, reasons=["vibes"])) == "UNKNOWN_REASON"
    assert await _refused(catalog.review(session, 8, "APPROVE", 1, "too many", now=NOW,
                                         reasons=["jumps", "turns", "streams", "bursts"])) == "TOO_MANY_REASONS"
    moved = await catalog.review(session, 8, "MOVE", 1, "closer to tech", slot="NM4", now=NOW, tier="borderline", reasons=[])
    assert (moved["slot"], moved["tier"], moved["reasons"]) == ("NM4", "borderline", [])
    quarantined = await catalog.review(session, 8, "QUARANTINE", 1, "broken file", now=NOW)
    assert (quarantined["tier"], quarantined["reasons"]) == (None, [])

def test_a_secondary_slot_costs_more_than_the_same_map_in_its_own_slot():
    a = {slot: Skill(5.5, 0.4) for slot in SLOTS}
    own = Candidate(1, 1, "m", "HD2", 5.5, "human", None, "r")
    borrowed = Candidate(1, 1, "m", "HD2", 5.5, "human", None, "r", secondary=True)
    assert generator.cost(generator.parts(borrowed, a, a, 0.0)) == pytest.approx(generator.cost(generator.parts(own, a, a, 0.0)) + generator.SECONDARY_COST)

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
from db.models.player import Player
from db.models.user import User
from db.models.witness_session import WitnessSession
from utils import title_rules as rules
from utils.title_history import History, Play, Span, load_history, mod_set
from utils.title_progress import _CALCULATORS, _has_jackpot, refresh_user_titles, update_weekly_plays
from utils.titles import RARITY_ORDER, TITLE_REGISTRY

T0 = datetime(2026, 3, 2, 12, 0)

def P(n=0, *, at=None, minutes=1, **kw) -> Play:
    base = dict(
        table="attempt", beatmap_id=100 + n, played_at=T0 + timedelta(minutes=n * minutes) if at is None else at,
        passed=True, failed=False, rank="A", score=500_000, accuracy=96.0, max_combo=400, fc=False,
        mods=frozenset(), base_sr=4.0, sr=4.0, bpm=180.0, ar=9.0, length=150, status="ranked", ranked_date=None,
    )
    base.update(kw)
    if "mods" in kw and not isinstance(kw["mods"], frozenset):
        base["mods"] = mod_set(kw["mods"])
    if base["failed"]:
        base["passed"] = False
    return Play(**base)

def H(*plays, spans=(), zone=None, undated=(), bests=()) -> History:
    history = History(attempts=list(plays), undated=list(undated), bests=list(bests), spans=list(spans))
    if zone is not None:
        history.zone = ZoneInfo(zone)
    return history

class U:
    created_at = datetime(2026, 1, 1)

def test_every_title_has_a_rule_a_tier_and_words_in_both_languages():
    for code, td in TITLE_REGISTRY.items():
        assert code in _CALCULATORS, code
        assert td.rarity in RARITY_ORDER
        assert td.name and td.description and td.name_ru and td.description_ru, code
    assert {td.rarity for td in TITLE_REGISTRY.values()} == set(RARITY_ORDER)

def test_a_day_of_ranked_plays_counts_only_ranked_maps():
    assert rules.daily_report(H(*[P(n) for n in range(10)]), U) == 10
    assert rules.daily_report(H(*[P(n) for n in range(9)], P(9, status="loved")), U) == 9
    spread = [P(n, at=T0 + timedelta(days=n // 5, minutes=n)) for n in range(10)]
    assert rules.daily_report(H(*spread), U) == 5

def test_the_first_fc_after_signing_up_is_counted_from_the_day_the_account_was_made():
    before, after = P(0, at=datetime(2025, 12, 31), fc=True), P(1, fc=True)
    assert rules.approved_record(H(before), U) == 0
    assert rules.approved_record(H(before, after), U) == 1
    assert rules.approved_record(H(P(2, fc=True, mods="RX")), U) == 0

def test_fresh_ink_is_a_map_passed_within_thirty_days_of_its_ranking():
    assert rules.fresh_ink(H(P(0, ranked_date=T0 - timedelta(days=10))), U) == 1
    assert rules.fresh_ink(H(P(0, ranked_date=T0 - timedelta(days=40))), U) == 0
    assert rules.fresh_ink(H(P(0, ranked_date=T0 - timedelta(days=10), failed=True)), U) == 0
    assert rules.fresh_ink(H(P(0, ranked_date=T0 - timedelta(days=10), status="loved")), U) == 0

def test_ten_different_maps_make_a_hop_only_within_one_session():
    assert rules.map_hopper(H(*[P(n) for n in range(10)]), U) == 10
    assert rules.map_hopper(H(*[P(n, beatmap_id=1 + n % 3) for n in range(10)]), U) == 3
    broken = [P(n, at=T0 + timedelta(minutes=n * 5 + (60 if n >= 5 else 0))) for n in range(10)]
    assert rules.map_hopper(H(*broken), U) == 5

def test_a_run_of_good_grades_is_broken_by_a_worse_one_or_a_fail():
    ranks = ["A", "S", "A", "X", "B", "A", "A"]
    assert rules.routine_inspection(H(*[P(n, rank=r) for n, r in enumerate(ranks)]), U) == 4
    assert rules.routine_inspection(H(P(0), P(1, failed=True, rank="F"), P(2)), U) == 1
    assert rules.routine_inspection(H(P(0), P(1, rank="X", mods="RX"), P(2)), U) == 2

def test_improving_one_map_three_times_needs_the_same_mods_each_time():
    scores = [100, 200, 150, 300, 400]
    one = [P(n, beatmap_id=7, score=s) for n, s in enumerate(scores)]
    assert rules.revision_department(H(*one), U) == 3
    mixed = [P(n, beatmap_id=7, score=s, mods="HD" if n % 2 else "") for n, s in enumerate([100, 200, 300, 400])]
    assert rules.revision_department(H(*mixed), U) == 2

def test_the_first_map_of_the_day_is_the_first_one_of_the_players_own_day():
    late_utc = P(0, at=datetime(2026, 3, 2, 14, 0), rank="B")
    after_midnight_in_tokyo = P(1, at=datetime(2026, 3, 2, 15, 30), rank="S")
    assert rules.no_warmup(H(late_utc, after_midnight_in_tokyo), U) == 0
    assert rules.no_warmup(H(late_utc, after_midnight_in_tokyo, zone="Asia/Tokyo"), U) == 1
    assert rules.no_warmup(H(P(0, rank="B"), P(1, rank="X")), U) == 0

def test_a_long_map_must_be_passed_not_just_played():
    assert rules.long_shift(H(P(0, length=600)), U) == 1
    assert rules.long_shift(H(P(0, length=599)), U) == 0
    assert rules.long_shift(H(P(0, length=900, failed=True)), U) == 0
    assert rules.long_shift(H(bests=[P(0, table="best", length=700)]), U) == 1

def test_ten_maps_at_98_percent_in_a_row():
    good = [P(n, accuracy=98.5) for n in range(10)]
    assert rules.ministry_accuracy(H(*good), U) == 10
    assert rules.ministry_accuracy(H(*good[:4], P(4, accuracy=97.9), *good[5:]), U) == 5

def test_one_and_done_needs_a_first_attempt_that_is_an_fc_on_a_ranked_map_of_five_stars():
    first = P(0, fc=True, sr=5.4, beatmap_id=9)
    assert rules.one_and_done(H(first), U) == 1
    assert rules.one_and_done(H(P(0, failed=True, beatmap_id=9), P(1, fc=True, sr=5.4, beatmap_id=9)), U) == 0
    assert rules.one_and_done(H(first, undated=[P(0, played_at=None, beatmap_id=9)]), U) == 0
    assert rules.one_and_done(H(P(0, fc=True, sr=4.9)), U) == 0
    assert rules.one_and_done(H(P(0, fc=True, sr=6.0, status="loved")), U) == 0

def test_five_different_years_of_ranking_in_one_session():
    years = [P(n, ranked_date=datetime(2015 + n, 6, 1)) for n in range(5)]
    assert rules.time_traveller(H(*years), U) == 5
    assert rules.time_traveller(H(*years[:4], P(4, ranked_date=datetime(2018, 1, 1))), U) == 4

def test_a_passport_is_stamped_by_each_mod_on_a_five_star_map():
    plays = [P(0, sr=5.5), P(1, sr=5.5, mods="HD,HR"), P(2, sr=5.5, mods="NC"), P(3, sr=5.5, mods="FL")]
    assert rules.mod_passport(H(*plays), U) == 5
    assert rules.mod_passport(H(P(0, sr=4.9), P(1, sr=5.5, mods="HD")), U) == 1
    assert rules.mod_passport(H(P(0, sr=5.5, mods="RX")), U) == 0

def test_the_ministries_are_won_one_style_at_a_time():
    separate = [P(0, fc=True, sr=5.2), P(1, fc=True, sr=5.2, mods="HD"), P(2, fc=True, sr=5.2, mods="HR"), P(3, fc=True, sr=5.2, mods="NC,CL")]
    assert rules.four_ministries(H(*separate), U) == 4
    assert rules.four_ministries(H(P(0, fc=True, sr=5.2, mods="HD,HR"), P(1, fc=True, sr=5.2, mods="HD,DT")), U) == 0
    assert rules.all_seeing_eye(H(*separate), U) == 0
    assert rules.all_seeing_eye(H(*[P(n, fc=True, sr=7.3, mods=m) for n, m in enumerate(["", "HD", "HR", "DT"])]), U) == 4

def test_two_minutes_are_counted_as_played_so_dt_shortens_a_map():
    assert rules.two_minutes_hate(H(P(0, fc=True, sr=7.4, length=119)), U) == 1
    assert rules.two_minutes_hate(H(P(0, fc=True, sr=7.4, length=150)), U) == 0
    assert rules.two_minutes_hate(H(P(0, fc=True, sr=7.4, length=150, mods="DT")), U) == 1
    assert rules.two_minutes_hate(H(P(0, fc=True, sr=6.9, length=100)), U) == 0

def test_runs_count_only_the_plays_that_could_belong_to_them():
    hard = [P(n, fc=True, sr=5.5) for n in range(5)]
    easy_between = [hard[0], hard[1], P(9, fc=False, sr=3.0, at=T0 + timedelta(minutes=2, seconds=30)), *hard[2:]]
    assert rules.clean_sweep(H(*hard), U) == 5
    assert rules.clean_sweep(H(*easy_between), U) == 5
    assert rules.clean_sweep(H(hard[0], hard[1], P(9, failed=True, sr=5.5, at=T0 + timedelta(minutes=2, seconds=30)), *hard[2:]), U) == 3
    assert rules.untouchable(H(*[P(n, fc=True, sr=6.2) for n in range(10)]), U) == 10
    assert rules.model_citizen(H(*[P(n, rank="S" if n % 2 else "XH") for n in range(10)]), U) == 10
    assert rules.model_citizen(H(*[P(n, rank="S", status="loved") for n in range(10)]), U) == 0
    assert rules.idealist(H(*[P(n, rank="X", sr=5.1) for n in range(5)]), U) == 5
    assert rules.idealist(H(*[P(n, rank="X", sr=4.9) for n in range(5)]), U) == 0
    assert rules.idealist(H(*[P(n, rank="X", sr=5.1, mods="RX") for n in range(5)]), U) == 0

def test_three_ss_on_different_hard_maps_in_one_session():
    assert rules.inner_party(H(*[P(n, rank="X", sr=6.1) for n in range(3)]), U) == 3
    assert rules.inner_party(H(*[P(n, beatmap_id=5, rank="X", sr=6.1) for n in range(3)]), U) == 1

def test_a_perfect_week_is_seven_days_of_ss_in_the_players_own_days():
    week = [P(n, at=datetime(2026, 3, 2 + n, 12, 0), rank="X") for n in range(7)]
    assert rules.perfect_week(H(*week), U) == 7
    assert rules.perfect_week(H(*week[:3], *week[4:]), U) == 3
    assert rules.perfect_week(H(*week, zone="Pacific/Auckland"), U) == 7

def test_coincidences_are_found_among_every_play_on_file():
    assert rules.exact_combo(H(P(0, max_combo=1984)), U) == 1
    assert rules.exact_combo(H(P(0, max_combo=1985)), U) == 0
    assert rules.thoughtcrime(H(P(0, accuracy=99.9876, rank="S")), U) == 1
    assert rules.thoughtcrime(H(P(0, accuracy=100.0, rank="X")), U) == 0
    assert rules.thoughtcrime(H(P(0, accuracy=99.5, rank="S")), U) == 0
    same = [P(0, score=777_000, beatmap_id=1, ranked_date=datetime(2010, 1, 1)), P(1, score=777_000, beatmap_id=2, ranked_date=datetime(2012, 1, 1))]
    assert rules.dejavu(H(*same), U) == 1
    assert rules.memory_hole(H(*same), U) == 1
    near = [same[0], P(1, score=777_000, beatmap_id=2, ranked_date=datetime(2010, 6, 1))]
    assert rules.dejavu(H(*near), U) == 1 and rules.memory_hole(H(*near), U) == 0
    assert rules.dejavu(H(P(0, score=5, beatmap_id=1), P(1, score=5, beatmap_id=1)), U) == 0
    assert rules.dejavu(H(P(0, score=5, beatmap_id=1), P(1, score=5, beatmap_id=2, failed=True)), U) == 0

def test_an_improvement_counts_only_when_the_better_grade_came_after_the_worse_one():
    assert rules.reeducated(H(P(0, beatmap_id=4, rank="D"), P(1, beatmap_id=4, rank="A")), U) == 1
    assert rules.reeducated(H(P(0, beatmap_id=4, rank="A"), P(1, beatmap_id=4, rank="D")), U) == 0
    assert rules.reeducated(H(P(0, beatmap_id=4, rank="D"), P(1, beatmap_id=5, rank="A")), U) == 0
    assert rules.reeducated(H(P(1, beatmap_id=4, rank="X"), undated=[P(0, beatmap_id=4, rank="D", played_at=None)]), U) == 1
    assert rules.perfectionist(H(P(0, beatmap_id=4, rank="SH"), P(1, beatmap_id=4, rank="XH")), U) == 1
    assert rules.perfectionist(H(P(0, beatmap_id=4, rank="X"), P(1, beatmap_id=4, rank="S")), U) == 0

def test_failing_a_map_counts_ten_per_session_so_a_single_evening_cannot_farm_it():
    one_evening = [P(n, beatmap_id=3, failed=True) for n in range(30)]
    assert rules.total_failure(H(*one_evening), U) == 10
    days = [P(n + 40 * d, beatmap_id=3, failed=True, at=T0 + timedelta(days=d, minutes=n)) for d in range(3) for n in range(10)]
    assert rules.total_failure(H(*days), U) == 30

def test_persistence_is_passing_a_map_after_failing_it_ten_times():
    fails = [P(n, beatmap_id=8, failed=True) for n in range(10)]
    assert rules.persistent(H(*fails, P(10, beatmap_id=8)), U) == 10
    assert rules.persistent(H(*fails), U) == 0
    assert rules.persistent(H(*fails[:4], P(4, beatmap_id=8)), U) == 4

def test_a_days_playtime_is_the_larger_of_what_the_plays_suggest_and_what_witness_saw():
    sparse = [P(0), P(1, at=T0 + timedelta(minutes=40))]
    assert rules.clockwork(H(*sparse), U) < 60
    span = Span(T0 - timedelta(hours=1), T0 + timedelta(hours=4), 3 * 3600 + 30)
    assert rules.clockwork(H(*sparse, spans=[span]), U) == 180
    marathon = [P(n, at=T0 + timedelta(minutes=n * 20)) for n in range(10)]
    assert rules.clockwork(H(*marathon), U) >= 180

def test_witness_sessions_cut_play_sessions_where_the_client_was_closed():
    plays = [P(0, at=T0), P(1, at=T0 + timedelta(minutes=10)), P(2, at=T0 + timedelta(minutes=20)), P(3, at=T0 + timedelta(minutes=29))]
    assert len(H(*plays).sessions()) == 1
    spans = [Span(T0 - timedelta(minutes=5), T0 + timedelta(minutes=15), 600), Span(T0 + timedelta(minutes=18), T0 + timedelta(minutes=40), 600)]
    cut = H(*plays, spans=spans).sessions()
    assert [len(s) for s in cut] == [2, 2]

def test_fifty_different_ranked_maps_in_a_session():
    assert rules.assembly_line(H(*[P(n) for n in range(50)]), U) == 50
    assert rules.assembly_line(H(*[P(n, status="loved") for n in range(50)]), U) == 0
    assert rules.assembly_line(H(*[P(n, beatmap_id=1 + n % 10) for n in range(50)]), U) == 10

def test_speed_and_difficulty_titles_use_mod_adjusted_stars_and_bpm_and_skip_unranked_mods():
    assert rules.rapid_fire(H(P(0, fc=True, sr=6.2, bpm=170.0, mods="DT")), U) == 1
    assert rules.rapid_fire(H(P(0, fc=True, sr=5.9, bpm=250.0)), U) == 0
    assert rules.rapid_fire(H(P(0, fc=True, sr=6.5, bpm=250.0, mods="RX")), U) == 0
    assert rules.close_to_absolute(H(P(0, rank="X", sr=6.5, bpm=240.0)), U) == 1
    assert rules.close_to_absolute(H(P(0, rank="X", sr=6.4, bpm=240.0)), U) == 0
    assert rules.overdrive(H(P(0, fc=True, sr=7.1, bpm=200.0, mods="DT")), U) == 1
    assert rules.double_sentence(H(P(0, fc=True, sr=7.0, mods="HD,HR,CL")), U) == 1
    assert rules.heavy_hand(H(P(0, fc=True, base_sr=5.2, ar=10.0, mods="DT")), U) == 1
    assert rules.heavy_hand(H(P(0, fc=True, base_sr=5.2, ar=9.0)), U) == 0
    assert rules.double_digit(H(P(0, sr=10.2)), U) == 1

def test_a_jackpot_is_read_the_way_the_score_is_written():
    assert _has_jackpot(1_777_777)
    assert _has_jackpot(777_777)
    assert not _has_jackpot(7_777_770)
    assert not _has_jackpot(None)

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rules.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def _chat(factory, n=3):
    async with factory() as s:
        users = [User(chat_id=-100, telegram_id=n_ + 1, osu_username=f"p{n_}", osu_user_id=1000 + n_, play_count=0,
                      player_pp=100, accuracy=90.0, ranked_score=1000, play_time=3600, total_hits=1000) for n_ in range(n)]
        s.add_all(users)
        await s.commit()
        return [(u.id, u.player_id) for u in users]

def _row(pid, n, **kw):
    base = dict(player_id=pid, score_id=n, beatmap_id=10 + n, pp=0.0, passed=True, rank="A", status="ranked",
                played_at=T0 + timedelta(minutes=n))
    base.update(kw)
    return UserMapAttempt(**base)

async def test_history_reads_the_players_zone_and_the_sessions_witness_told(factory):
    (uid, pid), *_ = await _chat(factory)
    async with factory() as s:
        (await s.get(Player, pid)).time_zone = "Asia/Tokyo"
        s.add(WitnessSession(player_id=pid, started_at=T0, ended_at=T0 + timedelta(hours=2), play_seconds=5000, plays=9))
        s.add(_row(pid, 1))
        s.add(_row(pid, 2, played_at=None))
        s.add(UserBestScore(player_id=pid, score_id=3, beatmap_id=77, pp=1.0, rank="S"))
        await s.commit()
    async with factory() as s:
        history = await load_history(s, pid)
        assert (len(history.attempts), len(history.undated), len(history.bests), len(history.spans)) == (1, 1, 1, 1)
        assert history.day(datetime(2026, 3, 2, 16, 0)) == datetime(2026, 3, 3).date()
        assert await load_history(s, pid) is history

async def test_a_player_without_a_zone_is_read_in_utc(factory):
    (uid, pid), *_ = await _chat(factory)
    async with factory() as s:
        history = await load_history(s, pid)
    assert history.day(datetime(2026, 3, 2, 23, 59)) == datetime(2026, 3, 2).date()

async def test_witness_playtime_unlocks_clockwork_through_a_refresh(factory):
    (uid, pid), *_ = await _chat(factory)
    async with factory() as s:
        s.add(WitnessSession(player_id=pid, started_at=T0, ended_at=T0 + timedelta(hours=5), play_seconds=3 * 3600, plays=40))
        await s.commit()
    async with factory() as s:
        user = await s.get(User, uid)
        progress = {p["code"]: p for p in await refresh_user_titles(user, s)}
        await s.commit()
    assert progress["session_3h"]["unlocked"] and progress["session_3h"]["current"] == 180

async def test_leading_three_categories_of_the_chat_is_being_the_big_brother(factory):
    people = await _chat(factory, 3)
    (uid, pid) = people[0]
    async with factory() as s:
        user = await s.get(User, uid)
        user.player_pp, user.accuracy, user.play_count, user.total_hits = 500, 99.0, 9000, 0
        await s.commit()
    async with factory() as s:
        user = await s.get(User, uid)
        progress = {p["code"]: p for p in await refresh_user_titles(user, s)}
    assert progress["big_brother"]["current"] == 3 and progress["big_brother"]["unlocked"]
    other_uid = people[1][0]
    async with factory() as s:
        other = await s.get(User, other_uid)
        progress = {p["code"]: p for p in await refresh_user_titles(other, s)}
    assert progress["big_brother"]["current"] == 0

async def test_unranked_mods_do_not_unlock_the_titles_that_are_read_from_the_database(factory):
    (uid, pid), *_ = await _chat(factory)
    async with factory() as s:
        s.add(_row(pid, 1, rank="X", star_rating=7.5, eff_sr=7.5, mods="RX"))
        await s.commit()
    async with factory() as s:
        user = await s.get(User, uid)
        progress = {p["code"]: p for p in await refresh_user_titles(user, s)}
    assert not progress["ss_7star"]["unlocked"]
    async with factory() as s:
        s.add(_row(pid, 2, rank="X", star_rating=7.5, eff_sr=7.5, mods="HD,CL"))
        await s.commit()
    async with factory() as s:
        user = await s.get(User, uid)
        progress = {p["code"]: p for p in await refresh_user_titles(user, s)}
    assert progress["ss_7star"]["unlocked"]

async def test_wardrobe_counts_each_mod_once_however_the_row_writes_them(factory):
    (uid, pid), *_ = await _chat(factory)
    async with factory() as s:
        s.add_all([_row(pid, n, mods=m) for n, m in enumerate(["HDDT", "HD,DT", "HR", "FL", "EZ", "HDHR"], start=1)])
        await s.commit()
    async with factory() as s:
        user = await s.get(User, uid)
        progress = {p["code"]: p for p in await refresh_user_titles(user, s)}
    assert progress["masks_5"]["current"] == 5

def test_the_weekly_counter_keeps_what_the_last_window_gained_before_it_starts_over():
    class Person:
        play_count = 1400
        playcount_week_anchor = 1000
        playcount_week_anchor_at = datetime(2020, 1, 1)
        week_plays_best = 100
    person = Person()
    update_weekly_plays(person)
    assert person.week_plays_best == 400
    assert person.playcount_week_anchor == 1400

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.player import Player
from services.duels.generator import Candidate
from services.story import chapters, engine, service, trials
from services.story import http as story_http
from services.story.chapters import ChapterError, Goal

OPENED = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)

TINY = """
id = "tiny"
start = "hello"
title = { ru = "Проба", en = "Trial" }

[speakers.clerk]
ru = "Писарь"
en = "Clerk"

[[steps]]
id = "hello"
kind = "scene"
next = "play"
lines = [{ who = "clerk", ru = "Здравствуйте", en = "Hello" }]

[[steps]]
id = "play"
kind = "task"
slot = "NM2"
then = "after"

[[steps]]
id = "after"
kind = "choice"
lines = [
  { who = "clerk", when = "play.fc", ru = "Чисто", en = "Clean" },
  { who = "clerk", when = "!play.fc", ru = "Сойдёт", en = "Fine" },
]
options = [
  { id = "left", flag = "went_left", next = "bye", ru = "Налево", en = "Left" },
  { id = "right", flag = "went_right", next = "bye", ru = "Направо", en = "Right" },
]

[[steps]]
id = "bye"
kind = "end"
lines = [{ who = "clerk", when = "went_left", ru = "Слева", en = "On the left" }]
"""


def _candidate(beatmap_id: int, slot: str, stars: float) -> Candidate:
    return Candidate(beatmap_id=beatmap_id, beatmapset_id=beatmap_id, mapper="m", slot=slot, stars=stars, level="HUMAN_APPROVED",
                     confidence=None, revision="r")


def _score(beatmap_id: int, *, at: datetime = OPENED + timedelta(minutes=5), accuracy: float = 0.97, perfect: bool = False,
           rank: str = "A", passed: bool = True, mods=()) -> dict:
    return {"beatmap": {"id": beatmap_id}, "ended_at": at.isoformat().replace("+00:00", "Z"), "accuracy": accuracy,
            "is_perfect_combo": perfect, "rank": rank, "passed": passed, "mods": [{"acronym": mod} for mod in mods]}


def test_the_written_prologue_loads_and_every_path_ends():
    found = chapters.load()
    prologue = found["prologue"]
    assert prologue.start == "welcome"
    assert {"prologue:by_the_book", "prologue:kept_a_copy", "prologue:check.fc"} <= prologue.flags()
    ends = [step for step in prologue.steps.values() if step.kind == "end"]
    assert len(ends) == 2


@pytest.mark.parametrize("broken, said", [
    (TINY.replace('next = "play"', 'next = "nowhere"'), "not a step"),
    (TINY.replace(', en = "Hello"', ""), "no en text"),
    (TINY.replace('when = "went_left"', 'when = "went_up"'), "never set"),
    (TINY.replace('slot = "NM2"', 'slot = "NM9"'), "not a slot"),
    (TINY.replace('next = "bye", ru = "Направо"', 'next = "hello", ru = "Направо"'), "loops back"),
    (TINY.replace('who = "clerk", ru = "Здравствуйте"', 'who = "ghost", ru = "Здравствуйте"'), "not among the speakers"),
    (TINY.replace('when = "play.fc"', 'when = "play.great"'), "not one of"),
])
def test_a_broken_chapter_is_refused_with_the_reason(broken, said):
    with pytest.raises(ChapterError, match=said):
        chapters.parse(broken)


def test_a_step_nothing_leads_to_is_refused():
    lonely = TINY + '\n[[steps]]\nid = "lost"\nkind = "end"\n'
    with pytest.raises(ChapterError, match="never reached"):
        chapters.parse(lonely)


def test_a_choice_needs_at_least_two_options():
    single = TINY.replace('  { id = "right", flag = "went_right", next = "bye", ru = "Направо", en = "Right" },\n', "")
    with pytest.raises(ChapterError, match="2 or 3 options"):
        chapters.parse(single)


def test_a_chapter_cannot_lead_to_one_that_is_not_written(tmp_path):
    (tmp_path / "tiny.toml").write_text(TINY.replace('kind = "end"', 'kind = "end"\nnext_chapter = "sequel"'), encoding="utf-8")
    with pytest.raises(ChapterError, match="not written"):
        chapters.load(tmp_path)


def test_the_story_walks_from_scene_to_task_to_choice_and_remembers_the_choice():
    tiny = chapters.parse(TINY)
    progress = engine.begin(tiny)
    assert engine.shown(tiny, progress, "ru")["lines"][0]["text"] == "Здравствуйте"
    engine.go_on(tiny, progress, "hello")
    assert engine.shown(tiny, progress, "en")["task"]["slot"] == "NM2"
    engine.settle(tiny, progress, {"fc"})
    shown = engine.shown(tiny, progress, "en")
    assert [line["text"] for line in shown["lines"]] == ["Clean"]
    assert [option["id"] for option in shown["options"]] == ["left", "right"]
    engine.choose(tiny, progress, "left", "after")
    assert "tiny:went_left" in progress.flags and "tiny:play.passed" in progress.flags
    assert [line["text"] for line in engine.shown(tiny, progress, "ru")["lines"]] == ["Слева"]
    engine.close(tiny, progress, None, "bye")
    assert progress.done == ["tiny"]


def test_a_late_or_wrong_move_is_refused_instead_of_skipping_ahead():
    tiny = chapters.parse(TINY)
    progress = engine.begin(tiny)
    with pytest.raises(engine.StoryError) as moved:
        engine.go_on(tiny, progress, "after")
    assert moved.value.code == "STEP_MOVED"
    with pytest.raises(engine.StoryError) as wrong:
        engine.choose(tiny, progress, "left")
    assert wrong.value.code == "NOT_A_CHOICE"


def test_a_score_counts_only_on_the_map_after_the_task_opened_and_without_relax():
    goal = Goal()
    assert trials.meets(goal, _score(7), 7, OPENED) == {"passed"}
    assert trials.meets(goal, _score(8), 7, OPENED) is None
    assert trials.meets(goal, _score(7, at=OPENED - timedelta(minutes=1)), 7, OPENED) is None
    assert trials.meets(goal, _score(7, passed=False, rank="F"), 7, OPENED) is None
    assert trials.meets(goal, _score(7, mods=("RX",)), 7, OPENED) is None
    assert trials.meets(goal, _score(7, perfect=True, rank="X", accuracy=1.0), 7, OPENED) == {"passed", "fc", "ss"}


def test_a_goal_asks_for_accuracy_a_full_combo_and_mods():
    picky = Goal(accuracy=98.0, full_combo=True, mods=frozenset({"HD"}))
    assert trials.meets(picky, _score(7, accuracy=0.985, perfect=True, mods=("HD",)), 7, OPENED) == {"passed", "fc"}
    assert trials.meets(picky, _score(7, accuracy=0.975, perfect=True, mods=("HD",)), 7, OPENED) is None
    assert trials.meets(picky, _score(7, accuracy=0.99, perfect=False, mods=("HD",)), 7, OPENED) is None
    assert trials.meets(picky, _score(7, accuracy=0.99, perfect=True), 7, OPENED) is None


def test_the_best_score_wins_and_every_play_on_the_map_is_a_try():
    scores = [_score(7, passed=False, rank="F"), _score(7), _score(7, perfect=True), _score(9)]
    found, tried = trials.best(Goal(), scores, 7, OPENED)
    assert found == {"passed", "fc"} and tried == 3


def test_a_map_is_picked_near_the_aim_in_its_slot_and_the_same_way_again():
    candidates = [_candidate(1, "NM1", 3.0), _candidate(2, "NM1", 4.1), _candidate(3, "NM1", 4.0), _candidate(4, "NM2", 4.0),
                  _candidate(5, "NM1", 3.9), _candidate(6, "NM1", 6.0)]
    first = trials.pick(candidates, "NM1", 4.0, "seed")
    assert first.beatmap_id in {2, 3, 5}
    assert trials.pick(candidates, "NM1", 4.0, "seed") == first
    assert trials.pick(candidates, "NM1", 4.0, "seed", avoid=(2, 3, 5)).beatmap_id in {1, 6}
    assert trials.pick(candidates, "DT1", 4.0, "seed") is None
    assert trials.pick(candidates, "NM1", 4.0, "seed", under=3.5).beatmap_id == 1
    assert trials.pick(candidates, "NM1", 4.0, "seed", under=2.0) is None
    assert trials.aim(5.0, 0.8, False) == pytest.approx(4.2)
    assert trials.aim(5.0, 0.8, True) == pytest.approx(3.7)


class FakeOsu:
    def __init__(self):
        self.recent: list[dict] = []
        self.on_map: list[dict] = []

    async def get_user_recent_scores(self, user_id, limit=1, oauth_token=None, mode=None):
        return list(self.recent)

    async def get_user_beatmap_scores(self, beatmap_id, user_id, oauth_token=None, mode=None):
        return list(self.on_map)


@pytest.fixture
async def story(tmp_path, monkeypatch):
    sqlite = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'story.db'}")
    async with sqlite.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(sqlite, expire_on_commit=False)
    async with factory() as session:
        session.add(Player(id=1, osu_user_id=555, telegram_id=77, osu_username="Citizen"))
        await session.commit()
    folder = tmp_path / "story"
    folder.mkdir()
    (folder / "tiny.toml").write_text(TINY, encoding="utf-8")
    service.forget()
    monkeypatch.setattr(service, "FIRST", "tiny")
    monkeypatch.setattr(service, "chapters", lambda: chapters.load(folder))

    async def level(session, player_id, slot):
        return 5.0

    async def catalogue(session):
        return [_candidate(11, "NM2", 5.0), _candidate(12, "NM2", 4.9), _candidate(14, "NM2", 5.1), _candidate(13, "NM2", 3.0)]

    monkeypatch.setattr(service, "_level", level)
    monkeypatch.setattr(service, "_catalogue", catalogue)
    yield factory
    service.forget()
    await sqlite.dispose()


async def test_the_story_is_kept_per_player_and_a_task_is_checked_against_osu(story):
    osu = FakeOsu()
    first = await service.state(story, 1, "ru")
    assert first["step"] == "hello" and first["chapter"] == "tiny"
    at_task = await service.next_step(story, 1, "hello", "ru")
    given = at_task["task"]["map"]
    assert at_task["kind"] == "task" and given["beatmap_id"] in {11, 12, 14} and given["tries"] == 0
    opened = trials.moment(given["opened_at"])
    osu.recent = [_score(given["beatmap_id"], at=opened + timedelta(minutes=2), perfect=True)]
    clock = iter([100.0, 200.0])
    checked = await service.check(story, osu, 1, "play", "en", clock=lambda: next(clock))
    assert checked["checked"] == {"done": True, "outcomes": ["fc", "passed"]}
    assert checked["kind"] == "choice" and [line["text"] for line in checked["lines"]] == ["Clean"]
    again = await service.state(story, 1, "en")
    assert again["step"] == "after"
    chosen = await service.pick_option(story, 1, "after", "left", "ru")
    assert chosen["kind"] == "end" and chosen["lines"][0]["text"] == "Слева"
    closed = await service.finish_chapter(story, 1, "bye", "ru")
    assert closed["done"] == ["tiny"]


async def test_after_three_tries_the_task_moves_to_an_easier_map_and_checks_are_spaced(story):
    osu = FakeOsu()
    await service.next_step(story, 1, "hello", "ru")
    given = (await service.state(story, 1, "ru"))["task"]["map"]
    opened = trials.moment(given["opened_at"])
    osu.recent = [_score(given["beatmap_id"], at=opened + timedelta(minutes=k), passed=False, rank="F") for k in range(1, 4)]
    clock = iter([100.0, 105.0, 130.0])
    eased = await service.check(story, osu, 1, "play", "ru", clock=lambda: next(clock))
    assert eased["checked"] == {"done": False, "tries": 3, "eased": True}
    moved = eased["task"]["map"]
    assert moved["beatmap_id"] != given["beatmap_id"] and moved["eased"] is True and moved["tries"] == 0
    assert moved["stars"] < given["stars"]
    with pytest.raises(engine.StoryError) as soon:
        await service.check(story, osu, 1, "play", "ru", clock=lambda: next(clock))
    assert soon.value.code == "TOO_SOON"
    osu.recent = [_score(given["beatmap_id"], at=opened + timedelta(minutes=9), passed=False, rank="F") for _ in range(5)]
    later = await service.check(story, osu, 1, "play", "ru", clock=lambda: next(clock))
    assert later["checked"]["eased"] is False and later["task"]["map"]["beatmap_id"] == moved["beatmap_id"]


async def test_the_story_routes_answer_the_linked_player_and_refuse_a_stale_step(story):
    async def guard(request):
        return None

    async def who(request):
        return SimpleNamespace(player_id=1)

    app = web.Application()
    app.add_routes(story_http.routes(guard, who, lambda: story, lambda: FakeOsu()))
    async with TestClient(TestServer(app)) as client:
        state = await client.get("/render/story?lang=en")
        assert state.status == 200 and (await state.json())["lines"][0]["text"] == "Hello"
        stale = await client.post("/render/story/next", json={"step": "after"})
        assert stale.status == 409 and (await stale.json())["error"] == "STEP_MOVED"
        moved = await client.post("/render/story/next", json={"step": "hello"})
        assert (await moved.json())["kind"] == "task"
        wrong = await client.post("/render/story/choose", json={"step": "play", "option": "left"})
        assert wrong.status == 409 and (await wrong.json())["error"] == "NOT_A_CHOICE"


async def test_with_no_easier_map_the_task_stays_and_is_not_eased_again(story, monkeypatch):
    async def lonely(session):
        return [_candidate(21, "NM2", 4.0)]

    monkeypatch.setattr(service, "_catalogue", lonely)
    osu = FakeOsu()
    await service.next_step(story, 1, "hello", "ru")
    given = (await service.state(story, 1, "ru"))["task"]["map"]
    opened = trials.moment(given["opened_at"])
    osu.recent = [_score(21, at=opened + timedelta(minutes=k), passed=False, rank="F") for k in range(1, 4)]
    checked = await service.check(story, osu, 1, "play", "ru", clock=lambda: 100.0)
    assert checked["checked"] == {"done": False, "tries": 3, "eased": False}
    assert checked["task"]["map"]["beatmap_id"] == 21 and checked["task"]["map"]["eased"] is True

import asyncio

import pytest
from sqlalchemy import select

from db.models.duel import DuelModelVersion
from services.duels import kamerton, training
from tests.unit.test_duels_balance import _labelled
from tests.unit.test_duels_training import factory


@pytest.fixture(autouse=True)
def jobs(monkeypatch):
    monkeypatch.setattr(training, "jobs", {})


async def test_progress_matches_actual_fits_and_does_not_change_model(factory):
    await _labelled(factory, 20)
    examples = await kamerton.examples(factory)
    steps = []
    with_progress = await asyncio.to_thread(kamerton.StackedModel.fit, examples, steps=20, progress=steps.append)
    without_progress = await asyncio.to_thread(kamerton.StackedModel.fit, examples, steps=20)
    assert with_progress.dump() == without_progress.dump()
    assert steps == sorted(steps) and steps[-1] == (kamerton.STACK_FOLDS + 2) * 20
    assert len(steps) > 10

    events, status = [], {}
    def report(**change):
        status.update(change)
        events.append(dict(status))
    version, _ = await training.train(factory, steps=20, report=report)
    phases = {event["phase"] for event in events}
    assert phases == {"gathering", "validation", "fitting", "saving"}
    progress = [event["completed"] for event in events if "completed" in event]
    assert progress == sorted(progress)
    assert status["maps"] == 20 and status["checks_done"] == status["checks_total"]
    assert status["completed"] == status["total"] - 1
    async with factory() as session:
        saved = await session.get(DuelModelVersion, version)
        expected = without_progress.dump()
        expected["version"] = version
        assert saved.body == expected


async def test_job_survives_a_lost_observer_and_is_recovered_by_latest_snapshot(factory, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def train(factory, steps=400, now=None, report=None):
        report(phase="fitting", completed=35, total=100, maps=20)
        entered.set()
        await release.wait()
        return "kamerton-42", {}
    monkeypatch.setattr(training, "train", train)
    name = training.open_job()
    task = asyncio.create_task(training.run_job(factory, name))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        seen = training.snapshot()
        assert seen["job"] == name and seen["state"] == "running"
        assert seen["completed"] == 35 and seen["elapsed_seconds"] >= 0
        seen["completed"] = 99
        assert training.snapshot(name)["completed"] == 35, "a snapshot is not the live job"
        # No observer is required for the server task to finish.
        release.set()
        await asyncio.wait_for(task, 2)
        done = training.snapshot()
        assert done["job"] == name and done["state"] == "done"
        assert done["completed"] == done["total"] == 100
        assert done["revision"] > seen["revision"] and done["version"] == "kamerton-42"
        assert training.snapshot("unknown") is None
    finally:
        release.set()
        await task


async def test_cancelled_server_task_is_interrupted_not_stuck_running(factory, monkeypatch):
    entered = asyncio.Event()
    async def train(factory, steps=400, now=None, report=None):
        report(phase="fitting", completed=5, total=100)
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(training, "train", train)
    name = training.open_job()
    task = asyncio.create_task(training.run_job(factory, name))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    found = training.snapshot()
    assert found["state"] == "interrupted" and found["finished_at"]
    assert found["completed"] == 5 and training.running_job() is None

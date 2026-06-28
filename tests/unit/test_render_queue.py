"""FIFO admission for the danser render slot (utils/osu/danser_renderer)."""

import asyncio

import pytest

from utils.osu import danser_renderer as dr


async def test_render_slot_reports_queue_position(monkeypatch):
    """With concurrency 1, the first job runs immediately (no on_queue call);
    a second concurrent job is told it's #1 in line before it blocks."""
    monkeypatch.setattr(dr, "_render_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(dr, "RENDER_CONCURRENCY", 1)
    monkeypatch.setattr(dr, "_inflight", 0)

    positions: list[int] = []

    async def on_queue(pos: int):
        positions.append(pos)

    started = asyncio.Event()
    release = asyncio.Event()

    async def job(oq):
        async with dr._render_slot(oq):
            started.set()
            await release.wait()

    first = asyncio.create_task(job(None))
    await started.wait()                      # first holds the only slot

    second = asyncio.create_task(job(on_queue))
    await asyncio.sleep(0.02)                  # let it queue behind `first`
    assert positions == [1]                    # told it's #1 in line

    release.set()
    await asyncio.gather(first, second)
    assert dr._inflight == 0                    # accounting balances out


async def test_render_slot_rejects_when_queue_full(monkeypatch):
    monkeypatch.setattr(dr, "_render_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(dr, "RENDER_CONCURRENCY", 1)
    monkeypatch.setattr(dr, "_inflight", dr._MAX_QUEUE)

    with pytest.raises(dr.RenderQueueFullError):
        async with dr._render_slot(None):
            pass

    assert dr._inflight == dr._MAX_QUEUE        # rejected entry didn't leak a count

#!/usr/bin/env python3
"""Render replays for the bot, on this machine.

Run from a checkout of this repo on whichever machine should do the rendering —
the point of it is a laptop that is several times the server the bot lives on.
It needs the engine built (`cd dossier && cargo build --release`), the osu! API
credentials the bot uses, and the shared secret:

    RENDER_WORKER_TOKEN=... OSU_CLIENT_ID=... OSU_CLIENT_SECRET=... \
        ./venv/bin/python scripts/render_worker.py --server https://example.org

It pulls rather than listens. Nothing has to be reachable from outside, no port
is opened, no address has to stay put — which matters because the machine this
is for sits behind a home router and moves. A worker that is off simply stops
claiming, and the bot renders on its own host a few seconds later.

How hard it works is not this file's decision. `services.dossier.machine` reads
the battery, the energy mode, whether anyone is at the keyboard and whether the
machine is hot, and answers with thread counts — or with a refusal, which is
respected by not claiming anything at all.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402

from services.dossier import machine  # noqa: E402
from services.dossier import maps, runner  # noqa: E402
from utils.logger import get_logger  # noqa: E402

logger = get_logger("render_worker")

# Long enough that an idle worker is not a busy one, short enough that somebody
# waiting on a render does not notice the difference.
POLL_SECONDS = 3.0
# Well inside the server's lease. A render says nothing for long stretches while
# it encodes, so silence has to be reported deliberately rather than inferred.
HEARTBEAT_SECONDS = 20.0


class Server:
    """The bot's render endpoints, as this worker sees them."""

    def __init__(self, base: str, token: str, name: str) -> None:
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "X-Render-Worker": name}
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=self.headers)
        return self

    async def __aexit__(self, *_):
        await self.session.close()

    async def claim(self) -> dict | None:
        async with self.session.post(f"{self.base}/render/claim") as reply:
            if reply.status == 204:
                return None
            if reply.status == 401:
                raise SystemExit("the server rejected the token")
            reply.raise_for_status()
            return await reply.json()

    async def fetch_replay(self, job_id: str, into: str) -> None:
        async with self.session.get(f"{self.base}/render/job/{job_id}/replay") as reply:
            reply.raise_for_status()
            with open(into, "wb") as handle:
                async for chunk in reply.content.iter_chunked(1 << 16):
                    handle.write(chunk)

    async def fetch_asset(self, job_id: str, name: str, into: str) -> None:
        async with self.session.get(
            f"{self.base}/render/job/{job_id}/file/{name}"
        ) as reply:
            reply.raise_for_status()
            with open(into, "wb") as handle:
                async for chunk in reply.content.iter_chunked(1 << 16):
                    handle.write(chunk)

    async def heartbeat(self, job_id: str, progress: dict | None = None) -> bool:
        """False means the job stopped being ours and the render should stop."""
        try:
            async with self.session.post(
                f"{self.base}/render/job/{job_id}/heartbeat",
                json={"progress": progress},
            ) as reply:
                return reply.status == 200
        except aiohttp.ClientError as exc:
            # A blip is not a lost job: the lease outlives several of these, and
            # abandoning a half-finished render over one failed request would
            # throw away minutes of work.
            logger.warning("heartbeat failed: %s", exc)
            return True

    async def deliver(self, job_id: str, path: str, meta: dict) -> None:
        with open(path, "rb") as handle:
            async with self.session.post(
                f"{self.base}/render/job/{job_id}/result",
                data=handle,
                headers={"X-Render-Meta": json.dumps(meta),
                         "Content-Type": "application/octet-stream"},
            ) as reply:
                reply.raise_for_status()

    async def give_back(self, job_id: str, reason: str) -> None:
        try:
            async with self.session.post(
                f"{self.base}/render/job/{job_id}/give-back", json={"reason": reason}
            ):
                pass
        except aiohttp.ClientError as exc:
            # The lease expiring does the same thing a moment later, so this is
            # a courtesy rather than the mechanism.
            logger.warning("could not hand job %s back: %s", job_id, exc)


async def _render(server: Server, job: dict, capacity, api) -> None:
    """Do one job, or hand it back saying why."""
    job_id = job["id"]
    workdir = tempfile.mkdtemp(prefix="render-worker-")
    replay = os.path.join(workdir, "replay.osr")
    out = os.path.join(workdir, "render.mp4")
    alive = True

    async def on_progress(told) -> None:
        nonlocal alive
        if not alive:
            return
        alive = await server.heartbeat(job_id, {
            "done": told.done, "total": told.total, "fps": told.fps,
            "seconds_left": told.seconds_left,
            "clip": list(told.clip) if told.clip else None,
        })

    async def keep_alive() -> None:
        """Say we are here even while the engine has nothing to report."""
        nonlocal alive
        while alive:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if alive:
                alive = await server.heartbeat(job_id)

    try:
        await server.fetch_replay(job_id, replay)

        # The scoreboard's pictures, and the player's own. The job refers to
        # them as `{{a0}}` and such; each is fetched by that name and the name
        # is swapped for where it landed here. Names are the server's own —
        # checked all the same, since they end up in a filename.
        here = {}
        for name in job.get("assets") or []:
            if not name.isalnum():
                logger.warning("job %s offered an odd asset name %r", job_id, name)
                continue
            landed = os.path.join(workdir, f"{name}.png")
            await server.fetch_asset(job_id, name, landed)
            here[name] = landed

        def localise(text):
            for name, path in here.items():
                text = text.replace("{{%s}}" % name, path)
            # Anything still templated names a file the server did not send.
            # Left as an empty column rather than a path that does not exist:
            # the engine draws an empty frame, which is honest.
            return re.sub(r"\{\{a\d+\}\}", "", text)

        settings = job["settings"]
        board = settings.get("leaderboard")
        board = localise(board) if board else None
        mine = tuple(localise(p) or None for p in (settings.get("my_pictures") or ["", ""]))

        header = await runner.inspect(replay)
        # The replay names its map by hash and nothing else, so the worker
        # fetches it the same way the bot would — which is why nothing but the
        # `.osr` has to cross the network.
        await maps.ensure_map(api, header.get("beatmap_hash") or "")

        watcher = asyncio.create_task(keep_alive())
        try:
            result = await runner.video(
                replay, maps.songs_dir(), out,
                size=settings.get("size") or "1280x720",
                fps=int(settings.get("fps") or 60),
                mute=bool(settings.get("mute")),
                skin=settings.get("skin"),
                leaderboard=board,
                my_pictures=mine,
                on_progress=on_progress,
                threads=capacity.threads,
                encoder_threads=capacity.encoder_threads,
                polite=capacity.polite,
            )
        finally:
            alive = False
            watcher.cancel()

        await server.deliver(job_id, out, {
            "report": result.report, "width": result.width,
            "height": result.height, "duration": result.duration,
        })
        logger.info("job %s delivered", job_id)
    except (runner.DossierError, maps.MapUnavailable, aiohttp.ClientError, OSError) as exc:
        logger.warning("job %s handed back: %s", job_id, exc)
        await server.give_back(job_id, str(exc))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="where the bot answers")
    parser.add_argument("--name", default=os.uname().nodename, help="how to call this worker")
    parser.add_argument("--once", action="store_true", help="take one job and stop")
    options = parser.parse_args()

    token = os.getenv("RENDER_WORKER_TOKEN", "")
    if not token:
        raise SystemExit("RENDER_WORKER_TOKEN is not set")
    if not runner.is_available():
        raise SystemExit(f"the engine is not built: {runner.binary_path()}")

    from utils.osu.api_client import OsuApiClient
    api = OsuApiClient()
    cores = os.cpu_count() or 4
    refused = None

    async with Server(options.server, token, options.name) as server:
        logger.info("worker %s watching %s", options.name, options.server)
        while True:
            capacity = machine.capacity(cores)
            if not capacity.take:
                # Said once per change rather than every poll: this is the
                # normal state of a laptop on battery, not an incident.
                if capacity.reason != refused:
                    logger.info("not taking work: %s", capacity.reason)
                    refused = capacity.reason
                await asyncio.sleep(POLL_SECONDS)
                continue
            refused = None

            try:
                job = await server.claim()
            except aiohttp.ClientError as exc:
                logger.warning("could not reach the bot: %s", exc)
                await asyncio.sleep(POLL_SECONDS)
                continue

            if job is None:
                await asyncio.sleep(POLL_SECONDS)
                continue

            logger.info("job %s (%s): %s, %s threads", job["id"], job.get("title") or "?",
                        capacity.reason, capacity.threads)
            await _render(server, job, capacity, api)
            if options.once:
                return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

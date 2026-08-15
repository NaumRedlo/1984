"""Renders done somewhere other than where the bot runs.

The bot's host is a two-core server; a render is minutes of drawing and
encoding on it. A laptop with twelve cores does the same work several times
faster, so this offers each render to one and falls back to doing it here when
none takes it.

The seam is the same one the engine already had: `services.dossier.runner`
speaks to the binary over a command line and a stream of NDJSON events, and
nothing about that changes when the binary runs on a different machine.
"""

from services.render_farm.queue import Job, RenderQueue, queue

__all__ = ["Job", "RenderQueue", "queue"]

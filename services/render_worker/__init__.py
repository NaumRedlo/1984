"""Standalone HTTP render worker.

Runs danser-cli on a dedicated server, reachable from the bot over HTTP. The
bot (which holds all osu! credentials) downloads the .osr and resolves the
beatmapset, then POSTs them here; this worker downloads the .osz from public
mirrors and renders. See services/render_worker/server.py and the bot-side
client utils/osu/render_client.py.
"""

from services.render_worker.server import RenderWorkerServer

__all__ = ["RenderWorkerServer"]

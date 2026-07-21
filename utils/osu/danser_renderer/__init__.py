"""Local danser-go renderer for osu! replays.

Requires danser-cli, xvfb-run, ffmpeg installed on the server.
CPU-only rendering via Mesa software (LIBGL_ALWAYS_SOFTWARE=1).

Split into cohesive submodules:

* errors  — the DanserError hierarchy
* beatmap — .osz acquisition (mirror fetch + disk save)
* core    — GL/binary checks, -sPatch builder, render queue + render_replay,
            replay download, and the render/queue/GL module state
* video   — ffprobe probe + ffmpeg fit-to-size re-encode
* skins   — .osk install/list/delete/rename

Everything the callers used off the flat module is re-exported here, so
``from utils.osu import danser_renderer`` / ``danser_renderer.<name>`` keeps
working unchanged (including the ``requests`` attribute some tests patch).
"""

import requests  # noqa: F401 — re-exported so tests can patch danser_renderer.requests.*

from utils.osu.danser_renderer.errors import (  # noqa: F401
    DanserError, DanserNotFoundError, RenderQueueFullError,
)
from utils.osu.danser_renderer.beatmap import (  # noqa: F401
    fetch_beatmap_osz, download_beatmap, save_beatmap_osz,
)
from utils.osu.danser_renderer.core import (  # noqa: F401
    _check_danser, _check_gl_ready, _build_spatch, _target_video_kbps,
    render_replay, download_replay_file,
)
from utils.osu.danser_renderer.video import (  # noqa: F401
    probe_video, fit_video_to_size,
)
from utils.osu.danser_renderer.skins import (  # noqa: F401
    sanitize_skin_name, list_skins, install_skin, delete_skin, rename_skin,
)

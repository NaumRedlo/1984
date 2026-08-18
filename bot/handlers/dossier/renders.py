"""Replays waiting to be rendered.

Judging takes a second; rendering takes minutes. So the bot judges first, then
offers a button — and the replay file has to outlive the handler that received
it for that button to mean anything.

The store is deliberately small and in-memory. These are scratch files for one
tester's experiments, not data: losing them on restart costs a re-upload, while
keeping them would leave replays on disk for ever with nothing to prune them.
"""

from datetime import datetime, timezone
import asyncio
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from time import monotonic

from utils.logger import get_logger

logger = get_logger("bot.dossier.renders")

# Enough for a few experiments in flight; past that the oldest go.
_MAX_PENDING = 12

# One render at a time. Two encoders on one host don't finish twice as fast,
# they finish twice as slowly and compete for the same cores.
render_lock = asyncio.Lock()


@dataclass
class Pending:
    replay_path: str
    workdir: str
    title: str
    created: float = field(default_factory=monotonic)
    # What the engine said about the render, once there has been one. Kept so
    # the summary can live behind a button instead of on top of the video: it
    # is worth reading afterwards and worth nothing in the way.
    report: list[str] = field(default_factory=list)
    # The judge's whole answer. The read-out is split across buttons now, and
    # each section is drawn on demand from this rather than from a string built
    # once and sliced — a section that disagrees with the totals above it would
    # be worse than no section.
    verdict: dict = field(default_factory=dict)
    # The render in flight, so it can be called off. A render is minutes on one
    # core and the wrong size is a mistake worth interrupting.
    task: asyncio.Task | None = None


# How a render is set up. Per user rather than per replay: someone who renders
# at 30fps once wants it next time too, and re-picking it for every file is the
# kind of friction that makes people stop using a tool.
@dataclass
class Choices:
    size: str = "1280x720"
    fps: int = 60
    mute: bool = False
    # None means whatever the deployment configured, which is the right default
    # for a setting most people will never touch.
    skin: str | None = None
    # The map's own artwork behind the play, and the field with nothing on it
    # that talks about the play. Both off: a render is watched to see what
    # happened, and the readouts are how you see it.
    background: bool = False
    bare: bool = False
    # Which of the engine's optional movements are on, as the comma-separated
    # list `--effects` takes. `None` is somebody who has never opened the
    # sub-tabs and gets the engine's own defaults; `""` is somebody who went in
    # and switched all of them off, and the engine obeys that. Kept as the
    # engine's own text rather than five fields so that a sixth movement is a
    # line in one table instead of a column, a field and a keyboard.
    effects: str | None = None
    # How loud each half of the mix is, 0–100. Both natural until somebody says
    # otherwise — `--mute` is still the way to have no sound at all, and these
    # are for hearing the play over the song rather than instead of it.
    music: int = 100
    hitsounds: int = 100
    # Whether the map's own hit sounds play over the skin's. On: a sound is
    # looked for in the map, then the skin, then the game's defaults, and that
    # order is the same in stable, in lazer and in danser. Skipping the first
    # step is a setting, and it is off everywhere by default.
    map_hitsounds: bool = True
    # How far the map's own artwork is darkened, 0–100. `None` is the engine's
    # own figure — which is not 82 stored, but "whatever it settles on".
    dim: int | None = None

    def summary(self) -> str:
        if self.mute:
            sound = "без звука"
        elif (self.music, self.hitsounds) == (100, 100):
            sound = "со звуком"
        else:
            # Named only when it is not the natural mix: a status line read at a
            # glance should not spend two numbers saying "as it comes".
            sound = f"музыка {self.music}% · хиты {self.hitsounds}%"
        extra = "".join(
            f" · {word}"
            for word, on in (("фон", self.background), ("без интерфейса", self.bare))
            if on
        )
        return (
            f"{self.size} · {self.fps} fps · {sound} · "
            f"скин {self.skin or 'по умолчанию'}{extra}"
        )

    def heavy(self) -> bool:
        """Whether this costs a machine minutes rather than seconds.

        Anything past what the settings offered before 4K arrived. A 2160p120
        render is roughly sixteen times the drawing of a 1080p60 one, which is
        the whole reason the ration below exists — and why the ration is not on
        renders in general: an 854x480 clip is cheap and always was.
        """
        try:
            width, height = (int(part) for part in self.size.split("x", 1))
        except ValueError:
            return False
        return width * height > 1920 * 1080 or self.fps > 60


def remember_settings(user, choices: Choices) -> None:
    """Write somebody's render settings onto their row.

    Named apart from `remember`, which files a replay: one collided with the
    other and quietly won, so storing a pending replay called this instead and
    tried to copy a database row onto disk.

    The in-memory copy stays the one the render path reads — it is asked for on
    every frame of progress — and this is what survives a restart.
    """
    user.render_size = choices.size
    user.render_fps = choices.fps
    user.render_mute = choices.mute
    user.render_skin = choices.skin
    user.render_background = choices.background
    user.render_bare = choices.bare
    user.render_effects = choices.effects
    user.render_music = choices.music
    user.render_hitsounds = choices.hitsounds
    user.render_map_hitsounds = choices.map_hitsounds
    user.render_dim = choices.dim


def restore_settings(user, choices: Choices) -> Choices:
    """Fill a fresh `Choices` from a row, leaving the defaults where the row
    says nothing — an account that has never opened the settings has four
    nulls, and those mean "as it comes" rather than "off"."""
    if user is None:
        return choices
    if user.render_size:
        choices.size = user.render_size
    if user.render_fps:
        choices.fps = int(user.render_fps)
    if user.render_mute is not None:
        choices.mute = bool(user.render_mute)
    choices.skin = user.render_skin or None
    if user.render_background is not None:
        choices.background = bool(user.render_background)
    if user.render_bare is not None:
        choices.bare = bool(user.render_bare)
    # Asked for rather than read: a row written before the column existed has
    # no such attribute, and an account that predates the sub-tabs should get
    # the engine's defaults rather than an error.
    choices.effects = getattr(user, "render_effects", None)
    for field in ("music", "hitsounds"):
        stored = getattr(user, f"render_{field}", None)
        if stored is not None:
            setattr(choices, field, int(stored))
    stored = getattr(user, "render_map_hitsounds", None)
    if stored is not None:
        choices.map_hitsounds = bool(stored)
    stored = getattr(user, "render_dim", None)
    choices.dim = None if stored is None else int(stored)
    return choices


# How many renders above 1080p60 one person may have in a day.
#
# A ration rather than a refusal: 4K at 120 frames is worth having and is
# roughly sixteen times the drawing of 1080p60, so the honest arrangement is that
# it exists and is counted. Everything at or below 1080p60 is unrationed, which
# is every render anybody could make before this.
HEAVY_PER_DAY = 5


def _today() -> str:
    """The day the ration is counted in, as text.

    UTC, and compared as `YYYY-MM-DD` strings: the only question ever asked is
    "is this still today", and two strings answer it without a timezone getting
    involved in what is meant to be a simple counter.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def heavy_left(user) -> int:
    """How many rationed renders this person has left today.

    Zero without an account, and that is not a mistake: the count has to be
    written down somewhere, and an unlinked account has nowhere. The settings
    screen says so rather than letting somebody pick 4K and be refused at the
    moment they actually want a video.
    """
    if user is None:
        return 0
    used = int(user.heavy_renders or 0) if user.heavy_renders_on == _today() else 0
    return max(0, HEAVY_PER_DAY - used)


def spend_heavy(user) -> None:
    """Count one against the ration, rolling the day over if it has changed."""
    if user is None:
        return
    if user.heavy_renders_on != _today():
        user.heavy_renders_on = _today()
        user.heavy_renders = 0
    user.heavy_renders = int(user.heavy_renders or 0) + 1


_pending: dict[str, Pending] = {}
_choices: dict[int, Choices] = {}


def choices(user_id: int) -> Choices:
    """This user's render settings, created on first use."""
    return _choices.setdefault(user_id, Choices())


def remember(replay_path: str, title: str, verdict: dict | None = None) -> str:
    """Copy the replay somewhere it will survive, and return a token for it."""
    workdir = tempfile.mkdtemp(prefix="dossier-render-")
    kept = os.path.join(workdir, "replay.osr")
    shutil.copyfile(replay_path, kept)

    token = uuid.uuid4().hex[:12]
    _pending[token] = Pending(
        replay_path=kept, workdir=workdir, title=title, verdict=verdict or {}
    )
    _evict_old()
    return token


def get(token: str) -> Pending | None:
    return _pending.get(token)


def forget(token: str) -> None:
    entry = _pending.pop(token, None)
    if entry:
        shutil.rmtree(entry.workdir, ignore_errors=True)


def _evict_old() -> None:
    while len(_pending) > _MAX_PENDING:
        oldest = min(_pending, key=lambda t: _pending[t].created)
        logger.info("evicting pending render %s", oldest)
        forget(oldest)

from datetime import datetime, timezone
import asyncio
from contextlib import contextmanager
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from time import monotonic

from utils.logger import get_logger

logger = get_logger("bot.dossier.renders")

_MAX_PENDING = 96

_rendering: set[str] = set()

def is_rendering(token: str) -> bool:
    return token in _rendering

@contextmanager
def one_at_a_time(token: str):
    _rendering.add(token)
    try:
        yield
    finally:
        _rendering.discard(token)

@dataclass
class Pending:
    replay_path: str
    workdir: str
    title: str
    created: float = field(default_factory=monotonic)

    report: list[str] = field(default_factory=list)

    verdict: dict = field(default_factory=dict)

    task: asyncio.Task | None = None

@dataclass
class Choices:
    size: str = "1280x720"
    fps: int = 60
    mute: bool = False

    skin: str | None = None

    background: bool = False
    bare: bool = False

    effects: str | None = None

    music: int = 100
    hitsounds: int = 100

    volume: int | None = None

    leaderboard: bool = True

    map_hitsounds: bool = True

    dim: int | None = None

    meter: int | None = None

    cursor: int | None = None

    blur: int | None = None

    def summary(self, lang: str = "ru") -> str:
        from utils.i18n import t

        if self.mute:
            sound = t("sts.rnd.sound_off", lang)
        elif (self.music, self.hitsounds) == (100, 100):
            sound = t("sts.rnd.sound_on", lang)
        else:

            sound = t("sts.rnd.sound_mix", lang, music=self.music, hits=self.hitsounds)

        extra = "".join(
            f" · {t(key, lang).lower()}"
            for key, said in (
                ("sts.rnd.background", self.background),
                ("sts.rnd.bare", self.bare),
                ("sts.rnd.no_board", not self.leaderboard),
            )
            if said
        )
        skin = self.skin or t("sts.rnd.skin_default", lang)

        size = self.size.replace("x", "×")
        return f"{size} · {self.fps} fps · {sound} · {skin}{extra}"

    def heavy(self) -> bool:
        try:
            width, height = (int(part) for part in self.size.split("x", 1))
        except ValueError:
            return False
        return width * height > 1920 * 1080 or self.fps > 60

def remember_settings(user, choices: Choices) -> None:
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
    user.render_meter = choices.meter
    user.render_volume = choices.volume
    user.render_leaderboard = choices.leaderboard
    user.render_cursor = choices.cursor
    user.render_blur = choices.blur

def restore_settings(user, choices: Choices) -> Choices:
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
    stored = getattr(user, "render_meter", None)
    choices.meter = None if stored is None else int(stored)
    stored = getattr(user, "render_cursor", None)
    choices.cursor = None if stored is None else int(stored)
    stored = getattr(user, "render_blur", None)
    choices.blur = None if stored is None else int(stored)
    stored = getattr(user, "render_volume", None)
    choices.volume = None if stored is None else int(stored)
    stored = getattr(user, "render_leaderboard", None)
    if stored is not None:
        choices.leaderboard = bool(stored)
    return choices

HEAVY_PER_DAY = 5

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def heavy_left(user) -> int:
    if user is None:
        return 0
    used = int(user.heavy_renders or 0) if user.heavy_renders_on == _today() else 0
    return max(0, HEAVY_PER_DAY - used)

def spend_heavy(user) -> None:
    if user is None:
        return
    if user.heavy_renders_on != _today():
        user.heavy_renders_on = _today()
        user.heavy_renders = 0
    user.heavy_renders = int(user.heavy_renders or 0) + 1

_pending: dict[str, Pending] = {}
_choices: dict[int, Choices] = {}

def choices(user_id: int) -> Choices:
    return _choices.setdefault(user_id, Choices())

def remember(replay_path: str, title: str, verdict: dict | None = None) -> str:
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

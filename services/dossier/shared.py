"""Replays whose players agreed to hand them over.

The settings screen has offered "send replay data to the developer" for a
while, and until now it did nothing at all: the flag was written to a column
and read back by the screen that wrote it, and no replay went anywhere. So the
bot was telling people «Каждый отрендеренный реплей уходит автору бота» and it
was not true.

Not a privacy failure — nothing was collected — but a false statement about
data handling shown to everybody who opened that screen, which is its own kind
of wrong and is the reason this exists.

## What is kept, and why exactly this

The consent says two things and this keeps two things: **the `.osr` itself**
and **what the engine made of it**. Nothing else — no Telegram id, no chat, no
account link. The `.osr` already carries the osu! username in its header, which
is what "the replay itself" means and is the whole of the identity involved.

It is worth being strict about that. The stated purpose is finding where the
engine judges a play wrongly, and everything needed for that is in those two
files. A field added because it might be useful later is a field nobody
consented to.

## Deduplicated by content

The same replay rendered five times is one file. A hash of the bytes is the
name, so a re-render costs nothing and the store does not fill with copies of
somebody testing a setting.

## Off unless the operator asks

`SHARED_REPLAY_DIR` unset means nothing is kept whatever anybody ticked. A
deployment that does not want a pile of other people's replays on its disk
should not get one by default.
"""

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import SHARED_REPLAY_DIR
from utils.logger import get_logger

logger = get_logger("services.dossier.shared")


def enabled() -> bool:
    """Whether this deployment keeps anything at all."""
    return bool(SHARED_REPLAY_DIR)


def how_many() -> int:
    """How many replays are held. Nought when nothing is collected.

    For the line the bot prints at startup: a count is the difference between
    "the variable is set" and "this is working", and the second is the one
    somebody actually wants to know.
    """
    if not enabled():
        return 0
    total = 0
    for _, _, leaves in os.walk(SHARED_REPLAY_DIR):
        total += sum(1 for leaf in leaves if leaf.endswith(".osr"))
    return total


def _digest(path: str) -> str:
    sha = hashlib.sha1()  # noqa: S324 — a filename, not a signature
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            sha.update(block)
    return sha.hexdigest()[:16]


def keep(replay_path: str, verdict: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Keep a copy of a replay its player agreed to share. Returns where.

    Never raises. A render that succeeded must not be reported as failed
    because a disk was full or a directory was not writable — the copy is a
    side errand, and the person waiting for a video has nothing to do with it.
    """
    if not enabled():
        return None
    try:
        digest = _digest(replay_path)
        # By the day, so a month of collecting is browsable rather than one
        # directory with thousands of files in it.
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        into = os.path.join(SHARED_REPLAY_DIR, day)
        os.makedirs(into, exist_ok=True)

        landed = os.path.join(into, f"{digest}.osr")
        if os.path.exists(landed):
            # Already have it. The verdict is still written, because the engine
            # that read it this time may not be the engine that read it last.
            logger.debug("replay %s already kept", digest)
        else:
            shutil.copy2(replay_path, landed)

        if verdict is not None:
            with open(os.path.join(into, f"{digest}.json"), "w", encoding="utf-8") as out:
                json.dump(verdict, out, ensure_ascii=False, indent=1, default=str)
        logger.info("kept shared replay %s", digest)
        return landed
    except (OSError, ValueError) as exc:
        logger.warning("could not keep a shared replay: %s", exc)
        return None


__all__ = ["enabled", "keep", "how_many"]

"""Which build of the engine a machine is about to render with.

A render farm worker runs its own checkout and its own `cargo build`. Nothing
has ever made it say so, so a worker whose binary is behind the bot's renders
with old code, produces output that looks perfectly plausible, and no one finds
out until somebody notices the pictures are wrong. That is the same shape as a
skin folder unpacked by an importer since fixed, and that one cost a long
evening before it was found — the lesson being that stale *inputs* are worse
than broken ones, because broken announces itself.

So the engine stamps itself with the commit it was built from and can be asked:

    $ dossier --version
    dossier 0.1.0 (15abdf1)

The manifest version is not the useful half — it has never been bumped and
never will be by hand. The commit is, and it is the only identity two machines
can compare: a hash of the binary would differ between a Linux build and a
macOS one of the same source, which is exactly the pair that needs comparing.

## What a disagreement means

Refusal. A worker whose build differs from the bot's is turned away and the bot
renders the job itself, which is the fallback the farm is built around anyway —
so the cost of being strict is a slower render, and the cost of being lax is a
wrong one nobody notices.

`unknown` is not a match and not a mismatch. A binary built without git to ask
cannot say what it is, and two of those are not thereby the same. They are let
through, once loudly: a farm that stops working because somebody built from a
tarball has failed at something that was never its business.
"""

import asyncio
import shutil
from typing import Optional

from config.settings import DOSSIER_BIN
from utils.logger import get_logger

logger = get_logger("services.dossier.build")

# What the engine prints when it was built with no git to ask.
UNKNOWN = "unknown"

_cached: Optional[str] = None


async def local(*, refresh: bool = False) -> Optional[str]:
    """What this machine's engine says it is, or `None` if it cannot be asked.

    Cached: the binary does not change under a running process, and a render is
    not the moment to spend a subprocess on a constant. `refresh` is for tests
    and for a long-lived worker that may outlive a rebuild.
    """
    global _cached
    if _cached is not None and not refresh:
        return _cached

    binary = shutil.which(DOSSIER_BIN) or DOSSIER_BIN
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(process.communicate(), 10)
    except (OSError, asyncio.TimeoutError):
        logger.warning("engine build: %s could not be asked its version", binary)
        return None
    if process.returncode != 0:
        # An engine old enough not to know `--version` answers non-zero. That is
        # itself a mismatch worth reporting, and reporting it as "cannot be
        # asked" would let exactly the stale binary this exists for through.
        logger.warning("engine build: %s does not answer --version", binary)
        return None

    _cached = out.decode(errors="replace").strip() or None
    return _cached


def commit_of(version: Optional[str]) -> str:
    """The commit out of `dossier 0.1.0 (15abdf1+)`, or `unknown`.

    The `+` is kept. A build from an edited tree is not the commit it names and
    two of them are not each other, so a worker running one is refused against
    anything — including the same hash without the mark.
    """
    if not version:
        return UNKNOWN
    start = version.rfind("(")
    end = version.rfind(")")
    if start == -1 or end < start:
        return UNKNOWN
    return version[start + 1 : end].strip() or UNKNOWN


def agree(ours: Optional[str], theirs: Optional[str]) -> tuple[bool, str]:
    """Whether two builds may work on the same job, and why.

    The reason is returned rather than logged so the caller can put it where it
    belongs — in a refusal the worker reads, not only in a log nobody is
    watching when it matters.
    """
    mine, yours = commit_of(ours), commit_of(theirs)
    if mine == UNKNOWN or yours == UNKNOWN:
        return True, "one of the two builds cannot say what it is"
    if mine != yours:
        return False, f"the bot renders with {mine} and this worker with {yours}"
    if mine.endswith("+"):
        return True, f"both are {mine}, built from an edited tree"
    return True, f"both are {mine}"


__all__ = ["local", "commit_of", "agree", "UNKNOWN"]

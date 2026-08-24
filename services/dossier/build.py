"""Which build of the engine a machine is about to render with.

A render farm worker runs its own checkout and its own `cargo build`. Nothing
has ever made it say so, so a worker whose binary is behind the bot's renders
with old code, produces output that looks perfectly plausible, and no one finds
out until somebody notices the pictures are wrong. That is the same shape as a
skin folder unpacked by an importer since fixed, and that one cost a long
evening before it was found — the lesson being that stale *inputs* are worse
than broken ones, because broken announces itself.

So the engine stamps itself with the source it was built from and can be asked:

    $ dossier --version
    dossier 0.1.0 (15abdf1)

The manifest version is not the useful half — it has never been bumped and
never will be by hand. The id is, and it is the only identity two machines can
compare: a hash of the binary would differ between a Linux build and a macOS
one of the same source, which is exactly the pair that needs comparing.

It is deliberately *not* the commit. It was, and this module stopped the farm
over it: a worker was refused with "the bot renders with 8aae009 and this
worker with 6054b39" when the entire difference between those commits was one
markdown file. The id now covers the crates, the lockfile and the workspace
manifest and nothing else — see `crates/dossier-cli/build.rs` for why those
three and how they are folded into one word.

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


def build_of(version: Optional[str]) -> str:
    """The id out of `dossier 0.1.0 (15abdf1+)`, or `unknown`.

    The `+` is kept, so a build from an edited tree is refused against the same
    id without the mark — the edits are exactly what the two do not share.

    Two `+` builds of the same id are let through, for the same reason two
    `unknown`s are: neither can say what it is, and this module has already
    decided that cannot-tell is not a refusal.
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

    A refusal says what to do about itself. The two kinds need opposite
    answers and used to get the same one: a worker turned away for having an
    edited tree was told to `git pull`, which does nothing about uncommitted
    changes and left somebody rebuilding in a loop. Only this function knows
    which kind it is, so this is where the remedy belongs.
    """
    mine, yours = build_of(ours), build_of(theirs)
    if mine == UNKNOWN or yours == UNKNOWN:
        return True, "one of the two builds cannot say what it is"

    if mine == yours:
        if mine.endswith("+"):
            # Two edited trees are the cannot-tell case, same as two
            # `unknown`s: neither can say what it is, and this module has
            # already decided that ignorance is not a refusal.
            return True, f"both are {mine}, built from an edited tree"
        return True, f"both are {mine}"

    if mine.rstrip("+") == yours.rstrip("+"):
        # The same source on both sides, and one binary was built from a tree
        # with edits on top of it. Pulling cannot fix that.
        #
        # It leads with "rebuild" because the mark outlives the edits: the
        # stamp is fixed when the binary is linked, so a machine that tidied up
        # and did not build again keeps saying `+` with nothing uncommitted
        # left to find. Rebuilding is the step that always applies; stashing is
        # only sometimes needed, and telling somebody to look for changes that
        # are not there is how an evening goes.
        edited = "this worker" if yours.endswith("+") else "the bot"
        return False, (
            f"both are on {mine.rstrip('+')}, but {edited} built its binary "
            f"from an edited tree — rebuild it there, having first committed "
            f"or stashed anything still uncommitted under dossier/crates"
        )

    return False, (
        f"the bot renders with {mine} and this worker with {yours} — "
        f"`git pull && cd dossier && cargo build --release` on whichever is behind"
    )


__all__ = ["local", "build_of", "agree", "UNKNOWN"]

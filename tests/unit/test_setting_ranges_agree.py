"""The ranges the bot offers are the ranges the engine accepts.

Somebody was told to type "от 25 до 300 процентов", typed 30, and got back
`--meter-scale runs from 0.5 to 3 — 0.3 is outside it`. The bot had promised
something the engine refuses: it divides a percentage by a hundred to reach the
flag, and the engine's floor is 0.5, so everything under 50% was a lie.

The engine's own `--help` is the authority, because it is the thing that
refuses. Parsing it here means the two sides cannot drift again without this
saying so — the same shape as the guard on the render options, and for the same
reason: two places state one fact and only one of them is enforced.
"""

import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bot.handlers.profile.settings_menu.typed import FIELDS  # noqa: E402
from services.dossier import runner  # noqa: E402

# The bot's own name for a setting, the engine's flag, and whether the bot
# states it as a percentage of the flag's number.
PAIRS = {
    "meter": ("--meter-scale", True),
    "cursor": ("--cursor-scale", True),
    "volume": ("--volume", False),
    "music": ("--music", False),
    "hitsounds": ("--hitsounds", False),
    "dim": ("--dim", False),
    "blur": ("--blur", False),
}


def engine_ranges() -> dict[str, tuple[float, float]]:
    """`<0.5-3>` out of the engine's usage, per flag."""
    done = subprocess.run(
        [runner.binary_path(), "video", "--help"],
        capture_output=True, text=True, check=False,
    )
    found = {}
    for line in (done.stdout + done.stderr).splitlines():
        match = re.match(r"\s*(--[a-z-]+)\s+<([0-9.]+)\s*-\s*([0-9.]+)>", line)
        if match:
            found[match.group(1)] = (float(match.group(2)), float(match.group(3)))
    return found


@pytest.mark.skipif(not runner.is_available(), reason="the engine is not built")
@pytest.mark.parametrize("name", sorted(PAIRS))
def test_the_bot_offers_only_what_the_engine_accepts(name):
    flag, percent = PAIRS[name]
    ranges = engine_ranges()
    assert flag in ranges, f"the engine no longer states a range for {flag}"
    low, high = ranges[flag]

    # Every typed setting is a whole number in a range, and the parser is the
    # thing that knows it — so the range is found by asking the parser rather
    # than by reading a constant that could itself be the stale one.
    parse = FIELDS[name].parse
    offered = [n for n in range(0, 1001) if parse(str(n)) is not None]
    assert offered, f"{name} accepts nothing at all"

    scale = 100.0 if percent else 1.0
    for edge in (offered[0], offered[-1]):
        asked = edge / scale
        assert low <= asked <= high, (
            f"the bot offers {name}={edge} which reaches {flag} {asked}, and "
            f"the engine takes {low} to {high} — somebody typing that is told "
            f"the bot lied and the engine caught it"
        )

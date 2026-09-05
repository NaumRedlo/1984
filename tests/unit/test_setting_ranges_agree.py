import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bot.handlers.profile.settings_menu.typed import FIELDS
from dossier import runner

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

    field = FIELDS[name]
    assert field.low is not None and field.high is not None, (
        f"{name} no longer says what it accepts, and the page cannot draw it"
    )
    assert field.parse(str(field.low)) is not None, f"{name} refuses its own floor"
    assert field.parse(str(field.high)) is not None, f"{name} refuses its own ceiling"
    assert field.parse(str(field.low - 1)) is None, f"{name} takes less than it says"

    scale = 100.0 if percent else 1.0
    for edge in (field.low, field.high):
        asked = edge / scale
        assert low <= asked <= high, (
            f"the bot offers {name}={edge} which reaches {flag} {asked}, and "
            f"the engine takes {low} to {high} — somebody typing that is told "
            f"the bot lied and the engine caught it"
        )

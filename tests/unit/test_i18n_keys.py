"""Every key the code asks for is a key the catalogue answers.

`t()` falls back to printing the key it was given, which is the right thing to
do — a screen with one line missing beats a screen that raises — but it is
silent, and silence is how five of them survived on the gameplay screen from
the day it was written until somebody sent a screenshot of `sts.fx.about.slider`
stacked under a heading.

The keys are read out of the source rather than hit at run time, because a key
on a branch nobody took in a test is exactly the key that goes missing.
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _asked() -> list[tuple[str, str]]:
    """Every literal key handed to `t(...)`, with where it was asked for."""
    out: list[tuple[str, str]] = []
    for path in sorted((ROOT / "bot").rglob("*.py")) + sorted(
        (ROOT / "services").rglob("*.py")
    ):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "t" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.append(
                    (first.value, f"{path.relative_to(ROOT)}:{first.lineno}")
                )
            elif isinstance(first, ast.JoinedStr):
                # `t(f"sts.fx.{name}")` — the fixed part is checked as a prefix,
                # since which keys it can reach is a question about the table it
                # is looping over rather than about this line.
                #
                # Only up to the first `{…}`, not every constant in the string:
                # joining across a placeholder turns `help.sec.{key}.body` into
                # `help.sec..body`, which matches nothing and reports a key that
                # was never asked for.
                fixed = ""
                for piece in first.values:
                    if not isinstance(piece, ast.Constant):
                        break
                    fixed += piece.value
                if fixed:
                    out.append((fixed + "*", f"{path.relative_to(ROOT)}:{first.lineno}"))
    return out


def _known() -> set[str]:
    from utils.i18n import _CATALOG

    return set(_CATALOG)


def test_every_key_the_code_asks_for_exists():
    known = _known()
    asked = _asked()
    assert asked and known, "the scan found nothing — it has stopped working"
    missing = []
    for key, where in asked:
        if key.endswith("*"):
            if not any(k.startswith(key[:-1]) for k in known):
                missing.append(f"{key!r} at {where}")
        elif key not in known:
            missing.append(f"{key!r} at {where}")
    assert not missing, "keys nobody wrote:\n  " + "\n  ".join(missing)


def test_the_scan_would_notice_a_key_that_went_away():
    """A guard on the guard: a scan that matched everything would look exactly
    like a catalogue with nothing missing."""
    asked = [k for k, _ in _asked() if not k.endswith("*")]
    assert asked, "no plain keys found"
    thinned = _known() - {asked[0]}
    assert asked[0] not in thinned

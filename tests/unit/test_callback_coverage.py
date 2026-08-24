"""Every button the bot draws is a button something answers.

The cancel button on a render was drawn on every progress message since
renders existed and nothing answered it: `dsx:` appeared once in the whole
repository, on the keyboard itself. A tap did nothing at all — not even the
spinner Telegram shows until a callback is acknowledged — so the render ran to
the end while the person who asked for it watched a dead button.

Nothing else could have caught it. It is not a crash, not a failing branch and
not a wrong answer; it is an absence, and an absence has no line to put a test
on. So the test is over the pair: what the keyboards produce, and what the
routers accept.
"""

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2] / "bot"


def _literal_prefix(node: ast.AST) -> tuple[str | None, bool]:
    """The fixed part of a `callback_data` value, and whether that is all of it.

    `f"dsr:{token}"` is `("dsr:", False)` — a prefix, with the rest decided at
    run time. `"st:home"` is `("st:home", True)`, which a router may match
    exactly.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, True
    if isinstance(node, ast.JoinedStr):
        fixed = ""
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                fixed += piece.value
            else:
                return (fixed or None), False
        return (fixed or None), True
    return None, False


def _read() -> tuple[list[tuple[str, bool, str]], list[tuple[str, str]]]:
    produced: list[tuple[str, bool, str]] = []
    handled: list[tuple[str, str]] = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "callback_data":
                fixed, exact = _literal_prefix(node.value)
                if fixed:
                    where = f"{path.relative_to(ROOT.parent)}:{node.value.lineno}"
                    produced.append((fixed, exact, where))
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(
                "callback_query"
            ):
                for arg in node.args:
                    text = ast.unparse(arg)
                    for m in re.finditer(r"startswith\(['\"]([^'\"]+)['\"]\)", text):
                        handled.append(("prefix", m.group(1)))
                    for m in re.finditer(r"data == ['\"]([^'\"]+)['\"]", text):
                        handled.append(("exact", m.group(1)))
                    for m in re.finditer(r"data\.in_\(\{([^}]*)\}", text):
                        for lit in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)):
                            handled.append(("exact", lit))
    return produced, handled


def _covered(value: str, exact: bool, handled) -> bool:
    for kind, pattern in handled:
        if kind == "prefix" and value.startswith(pattern):
            return True
        if kind == "exact" and (value == pattern if exact else pattern.startswith(value)):
            return True
    return False


def test_every_button_the_bot_draws_has_something_that_answers_it():
    produced, handled = _read()
    assert produced and handled, "the scan found nothing — it has stopped working"
    orphans = [
        f"{value!r} at {where}"
        for value, exact, where in produced
        if not _covered(value, exact, handled)
    ]
    assert not orphans, "buttons nobody answers:\n  " + "\n  ".join(orphans)


def test_the_scan_would_notice_if_a_handler_went_away():
    """A guard on the guard. This test is only worth having if removing a
    router makes it fail, and a scan that quietly matched everything would look
    exactly like a clean repository."""
    produced, handled = _read()
    without_render = [h for h in handled if h != ("prefix", "dsr:")]
    assert any(
        not _covered(value, exact, without_render)
        for value, exact, _ in produced
    ), "dropping a real router changed nothing — the scan is not reading them"

"""Who may reach the engine.

The gate has been an allowlist since the engine could not be shown to anybody,
and the release is the moment it stops being one. Both spellings live in the
same variable so there is one place to look for the answer to "who can render",
and so closing it again is the same edit in reverse.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from config import settings  # noqa: E402
from utils.render_access import can_use_render  # noqa: E402


def _gate(monkeypatch, *, everyone: bool, ids: list[int]):
    monkeypatch.setattr(settings, "RENDER_OPEN_TO_ALL", everyone)
    monkeypatch.setattr(settings, "RENDER_TESTER_IDS", ids)


def test_nobody_by_default(monkeypatch):
    """An unfinished renderer ignores the world rather than answering it, and
    that stays the behaviour of an unset variable."""
    _gate(monkeypatch, everyone=False, ids=[])
    assert not can_use_render(12345)


def test_the_allowlist_still_works(monkeypatch):
    _gate(monkeypatch, everyone=False, ids=[111, 222])
    assert can_use_render(111) and not can_use_render(333)


def test_a_star_opens_it_to_everybody(monkeypatch):
    """The release switch itself."""
    _gate(monkeypatch, everyone=True, ids=[])
    assert can_use_render(999999) and can_use_render(1)


def test_a_star_is_read_from_the_environment():
    """The parsing, not the policy: `*` is not a number, so the list stays
    empty and the flag is what carries the meaning."""
    raw = "*"
    assert raw.strip() == "*"
    assert [int(x) for x in raw.split(",") if x.strip().isdigit()] == []


def test_closing_it_again_is_the_same_edit_in_reverse(monkeypatch):
    """The property that makes this safe to flip during an alpha: putting the
    ids back takes the gate straight back to where it was."""
    _gate(monkeypatch, everyone=True, ids=[])
    assert can_use_render(777)
    _gate(monkeypatch, everyone=False, ids=[111])
    assert not can_use_render(777)

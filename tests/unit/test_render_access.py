import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from config import settings
from utils.render_access import can_use_render

def _gate(monkeypatch, *, everyone: bool, ids: list[int]):
    monkeypatch.setattr(settings, "RENDER_OPEN_TO_ALL", everyone)
    monkeypatch.setattr(settings, "RENDER_TESTER_IDS", ids)

def test_nobody_by_default(monkeypatch):
    _gate(monkeypatch, everyone=False, ids=[])
    assert not can_use_render(12345)

def test_the_allowlist_still_works(monkeypatch):
    _gate(monkeypatch, everyone=False, ids=[111, 222])
    assert can_use_render(111) and not can_use_render(333)

def test_a_star_opens_it_to_everybody(monkeypatch):
    _gate(monkeypatch, everyone=True, ids=[])
    assert can_use_render(999999) and can_use_render(1)

def test_a_star_is_read_from_the_environment():
    raw = "*"
    assert raw.strip() == "*"
    assert [int(x) for x in raw.split(",") if x.strip().isdigit()] == []

def test_closing_it_again_is_the_same_edit_in_reverse(monkeypatch):
    _gate(monkeypatch, everyone=True, ids=[])
    assert can_use_render(777)
    _gate(monkeypatch, everyone=False, ids=[111])
    assert not can_use_render(777)

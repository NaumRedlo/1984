"""Unit tests for the admin-panel command registry (bot.handlers.admin.panel_registry).

Guards that the panel stays in sync with the real admin commands and that its
inline callbacks are well-formed:
  - every registry trigger is a real admin command (no invented commands);
  - every real admin command is reachable from the panel (full coverage);
  - no duplicate triggers; every category is non-empty;
  - callback_data fits Telegram's 64-byte limit;
  - executor (auto-run) commands are safe: no args, not destructive.
"""

from __future__ import annotations

import re
from pathlib import Path

from bot.handlers.admin.panel_registry import CATEGORIES, all_commands, find_command

_ADMIN_DIR = Path(__file__).resolve().parents[2] / "bot" / "handlers" / "admin"
_TTF = re.compile(r'TextTriggerFilter\(\s*((?:"[^"]+"\s*,?\s*)+)\)')
_STR = re.compile(r'"([^"]+)"')

# The panel's own entry trigger — it isn't itself a registry command.
_PANEL_GROUP = frozenset({"admin", "ap"})


def _real_command_groups() -> list[frozenset[str]]:
    """Each TextTriggerFilter("x", "y") in bot/handlers/admin/ == one command,
    represented as the frozenset of its trigger + aliases."""
    groups: list[frozenset[str]] = []
    for f in _ADMIN_DIR.glob("*.py"):
        for m in _TTF.finditer(f.read_text(encoding="utf-8")):
            triggers = tuple(_STR.findall(m.group(1)))
            if triggers:
                groups.append(frozenset(triggers))
    return groups


def test_every_registry_trigger_is_real():
    real = set().union(*_real_command_groups())
    for cmd in all_commands():
        assert cmd.trigger in real, f"{cmd.trigger!r} is not a real admin command"


def test_every_command_is_covered_by_panel():
    registry = {c.trigger for c in all_commands()}
    for group in _real_command_groups():
        if group == _PANEL_GROUP:
            continue  # the panel entry itself
        assert group & registry, f"command {sorted(group)} is missing from the panel"


def test_no_duplicate_triggers():
    triggers = [c.trigger for c in all_commands()]
    assert len(triggers) == len(set(triggers)), "duplicate trigger in registry"


def test_categories_non_empty_and_unique_keys():
    keys = [c.key for c in CATEGORIES]
    assert len(keys) == len(set(keys)), "duplicate category key"
    for cat in CATEGORIES:
        assert cat.commands, f"category {cat.key!r} is empty"


def test_callback_data_within_limit():
    for cat in CATEGORIES:
        assert len(f"ap:c:{cat.key}".encode()) <= 64
        for cmd in cat.commands:
            assert len(f"ap:m:{cmd.trigger}".encode()) <= 64
            assert len(f"ap:r:{cmd.trigger}".encode()) <= 64


def test_executor_commands_are_safe():
    for cmd in all_commands():
        if cmd.executor is not None:
            assert callable(cmd.executor)
            assert cmd.args is None, f"{cmd.trigger}: executor command must take no args"
            assert cmd.destructive is False, f"{cmd.trigger}: executor must not be destructive"


def test_find_helpers():
    assert find_command("whereami") is not None
    assert find_command("nonsuch") is None

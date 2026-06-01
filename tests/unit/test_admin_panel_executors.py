"""Smoke tests for an admin-panel executor coroutine.

The panel runs read-only commands via build_*_report() coroutines extracted
from their handlers. build_import_queue_report needs no DB, so it's the
cleanest end-to-end check that the extraction + lazy executor wiring works.
"""

from __future__ import annotations

import pytest

import bot.handlers.admin.duel_pool as bp
from bot.handlers.admin.panel_registry import find_command


@pytest.mark.asyncio
async def test_import_queue_report_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "IMPORT_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(bp, "_import_queue", {})
    text = await bp.build_import_queue_report()
    assert "пуста" in text.lower()


@pytest.mark.asyncio
async def test_import_queue_report_lists_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "IMPORT_TMP_DIR", str(tmp_path))
    # No created_at → the stale-cleanup pass leaves the slot intact.
    monkeypatch.setattr(bp, "_import_queue", {
        "s1": {"status": "pending", "filename": "pack.7z"},
    })
    text = await bp.build_import_queue_report()
    assert "pack.7z" in text
    assert "pending" in text


@pytest.mark.asyncio
async def test_registry_executor_resolves_to_report(tmp_path, monkeypatch):
    # The lazy executor in the registry must reach the same coroutine.
    monkeypatch.setattr(bp, "IMPORT_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(bp, "_import_queue", {})
    cmd = find_command("importqueue")
    assert cmd.executor is not None
    text = await cmd.executor()
    assert "пуста" in text.lower()

"""Unit tests for services.duel.bulk_import._insert_duel_map retry logic.

A SQLite commit can raise OperationalError('database is locked') under write
contention — that's transient and must be retried, NOT mislabelled as a real
IntegrityError (duplicate). These tests mock the session so no DB is touched.
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

import services.duel.bulk_import as bi


def _locked() -> OperationalError:
    return OperationalError("INSERT ...", {}, Exception("database is locked"))


def _dup() -> IntegrityError:
    return IntegrityError("INSERT ...", {}, Exception("UNIQUE constraint failed"))


def _patch_db(commit_effects, calls):
    """Patch get_db_session with a fake whose .commit() pops one side-effect
    per call (an exception to raise, or None to succeed)."""
    class _Session:
        def add(self, _entry):
            pass

        async def commit(self):
            calls["commit"] += 1
            eff = commit_effects.pop(0) if commit_effects else None
            if eff is not None:
                raise eff

        async def rollback(self):
            pass

    @contextlib.asynccontextmanager
    async def _fake_get_db_session():
        yield _Session()

    return _fake_get_db_session


@pytest.fixture(autouse=True)
def _fast_and_isolated(monkeypatch):
    # No real sleeps between retries; no real ORM mutation.
    monkeypatch.setattr(bi, "DUEL_BULK_DB_RETRY_DELAY", 0)
    monkeypatch.setattr("services.duel.map_pool.apply_to_entry", lambda e, r: None)


def _run(monkeypatch, commit_effects):
    import asyncio
    calls = {"commit": 0}
    monkeypatch.setattr("db.database.get_db_session", _patch_db(commit_effects, calls))
    status, _entry = asyncio.run(bi._insert_duel_map({"beatmap_id": 1}, {}))
    return status, calls["commit"]


def test_retries_then_succeeds(monkeypatch):
    # Locked twice, then commits. Should end 'added' after 3 commit attempts.
    status, commits = _run(monkeypatch, [_locked(), _locked(), None])
    assert status == "added"
    assert commits == 3


def test_duplicate_not_retried(monkeypatch):
    status, commits = _run(monkeypatch, [_dup()])
    assert status == "duplicate"
    assert commits == 1


def test_gives_up_after_max_retries(monkeypatch):
    # Always locked → 'locked' after exactly DUEL_BULK_DB_RETRIES attempts.
    status, commits = _run(monkeypatch, [_locked()] * 10)
    assert status == "locked"
    assert commits == bi.DUEL_BULK_DB_RETRIES


def test_succeeds_first_try(monkeypatch):
    status, commits = _run(monkeypatch, [None])
    assert status == "added"
    assert commits == 1

"""Unit tests for services.map_import.multi_volume single-archive helpers.

Covers sniff_archive_kind (magic-byte detection) and normalize_single_archive
(.zip pass-through, .rar/unknown rejection, and the real .7z → extract →
re-zip chain when the `7z` binary is available).
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile

import pytest

from services.duel.bulk_import import _iter_osu_entries_from_archive
from services.map_import.multi_volume import (
    MultiVolumeError,
    normalize_single_archive,
    sniff_archive_kind,
)

_HAS_7Z = shutil.which("7z") is not None


def _make_osz(path: str, osu_name: str = "Artist - Song [Diff].osu") -> None:
    """Write a minimal .osz (a zip holding one .osu text file)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(osu_name, "osu file format v14\n\n[Metadata]\nBeatmapID:123\n")


# ── sniff_archive_kind ───────────────────────────────────────────────────────


def test_sniff_zip(tmp_path):
    p = tmp_path / "a.zip"
    _make_osz(str(p))
    assert sniff_archive_kind(str(p)) == "zip"


def test_sniff_osz_is_zip(tmp_path):
    p = tmp_path / "map.osz"
    _make_osz(str(p))
    assert sniff_archive_kind(str(p)) == "zip"


def test_sniff_7z_magic(tmp_path):
    p = tmp_path / "a.7z"
    p.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32)
    assert sniff_archive_kind(str(p)) == "7z"


def test_sniff_rar_magic(tmp_path):
    p = tmp_path / "a.rar"
    p.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 32)
    assert sniff_archive_kind(str(p)) == "rar"


def test_sniff_unknown(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"<html>not a file</html>")
    assert sniff_archive_kind(str(p)) == "unknown"


def test_sniff_empty(tmp_path):
    p = tmp_path / "empty"
    p.write_bytes(b"")
    assert sniff_archive_kind(str(p)) == "unknown"


# ── normalize_single_archive ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_normalize_zip_passes_through(tmp_path):
    p = tmp_path / "pack.zip"
    _make_osz(str(p))
    import_path, cleanup = await normalize_single_archive(str(p))
    assert import_path == str(p)
    assert cleanup is None


@pytest.mark.asyncio
async def test_normalize_rar_rejected(tmp_path):
    p = tmp_path / "pack.rar"
    p.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 32)
    with pytest.raises(MultiVolumeError, match="RAR"):
        await normalize_single_archive(str(p))


@pytest.mark.asyncio
async def test_normalize_unknown_rejected(tmp_path):
    p = tmp_path / "pack.bin"
    p.write_bytes(b"<html>error page</html>")
    with pytest.raises(MultiVolumeError, match="формат"):
        await normalize_single_archive(str(p))


@pytest.mark.skipif(not _HAS_7Z, reason="requires the 7z binary")
@pytest.mark.asyncio
async def test_normalize_7z_extracts_and_repacks(tmp_path):
    # Build a real .7z holding a .osz, then verify normalize yields a zip
    # the bulk-importer can iterate down to the inner .osu.
    work = tmp_path / "work"
    work.mkdir()
    _make_osz(str(work / "song.osz"))
    archive = tmp_path / "pack.7z"
    subprocess.run(
        ["7z", "a", str(archive), str(work)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    import_path, cleanup = await normalize_single_archive(str(archive))
    try:
        assert cleanup is not None
        assert import_path.endswith(".zip")
        # Repacked archive is a valid zip containing the .osz...
        with zipfile.ZipFile(import_path) as zf:
            assert any(n.lower().endswith(".osz") for n in zf.namelist())
        # ...and the bulk-importer drills through to the .osu.
        entries = list(_iter_osu_entries_from_archive(import_path))
        assert len(entries) == 1
        assert entries[0][0].lower().endswith(".osu")
    finally:
        shutil.rmtree(cleanup, ignore_errors=True)

"""Unit tests for bsk_pool._cleanup_stale_imports orphan sweeping.

Focus: the .7z extraction dirs (import7z_*) left behind when an import is
killed mid-flight must be rmtree'd once stale, while in-flight dirs (recent
mtime, or referenced by a queued/running slot via extract_dir) are kept.
"""

from __future__ import annotations

import os
from datetime import datetime

import bot.handlers.admin.bsk_pool as bp


def _touch_dir(path: str, *, age_seconds: float) -> None:
    os.makedirs(path, exist_ok=True)
    # Put a file inside so it's a real non-empty tree.
    with open(os.path.join(path, "repacked.zip"), "wb") as f:
        f.write(b"PK\x03\x04")
    t = datetime.utcnow().timestamp() - age_seconds
    os.utime(path, (t, t))


def _touch_file(path: str, *, age_seconds: float) -> None:
    with open(path, "wb") as f:
        f.write(b"x")
    t = datetime.utcnow().timestamp() - age_seconds
    os.utime(path, (t, t))


def test_sweeps_stale_7z_dir_keeps_recent_and_active(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "IMPORT_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(bp, "_import_queue", {})

    old_dir = str(tmp_path / "import7z_OLD")
    new_dir = str(tmp_path / "import7z_NEW")
    active_dir = str(tmp_path / "import7z_ACTIVE")
    old_file = str(tmp_path / "import_OLDFILE")

    over_ttl = bp.IMPORT_PENDING_TTL_SECONDS + 120
    _touch_dir(old_dir, age_seconds=over_ttl)       # stale, orphan → swept
    _touch_dir(new_dir, age_seconds=10)             # fresh → kept
    _touch_dir(active_dir, age_seconds=over_ttl)    # stale but referenced → kept
    _touch_file(old_file, age_seconds=over_ttl)     # stale download → removed

    # A running slot whose extraction dir must be protected.
    bp._import_queue["slot1"] = {
        "tg_id": 1, "file_path": str(tmp_path / "import_inflight"),
        "filename": "p.7z", "status": "running",
        "size": 0, "created_at": datetime.utcnow(),
        "extract_dir": active_dir,
    }

    bp._cleanup_stale_imports()

    assert not os.path.exists(old_dir), "stale import7z_ dir should be rmtree'd"
    assert os.path.isdir(new_dir), "fresh extraction dir must be kept"
    assert os.path.isdir(active_dir), "active (referenced) dir must be kept"
    assert not os.path.exists(old_file), "stale download file should be removed"

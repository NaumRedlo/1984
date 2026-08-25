"""What a render worker is allowed to load.

A worker runs on somebody else's laptop, and what it imports is what they have
to install. It used to import the whole bot: Pillow, fontTools, SQLAlchemy, the
pp calculator and the database layer — none of which a render touches. Not one
of those was imported on purpose. `services/__init__.py` re-exported the card
renderer, so importing *anything* under `services` built the card stack, and
`utils/osu/__init__.py` did the same for the API client and the database.

That is the kind of coupling nothing announces. It costs nothing at runtime on
the machine that has all of it installed, and it is the whole install on a
machine that does not — so it needs a test rather than a note.

Run in a fresh interpreter on purpose. Asking `sys.modules` from inside pytest
answers about pytest, which has already imported half the tree.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Packages a render has no business needing. Each one was actually being loaded
# before the re-exports went lazy.
FORBIDDEN = ("aiogram", "sqlalchemy", "aiosqlite", "PIL", "fontTools",
             "rosu_pp_py", "cryptography", "db")

_PROBE = """
import importlib.util, sys
before = set(sys.modules)
spec = importlib.util.spec_from_file_location("render_worker", %r)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
loaded = {name.split(".")[0] for name in set(sys.modules) - before}
print(" ".join(sorted(loaded)))
"""


def _loaded_by_the_worker() -> set[str]:
    probe = _PROBE % os.path.join(ROOT, "scripts", "render_worker.py")
    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT, capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": ROOT},
    )
    assert done.returncode == 0, f"the worker would not import:\n{done.stderr}"
    return set(done.stdout.split())


def test_a_worker_does_not_import_the_bot():
    loaded = _loaded_by_the_worker()
    unwanted = sorted(loaded & set(FORBIDDEN))
    assert not unwanted, (
        f"the worker imports {', '.join(unwanted)} — nothing about a render "
        f"needs any of it, and a worker on somebody's laptop installs what it "
        f"imports. Something re-exported eagerly again; see "
        f"services/__init__.py."
    )


def test_the_worker_still_imports_what_it_does_need():
    """The guard above passes trivially if the worker stopped importing at all
    — a broken probe would report an empty set and call it clean."""
    loaded = _loaded_by_the_worker()
    assert {"aiohttp", "services", "utils", "config"} <= loaded


def _pinned(path: str) -> dict[str, str]:
    lines = open(os.path.join(ROOT, path), encoding="utf-8").read().splitlines()
    found = {}
    for line in lines:
        line = line.split("#")[0].strip()
        if not line:
            continue
        for mark in ("==", ">=", "~="):
            if mark in line:
                name, _, version = line.partition(mark)
                found[name.strip().lower()] = mark + version.strip()
                break
    return found


def test_the_workers_requirements_do_not_drift_from_the_bots():
    """Two files naming the same packages will disagree eventually, and the
    disagreement shows up as a worker that renders differently."""
    bot, worker = _pinned("requirements.txt"), _pinned("requirements-worker.txt")
    assert worker, "requirements-worker.txt names nothing"
    for name, version in worker.items():
        assert name in bot, f"{name} is in the worker's list and not the bot's"
        assert bot[name] == version, (
            f"{name}: the bot pins {bot[name]} and the worker {version}"
        )


def test_the_workers_list_is_actually_shorter():
    """The point of the file. If it ever grows to the bot's size, it has
    stopped being worth having."""
    bot, worker = _pinned("requirements.txt"), _pinned("requirements-worker.txt")
    assert len(worker) < len(bot) / 2

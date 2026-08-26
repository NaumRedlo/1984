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
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Packages a render has no business needing. Each one was actually being loaded
# before the re-exports went lazy.
FORBIDDEN = ("aiogram", "sqlalchemy", "aiosqlite", "PIL", "fontTools",
             "rosu_pp_py", "cryptography", "db")

# Importing the module is not enough, and that is the whole of why this was
# wrong once already. `main` imports `OsuApiClient` *inside the function*, so
# a probe that only loads the module never reaches it — the guard passed, and
# a worker on somebody's machine died on `No module named 'sqlalchemy'` the
# moment it was actually run.
#
# So the probe also imports what the deferred lines import. Not by calling
# `main`, which would go to the network: by naming them.
_PROBE = """
import importlib, importlib.util, sys
before = set(sys.modules)
spec = importlib.util.spec_from_file_location("render_worker", %r)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for late in ("utils.osu.api_client", "services.dossier"):
    importlib.import_module(late)
loaded = {name.split(".")[0] for name in set(sys.modules) - before}
print(" ".join(sorted(loaded)))
"""


def _loaded_by_the_worker(*, deferred: bool = True) -> set[str]:
    """What a fresh interpreter loads. `deferred` also pulls the imports the
    worker makes inside functions — the fallback for a bot too old to send the
    map with the job, which is the one part still reaching into the bot."""
    probe = _PROBE % os.path.join(ROOT, "scripts", "render_worker.py")
    if not deferred:
        probe = probe.replace('for late in ("utils.osu.api_client", "services.dossier"):',
                              'for late in ("services.dossier",):')
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


def test_what_the_worker_imports_late_is_covered_too():
    """The probe has to name the deferred imports, or it tests less than it
    looks like it tests. `main` does `from utils.osu.api_client import
    OsuApiClient` inside the function — this list is what stands in for that,
    and it has to be kept up with the ones the worker actually defers.
    """
    source = open(os.path.join(ROOT, "scripts", "render_worker.py")).read()
    # `[ \t]` and not `\s`: `\s` matches a newline, so `^\s+from` happily
    # spans a blank line and reports a top-level import as an indented one.
    deferred = set(re.findall(r"^[ \t]+from ([\w.]+) import ", source, re.M))
    covered = set(re.findall(r'"([\w.]+)"', _PROBE))
    # Only the ones that reach outside the worker's own three packages.
    outside = {
        name for name in deferred
        if name.split(".")[0] in ("utils", "services", "bot", "db", "config")
    }
    missed = outside - covered
    assert not missed, (
        f"the worker defers {', '.join(sorted(missed))} and the probe never "
        f"loads them, so this guard is not looking at what a run would"
    )


def test_the_client_does_not_read_the_bots_settings():
    """The one seam that held the bridge inside this repository.

    `services/dossier` plus the two beatmap helpers is what the bot and a
    worker both use, and it took its configuration from `config.settings` —
    the *bot's* module, fifty-odd values of which ten were the bridge's. A
    worker importing it took the bot's whole configuration to learn where
    ffmpeg is.

    With that gone the bridge is a thing that can be lifted out, which is what
    the separate repository needs it to be.
    """
    # Without the deferred fallback: `utils.osu.api_client` is the bot's own
    # client, kept only for a bot older than this worker, and it goes with the
    # split rather than being untangled here.
    loaded = _loaded_by_the_worker(deferred=False)
    assert "config" not in loaded, (
        "the render client is reading the bot's settings again — see "
        "services/dossier/settings.py for where its own live"
    )


def test_one_definition_of_each_setting_and_not_two():
    """The bot re-exports them rather than declaring them again. Two
    declarations would disagree eventually, and the disagreement would be
    about which binary to run."""
    bot = open(os.path.join(ROOT, "config", "settings.py")).read()
    for name in ("DOSSIER_BIN", "SKIN_STORE_DIR", "BEATMAP_STORE_DIR", "MAX_SKIN_MB"):
        assert f"{name} = os.getenv" not in bot, (
            f"{name} is declared in the bot's settings as well as the bridge's"
        )
        assert name in bot, f"{name} is no longer re-exported, and the bot reads it"

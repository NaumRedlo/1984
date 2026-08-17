"""Skins the bot holds, and getting one out of an `.osk`.

An `.osk` is a zip somebody sent us, which is the whole reason this file is
careful. Three things can go wrong with a stranger's archive and all three are
guarded here rather than hoped about:

- **A path that escapes the folder.** `../../.ssh/authorized_keys` is a valid
  zip entry name. Nothing here builds a path out of one: only the last segment
  of an entry is used, so an escaping name becomes a file called `passwd` in
  the store and goes no further. The realpath check that follows is a guard
  against that reasoning being wrong, not the defence itself — if it ever
  fires, the import stops rather than continuing on a wrong assumption.
- **An archive that unpacks to more than the disk holds.** A few hundred
  kilobytes of zip can be gigabytes of zeroes, so the declared total is checked
  first and the written total is counted as it goes, because the declaration is
  the attacker's to write.
- **Depth.** osu! reads only the top of a skin folder, so nothing below it is
  worth keeping and subdirectories are dropped on the way in. That is also what
  the renderer does when it reads one — see `dossier-render`'s `imported.rs`,
  and the `cursors/` folder that taught it.

What comes out is a folder the engine can be pointed at with `--skin`.
"""

import hashlib
import os
import re
import shutil
import subprocess
import zipfile

from config.settings import DOSSIER_FFMPEG, SKIN_STORE_DIR
from utils.logger import get_logger

logger = get_logger("services.dossier.skins")


class SkinRejected(RuntimeError):
    """The archive is not something we will unpack. The message is shown to
    whoever sent it."""


# A skin is pictures and sounds. Fifty megabytes is roomier than any real one
# and small enough that a hundred of them still fit somewhere sensible.
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
# Past this an archive is not a skin. The one this was built against holds 232.
MAX_FILES = 2000

# What osu! reads. Everything else in an archive — readmes, sources, the
# author's own screenshots — is weight we would carry to a worker for nothing.
KEPT_SUFFIXES = (".png", ".jpg", ".wav", ".mp3", ".ogg", ".ini")


def store_dir() -> str:
    return os.path.expanduser(SKIN_STORE_DIR)


def _safe_name(name: str) -> str:
    """A folder name from whatever the file was called.

    Kept to what cannot surprise a filesystem or a command line, since this
    ends up as both.
    """
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]", "", stem).strip(" .")
    return (cleaned or "skin")[:48]


def available() -> list[str]:
    """Every skin in the store, by name."""
    try:
        return sorted(
            entry.name
            for entry in os.scandir(store_dir())
            if entry.is_dir() and not entry.name.startswith(".")
        )
    except OSError:
        return []


def folder_of(name: str) -> str | None:
    """Where a stored skin lives, or None if it is not one.

    The name is checked against the listing rather than joined onto the store
    and hoped about: it arrives from a callback, and a callback is user input.
    """
    if name in available():
        return os.path.join(store_dir(), name)
    return None


def forget(name: str) -> bool:
    folder = folder_of(name)
    if not folder:
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


def import_osk(archive_path: str, filename: str) -> str:
    """Unpack an `.osk` into the store and return the name it was filed under.

    Replaces a skin of the same name: sending the file again is how somebody
    updates one, and asking them to delete it first would be a step with no
    purpose.
    """
    os.makedirs(store_dir(), exist_ok=True)
    name = _safe_name(filename)
    destination = os.path.join(store_dir(), name)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            if len(entries) > MAX_FILES:
                raise SkinRejected(
                    f"в архиве {len(entries)} файлов — это не скин"
                )
            declared = sum(item.file_size for item in entries)
            if declared > MAX_UNPACKED_BYTES:
                raise SkinRejected(
                    f"скин распакуется в {declared // 1024 // 1024} МБ, "
                    f"а больше {MAX_UNPACKED_BYTES // 1024 // 1024} МБ мы не берём"
                )

            staging = destination + ".incoming"
            shutil.rmtree(staging, ignore_errors=True)
            os.makedirs(staging, exist_ok=True)
            try:
                written = _extract(archive, entries, staging)
                _to_wav(staging)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
    except zipfile.BadZipFile as exc:
        raise SkinRejected(f"файл не читается как архив: {exc}") from exc

    if written == 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise SkinRejected("в архиве нет ничего, что движок умеет читать")

    # Swapped in only once it is whole, so a failed import never leaves a
    # half-unpacked skin somebody can select and render with.
    shutil.rmtree(destination, ignore_errors=True)
    os.replace(staging, destination)
    logger.info("imported skin %s: %d file(s)", name, written)
    return name


def _extract(archive: zipfile.ZipFile, entries, into: str) -> int:
    """Write the entries worth keeping, flatly, and say how many there were."""
    root = os.path.realpath(into)
    total = 0
    written = 0
    for item in entries:
        # Flattened: osu! reads only the top of a skin folder, and an archive
        # that wraps its files in one — most of them do — would otherwise
        # unpack into a folder the engine finds empty.
        leaf = os.path.basename(item.filename)
        if not leaf or not leaf.lower().endswith(KEPT_SUFFIXES):
            continue
        target = os.path.realpath(os.path.join(root, leaf))
        if os.path.commonpath([root, target]) != root:
            # Unreachable if `basename` does what it says, which is the point:
            # a guard on the reasoning above rather than the defence. Reached,
            # it means the assumption is wrong and going on would be worse than
            # stopping.
            raise SkinRejected("в архиве путь, ведущий за пределы папки")

        with archive.open(item) as source, open(target, "wb") as sink:
            while chunk := source.read(1 << 16):
                total += len(chunk)
                if total > MAX_UNPACKED_BYTES:
                    # The declared size was checked already; this is the same
                    # question asked of what actually arrived, because the
                    # declaration is written by whoever made the archive.
                    raise SkinRejected("архив распаковывается больше, чем обещал")
                sink.write(chunk)
        written += 1
    return written


# What the engine can read a sample from, and what it cannot.
#
# `dossier-audio` has no dependencies — it decodes WAV and nothing else, which
# is the same from-scratch rule the rest of the engine is written to. Skins do
# not care: the one this was reported against ships every hitsound as `.ogg`,
# so the engine found no samples at all and the render came out silent while
# the pictures worked perfectly.
#
# Converted here rather than taught to the engine. Adding a Vorbis decoder to
# `dossier-audio` means either a dependency it has never had or several
# thousand lines of codebooks and an MDCT; ffmpeg is already required to mux a
# render, and this way the work is done once when a skin arrives instead of on
# every render that wears it.
FOREIGN_AUDIO = (".ogg", ".mp3")


def _to_wav(folder: str) -> None:
    """Turn every sample the engine cannot read into one it can.

    Best effort per file. A sample that will not convert is left as it was and
    the skin is still imported: a missing hitsound is a quieter render, and
    refusing the whole skin over one file would be a worse answer than the one
    the skin already had.
    """
    for leaf in sorted(os.listdir(folder)):
        if not leaf.lower().endswith(FOREIGN_AUDIO):
            continue
        source = os.path.join(folder, leaf)
        target = os.path.join(folder, os.path.splitext(leaf)[0] + ".wav")
        if os.path.exists(target):
            # The skin shipped both. Its own `.wav` is the one osu! would pick.
            continue
        try:
            done = subprocess.run(
                [DOSSIER_FFMPEG, "-nostdin", "-v", "error", "-y", "-i", source,
                 "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", target],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("could not convert %s: %s", leaf, exc)
            return
        if done.returncode != 0:
            logger.warning(
                "ffmpeg refused %s: %s", leaf,
                done.stderr.decode("utf-8", "replace").strip()[:200],
            )
            # Half a file is worse than none — the engine would read it.
            if os.path.exists(target):
                os.remove(target)


def convert_stored() -> int:
    """Give every stored skin the samples the engine can read, and say how many
    folders needed it.

    A skin arriving now is converted on the way into the store, and one that
    arrived before that existed is silent — reported as exactly that: some
    hitsounds not being found. The alternative was asking somebody to re-send
    every skin they had ever sent, which is a worse answer than a sweep that
    costs a directory listing per skin on the days it finds nothing.

    Cheap to repeat. `_to_wav` skips a file whose `.wav` already exists, so the
    second run of this does nothing at all.
    """
    store = store_dir()
    if not os.path.isdir(store):
        return 0
    touched = 0
    for name in sorted(os.listdir(store)):
        folder = os.path.join(store, name)
        if not os.path.isdir(folder):
            continue
        try:
            leaves = os.listdir(folder)
        except OSError:
            continue
        foreign = [f for f in leaves if f.lower().endswith(FOREIGN_AUDIO)]
        # Only the folders that have something to gain: a skin whose `.ogg`
        # files already have `.wav` beside them is done, and re-asking ffmpeg
        # about each one would make startup pay for nothing.
        if not foreign:
            continue
        stems = {os.path.splitext(f)[0].lower() for f in leaves if f.lower().endswith(".wav")}
        if all(os.path.splitext(f)[0].lower() in stems for f in foreign):
            continue
        _to_wav(folder)
        touched += 1
    if touched:
        logger.info("converted samples in %d stored skin(s)", touched)
    return touched


def packed(name: str) -> tuple[str, str] | None:
    """The skin as one zip, and the hash of it. `None` if there is no such skin.

    One file rather than a hundred and sixty-seven, because a worker fetches
    these over the network and a round trip per element would cost more than
    the pictures do. The hash is what lets it skip the fetch entirely on the
    second render with the same skin.

    Built once and kept beside the store, rebuilt when anything in the folder
    is newer than it — a skin changes when somebody sends the file again, which
    is rare, and rezipping five megabytes for every render is not free.
    """
    folder = folder_of(name)
    if not folder:
        return None
    files = sorted(
        (entry.name, entry.stat().st_mtime)
        for entry in os.scandir(folder)
        if entry.is_file()
    )
    if not files:
        return None

    archive = os.path.join(store_dir(), f".{name}.zip")
    newest = max(mtime for _, mtime in files)
    if not os.path.exists(archive) or os.path.getmtime(archive) < newest:
        staging = archive + ".building"
        with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as out:
            for leaf, _ in files:
                out.write(os.path.join(folder, leaf), leaf)
        os.replace(staging, archive)

    digest = hashlib.sha256()
    with open(archive, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return archive, digest.hexdigest()[:16]

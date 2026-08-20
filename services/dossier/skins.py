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
import json
import os
import re
import shutil
import subprocess
import time
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


# What unpacking a skin does, as a number that goes up when it changes.
#
# A skin folder is unpacked once and used for ever after, so a fix to the
# unpacking does nothing for the skins already in the store — they keep the
# result of the code that was running the day they arrived, and they keep it
# silently. That is not a hypothetical: the root-first rule below was added
# after `vv_idke_trail` imported with the wrong combo numbers and the wrong hit
# sounds, and every folder unpacked before it stayed wrong afterwards. Forty-two
# files of it, including every core hit sound, and it took an evening to find
# because nothing anywhere said the folder was old.
#
# So each folder records which unpacking made it, and a folder made by an older
# one is *reported*. It cannot be repaired here: the store keeps the unpacked
# skin and not the `.osk`, so the only way back is somebody sending the archive
# again. Saying so is the whole job.
#
# Bump this whenever `_extract` or `_to_wav` changes what comes out.
#
#   1  the first stamped unpacking: root-first extraction, `.ogg`/`.mp3`
#      converted on the way in, and a `.wav` the engine cannot decode
#      re-encoded over itself
EXTRACT_VERSION = 1

# Where that is written, inside the folder it describes. Leading dot so it is
# not mistaken for an element: the engine indexes every file at the top of a
# skin and a name it does not know is simply never asked for.
STAMP = ".dossier-import.json"


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

    _write_stamp(staging, filename, written)

    # Swapped in only once it is whole, so a failed import never leaves a
    # half-unpacked skin somebody can select and render with.
    shutil.rmtree(destination, ignore_errors=True)
    os.replace(staging, destination)
    logger.info("imported skin %s: %d file(s)", name, written)
    return name


def _write_stamp(folder: str, filename: str, written: int) -> None:
    """Record what unpacked this folder, inside the folder.

    Written into the staging copy, before the swap, so a folder that exists is
    always stamped and one that is half-made is never seen at all.
    """
    body = {
        "extract_version": EXTRACT_VERSION,
        "source": filename,
        "files": written,
        "at": int(time.time()),
    }
    try:
        with open(os.path.join(folder, STAMP), "w", encoding="utf-8") as handle:
            json.dump(body, handle, ensure_ascii=False, indent=1)
    except OSError:
        # A skin that unpacked fine is worth more than its stamp. Losing the
        # stamp reads as "unpacked by something older", which is the safe way
        # round: it asks for a re-send that is not needed rather than hiding one
        # that is.
        logger.warning("could not stamp %s", folder)


def stamp_of(folder: str) -> dict:
    """What the folder says about its own unpacking.

    An unstamped folder reports version 0 — every skin in every store predates
    this, and none of them were made by the current code.
    """
    try:
        with open(os.path.join(folder, STAMP), encoding="utf-8") as handle:
            body = json.load(handle)
    except (OSError, ValueError):
        return {"extract_version": 0}
    if not isinstance(body, dict):
        return {"extract_version": 0}
    body.setdefault("extract_version", 0)
    return body


def is_stale(name: str) -> bool:
    """Whether this skin was unpacked by code older than what is running.

    A skin nobody has is not stale; it is absent, which is a different answer
    and a different message.
    """
    folder = folder_of(name)
    # Nothing there is not stale. A folder that does not exist needs no
    # re-unpacking, and calling it stale would ask somebody to send an archive
    # for a skin they do not have.
    if folder is None or not os.path.isdir(folder):
        return False
    return int(stamp_of(folder).get("extract_version") or 0) < EXTRACT_VERSION


def stale() -> list[str]:
    """Every skin in the store that needs sending again."""
    return [name for name in available() if is_stale(name)]


def _extract(archive: zipfile.ZipFile, entries, into: str) -> int:
    """Write the entries worth keeping, flatly, and say how many there were.

    Flattened, because osu! reads only the top of a skin folder and an archive
    that wraps its files in one — most of them do — would otherwise unpack into
    a folder the engine finds empty.

    But flattening alone lets a skin overwrite itself. `vv_idke_trail` ships 41
    names twice: `default-0.png` beside `num/default-0.png`, and every hit sound
    in the root beside a copy in `hitsound/`. osu! reads the root ones and never
    opens those folders, so whichever landed last here was a file the game would
    never have used — which is why that skin imported with the wrong combo
    numbers and the wrong hit sounds, and why it looked like the map's.

    So: the root wins, always. A file inside a folder is written only where the
    root has nothing of that name — which is what rescues the sets a skin keeps
    only in a folder and names through `[Fonts] ScorePrefix: num\berlin`.
    """
    root = os.path.realpath(into)
    total = 0
    written = 0
    # The root first, so that nothing below it can take a name the root claims.
    ordered = sorted(entries, key=lambda item: item.filename.replace("\\", "/").count("/"))
    for item in ordered:
        leaf = os.path.basename(item.filename.replace("\\", "/"))
        if not leaf or not leaf.lower().endswith(KEPT_SUFFIXES):
            continue
        nested = "/" in item.filename.replace("\\", "/")
        if nested and os.path.exists(os.path.join(root, leaf)):
            # The game would read the root's copy and never this one.
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


def _readable_wav(path: str) -> bool:
    """Whether `dossier-audio` can get a sample out of this `.wav`.

    The same rules its `decode_wav` applies: a RIFF/WAVE container, PCM (format
    tag 1), 8 or 16 bits, one channel or two. Anything else — GSM 6.10, IEEE
    float, 24-bit, or a file that is not RIFF at all because somebody renamed an
    `.ogg` to `.wav` — it refuses, and the render falls back to synthesis or to
    silence while osu! plays the file perfectly well through BASS.

    A header with an empty `data` chunk counts as readable, and deliberately so:
    that is how a skin silences an element, the engine decodes it to nothing,
    and osu! does the same. `ResourceStore.Get` returns the first result that is
    not null, and a blank file is `byte[0]` rather than null — so the blank wins
    over any `.ogg` beside it there too.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
            if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
                return False
            # Chunks are not in a guaranteed order, so walk rather than assume.
            while True:
                header = handle.read(8)
                if len(header) < 8:
                    break
                size = int.from_bytes(header[4:8], "little")
                if header[0:4] == b"fmt " and size >= 16:
                    body = handle.read(size + (size & 1))
                    if len(body) < 16:
                        return False
                    tag = int.from_bytes(body[0:2], "little")
                    channels = int.from_bytes(body[2:4], "little")
                    bits = int.from_bytes(body[14:16], "little")
                    return tag == 1 and channels in (1, 2) and bits in (8, 16)
                handle.seek(size + (size & 1), os.SEEK_CUR)
    except OSError:
        return False
    return False


def _has_bytes(path: str) -> bool:
    """Whether there is anything in the file at all.

    Skins carry empty files. The two in hand ship four zero-byte
    `nightcore-*.ogg` apiece, and asking ffmpeg about one earns a multi-line
    complaint in the log and produces nothing — every start, for ever, because
    a file with no bytes can never gain the `.wav` that would mark it done.
    """
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _unconverted(folder: str) -> list[tuple[str, str]]:
    """Every sample here the engine cannot read, as (source, target) pairs.

    Two kinds, and the second is the one that was being missed. A `.ogg` or
    `.mp3` with no `.wav` beside it is the obvious one. The other is a `.wav`
    the engine cannot decode — GSM 6.10, or an `.ogg` somebody renamed — which
    osu! plays and we heard as silence, and which used to be skipped on the
    grounds that a `.wav` existed. That one is re-encoded over itself rather
    than replaced by a sibling `.ogg`: it is the file osu! would pick, so it is
    the file whose contents have to come out.
    """
    work = []
    try:
        leaves = sorted(os.listdir(folder))
    except OSError:
        return work
    for leaf in leaves:
        path = os.path.join(folder, leaf)
        lower = leaf.lower()
        if lower.endswith(FOREIGN_AUDIO):
            target = os.path.join(folder, os.path.splitext(leaf)[0] + ".wav")
            # The skin shipped both, and its own `.wav` is the one osu! would
            # pick — so long as we can read it. When we cannot, the `.wav` is
            # dealt with below on its own terms.
            if not os.path.exists(target) and _has_bytes(path):
                work.append((path, target))
        elif lower.endswith(".wav") and _has_bytes(path) and not _readable_wav(path):
            work.append((path, path))
    return work


def _to_wav(folder: str) -> None:
    """Turn every sample the engine cannot read into one it can.

    Best effort per file. A sample that will not convert is left as it was and
    the skin is still imported: a missing hitsound is a quieter render, and
    refusing the whole skin over one file would be a worse answer than the one
    the skin already had.
    """
    for source, target in _unconverted(folder):
        leaf = os.path.basename(source)
        # ffmpeg will not read and write the same path, so a file being
        # re-encoded over itself goes via a neighbour and is moved into place.
        in_place = source == target
        written = target + ".converting" if in_place else target
        try:
            done = subprocess.run(
                [DOSSIER_FFMPEG, "-nostdin", "-v", "error", "-y", "-i", source,
                 "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", "-f", "wav",
                 written],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("could not convert %s: %s", leaf, exc)
            return
        if done.returncode == 0 and in_place:
            os.replace(written, target)
        if done.returncode != 0:
            # One line of it. ffmpeg answers a bad file with a paragraph, and a
            # paragraph per file per skin is what a journal looks like when
            # nothing is wrong.
            said = done.stderr.decode("utf-8", "replace").strip().splitlines()
            logger.warning("ffmpeg refused %s: %s", leaf, said[0][:160] if said else "")
            # Half a file is worse than none — the engine would read it. The
            # original is left alone: it is unreadable to us either way, and
            # deleting a skin's own file over our inability to decode it would
            # be the worse of the two.
            if os.path.exists(written) and not (in_place and written == target):
                os.remove(written)


def convert_folder(folder: str) -> int:
    """Make one folder readable, and say how many files it took.

    For a worker, which unpacks skins into a cache of its own and would
    otherwise be at the mercy of whether the bot's store had been swept before
    the zip was built. A skin that reached a machine unreadable stayed
    unreadable there for as long as the cache kept it, and the render was silent
    with a perfectly good skin on disk.
    """
    work = _unconverted(folder)
    if work:
        _to_wav(folder)
    return len(work)


def convert_stored() -> int:
    """Give every stored skin the samples the engine can read, and say how many
    folders needed it.

    A skin arriving now is converted on the way into the store, and one that
    arrived before that existed is silent — reported as exactly that: some
    hitsounds not being found. The alternative was asking somebody to re-send
    every skin they had ever sent, which is a worse answer than a sweep that
    costs a directory listing per skin on the days it finds nothing.

    Cheap to repeat. The second run of this finds nothing to do and says so by
    doing nothing at all.
    """
    store = store_dir()
    if not os.path.isdir(store):
        return 0
    touched = 0
    for name in sorted(os.listdir(store)):
        folder = os.path.join(store, name)
        if not os.path.isdir(folder):
            continue
        # One question, asked by the thing that would do the work: is there
        # anything here the engine cannot read. It used to be asked separately —
        # "does every `.ogg` have a `.wav`" — which said yes for a skin whose
        # `.wav` was a renamed `.ogg` or GSM-encoded, and those were the ones
        # coming out silent.
        if not _unconverted(folder):
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

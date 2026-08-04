#!/usr/bin/env python3
"""Fetch the beatmaps the corpus is missing.

A replay names its beatmap by MD5 and nothing else, so a replay whose map is
not on disk cannot be measured at all — it does not disagree with us, it is
simply absent, which is the worse of the two. This closes that gap.

The file comes from `https://osu.ppy.sh/osu/<id>`: the official raw .osu
endpoint, no key, no mirror. That matters because a mirror only has what
somebody uploaded to it, while ppy has every map that exists — the maps that
"are not on the mirror" are, in fact, one request away.

The id is found in one of two ways, cheapest first:

  1. Server-downloaded replays are named `solo-replay-osu_<beatmap>_<score>`.
     The beatmap id is right there and no third party is involved.
  2. Otherwise the MD5 is looked up on a mirror, which is the one thing a
     mirror can do that ppy cannot: turn a hash into an id. Two are tried,
     because their indexes differ: catboy answers for ranked and loved maps
     and 404s on the rest, while osu.direct also carries graveyard — which is
     most of what a replay from a friend is played on.

Every download is checked three ways before it is kept, following the same
discipline as osu!'s own BeatmapStore: it must start with `osu file format v`
(otherwise an error page lands in the cache and stays there), it must be under
the size ceiling, and **its MD5 must be the one the replay asked for**. That
last check is not paranoia — ppy serves the map as it is now, and a map that
was revised since the replay was set will come back a different file. When
that happens the honest outcome is "this revision is gone", not a silent
measurement against the wrong notes.

    tools/fetch-maps.py --songs ~/.osu/Songs ~/Replays/*.osr
    tools/fetch-maps.py --songs ~/.osu/Songs --dry-run ~/Replays/*.osr

Maps land in the songs directory as `<hash>.osu`, which `locate::search_dir`
prefers over archives — hashing one file is a read, an .osz is an inflate.

## `--with-audio`

A bare .osu is the notes and nothing else, so a render of it is silent. With
this flag the whole beatmapset comes down from a mirror instead, as
`<hash>.osz`, and the engine pulls the song out of the archive the same way it
would from a folder of somebody's osu! install.

ppy has no endpoint for this — `/osu/<id>` is the single difficulty, and the
set download wants a logged-in session — so here the mirrors are not a fallback
but the only road. Four are tried, the same four and in the same order the bot
uses, for the same reason: their indexes differ, and one behind a 522 today is
fine tomorrow.

The archive replaces any bare .osu of the same hash rather than sitting beside
it. `search_dir` prefers loose files, so leaving both would keep the silent one
winning and the download would have bought nothing.
"""

import argparse
import hashlib
import json
import os
import pathlib
import io
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

# osu!'s own ceiling, from BeatmapStore.cs. A .osu past this is not a beatmap.
MAX_BEATMAP_BYTES = 50 * 1024 * 1024
PPY_RAW = "https://osu.ppy.sh/osu/{id}"
MIRRORS_MD5 = (
    "https://catboy.best/api/v2/md5/{hash}",
    "https://osu.direct/api/v2/md5/{hash}",
)
# Whole beatmapsets, for `--with-audio`. The bot's list, in the bot's order —
# see utils/osu/beatmap_download.py for what each one is worth.
MIRRORS_OSZ = (
    "https://catboy.best/d/{set_id}",
    "https://api.nerinyan.moe/d/{set_id}",
    "https://beatconnect.io/b/{set_id}",
    "https://osu.direct/d/{set_id}",
)
# A set with a video is a hundred megabytes of something nobody renders.
MAX_SET_BYTES = 200 * 1024 * 1024
# Mirrors behind Cloudflare answer a Python UA with a challenge page.
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Server-downloaded replays carry the beatmap id in their name.
SOLO_REPLAY = re.compile(r"solo-replay-\w+_(\d+)_\d+")
TIMEOUT = 20
# A set is two orders of magnitude larger than one difficulty.
SET_TIMEOUT = 120


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "dossier/0.1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read(MAX_BEATMAP_BYTES + 1)


def get_set(url):
    """A whole beatmapset, with a browser's user agent and a longer patience.

    Separate from `get` on both counts. A .osz is two orders of magnitude
    larger than a .osu, so the twenty seconds that is generous for one file is
    not for a set — and mirrors sitting behind Cloudflare answer a Python user
    agent with a challenge page rather than an archive, which is a 200 with
    HTML in it and would otherwise be kept as a beatmap.
    """
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(request, timeout=SET_TIMEOUT) as response:
        return response.read(MAX_SET_BYTES + 1)


def set_id_of(want_hash):
    """The beatmapset the map belongs to, from a mirror's hash index.

    A different number from the beatmap id the rest of this tool pins: one
    difficulty against the set it ships in. ppy's raw endpoint takes the first
    and the mirrors' downloads take the second, so a run that wants audio has
    to ask for both.
    """
    for mirror in MIRRORS_MD5:
        try:
            entry = json.loads(get(mirror.format(hash=want_hash)))
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("beatmapset_id"):
            return int(entry["beatmapset_id"])
    return None


def keep_set(content, want_hash, into):
    """Store the archive, once it is one and once it holds the map we asked for.

    The same three checks the single file gets, adapted: a size ceiling, that
    it is really a zip rather than a mirror's error page, and that a difficulty
    inside hashes to what the replay named. That last one is the whole point —
    a set download is not addressed by hash, so without it a revised set would
    be kept as if it were the revision that was played.
    """
    if not content:
        return "empty"
    if len(content) > MAX_SET_BYTES:
        return f"over {MAX_SET_BYTES // (1024 * 1024)}MB"
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return "not an archive"
    found = False
    for name in names:
        if not name.lower().endswith(".osu"):
            continue
        try:
            if hashlib.md5(archive.read(name)).hexdigest() == want_hash:
                found = True
                break
        except (zipfile.BadZipFile, OSError):
            continue
    if not found:
        return "the set does not hold this revision"
    temporary = into.with_suffix(".osz.part")
    temporary.write_bytes(content)
    os.replace(temporary, into)
    # `search_dir` prefers a loose .osu over an archive, so a bare one left
    # beside this would keep winning and the download would have bought nothing.
    bare = into.with_suffix(".osu")
    if bare.exists():
        bare.unlink()
    return None


def fetch_set(want_hash, songs):
    """Try every mirror in turn. The last complaint is the one worth reporting."""
    set_id = set_id_of(want_hash)
    if set_id is None:
        return "no mirror knows this hash"
    why = "no mirror answered"
    for mirror in MIRRORS_OSZ:
        host = mirror.split("/")[2]
        try:
            content = get_set(mirror.format(set_id=set_id))
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            why = f"{host}: {error}"
            continue
        problem = keep_set(content, want_hash, songs / f"{want_hash}.osz")
        if problem is None:
            return None
        why = f"{host}: {problem}"
    return f"set {set_id}: {why}"


def headers_of(dossier, replays):
    """What map does each replay want? `inspect` reads the header alone."""
    out = subprocess.run(
        [dossier, "inspect", "--json", *[str(r) for r in replays]],
        capture_output=True,
        text=True,
    ).stdout
    wanted = {}
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("beatmap_hash"):
            wanted.setdefault(entry["beatmap_hash"], []).append(entry["replay"])
    return wanted


def on_disk(songs):
    """Every map hash already under the songs directory, loose or archived."""
    have = set()
    for path in songs.rglob("*"):
        if path.suffix.lower() == ".osu":
            have.add(hashlib.md5(path.read_bytes()).hexdigest())
        elif path.suffix.lower() == ".osz":
            try:
                archive = zipfile.ZipFile(path)
            except (zipfile.BadZipFile, OSError):
                continue
            for name in archive.namelist():
                if name.lower().endswith(".osu"):
                    try:
                        have.add(hashlib.md5(archive.read(name)).hexdigest())
                    except (zipfile.BadZipFile, OSError):
                        continue
    return have


def beatmap_id(want_hash, replays):
    """The id, from the replay's own name where possible, the mirror if not."""
    for replay in replays:
        found = SOLO_REPLAY.search(pathlib.Path(replay).name)
        if found:
            return int(found.group(1)), "name"
    unknown = True
    for mirror in MIRRORS_MD5:
        host = mirror.split("/")[2]
        try:
            body = get(mirror.format(hash=want_hash))
        except urllib.error.HTTPError as error:
            # A 404 is an answer: this mirror's index does not carry the map.
            # Anything else means we never got an answer at all, and saying so
            # separately is the difference between "gone" and "try again".
            if error.code != 404:
                unknown = False
            continue
        except (urllib.error.URLError, OSError, TimeoutError):
            unknown = False
            continue
        try:
            entry = json.loads(body)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("id"):
            return int(entry["id"]), host
    return None, "hash on no mirror" if unknown else "mirrors unreachable"


def keep(content, want_hash, into):
    """Store it, or say why it is not the file we asked for."""
    if len(content) > MAX_BEATMAP_BYTES:
        return f"over {MAX_BEATMAP_BYTES // (1024 * 1024)}MB"
    if not content:
        # ppy answers 200 with nothing at all for a map that has been deleted
        # since it was played. The id is real, the file is not.
        return "deleted from ppy"
    header = content[:64].decode("utf-8-sig", "replace").lstrip()
    if not header.startswith("osu file format v"):
        return "not a .osu file"
    got = hashlib.md5(content).hexdigest()
    if got != want_hash:
        # The map was revised after the replay was set. ppy has no way to
        # serve the older revision, so this one stays unmeasurable.
        return f"revised since ({got[:12]}…)"
    # Written beside the target and moved, so an interrupted fetch never
    # leaves half a beatmap where a whole one is expected.
    temporary = into.with_suffix(".osu.part")
    temporary.write_bytes(content)
    os.replace(temporary, into)
    return None


def read_manifest(path):
    """The corpus as written down: rows keyed by replay hash."""
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) == 7:
            rows.append(fields)
    return rows


def wanted_from_manifest(rows):
    """What to fetch, from the manifest alone — no replay files needed.

    This is the half of reproducibility the replays cannot provide. They are
    other people's plays and are not in the repository, but the maps they were
    played on are public, and the manifest says exactly which.
    """
    wanted = {}
    pinned = {}
    for replay_md5, beatmap_md5, beatmap_id, _error, _combo, _score, name in rows:
        wanted.setdefault(beatmap_md5, []).append(name)
        if beatmap_id != "-":
            pinned[beatmap_md5] = int(beatmap_id)
    return wanted, pinned


def write_manifest(path, resolved):
    """Put back the ids this run worked out, and nothing else.

    Pinning them is what stops the corpus depending on a mirror still
    answering hash lookups a year from now: with an id, the map comes
    straight from ppy.
    """
    lines = []
    filled = 0
    for line in path.read_text().splitlines():
        fields = line.split("\t")
        if line.startswith("#") or len(fields) != 7 or fields[2] != "-":
            lines.append(line)
            continue
        found = resolved.get(fields[1])
        if found is None:
            lines.append(line)
            continue
        fields[2] = str(found)
        filled += 1
        lines.append("\t".join(fields))
    path.write_text("\n".join(lines) + "\n")
    return filled


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("replays", nargs="*", type=pathlib.Path)
    parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        help="take the map list from the corpus manifest instead of from replay files, "
        "and pin back any beatmap id resolved along the way",
    )
    parser.add_argument(
        "--songs",
        type=pathlib.Path,
        default=os.environ.get("DOSSIER_SONGS_DIR"),
        help="where maps live, and where fetched ones are put",
    )
    parser.add_argument(
        "--dossier",
        default=os.environ.get("DOSSIER_BIN", "target/release/dossier"),
        help="the binary to read replay headers with",
    )
    parser.add_argument(
        "--with-audio",
        action="store_true",
        help="fetch whole beatmapsets from mirrors as <hash>.osz instead of bare .osu "
        "files, so renders have the map's music. Replaces any bare .osu already on "
        "disk, which the engine would otherwise keep preferring.",
    )
    parser.add_argument("--dry-run", action="store_true", help="say what is missing, fetch nothing")
    options = parser.parse_args()
    if not options.songs:
        parser.error("--songs is required (or set DOSSIER_SONGS_DIR)")
    songs = pathlib.Path(options.songs).expanduser()
    songs.mkdir(parents=True, exist_ok=True)

    pinned = {}
    if options.manifest:
        rows = read_manifest(options.manifest)
        wanted, pinned = wanted_from_manifest(rows)
        print(f"{len(rows)} rows name {len(wanted)} maps, {len(pinned)} with an id already")
    else:
        if not options.replays:
            parser.error("give replay paths, or --manifest")
        wanted = headers_of(options.dossier, options.replays)
        if not wanted:
            print("no replay headers could be read — is --dossier right?", file=sys.stderr)
            return 1
        replays = sum(len(r) for r in wanted.values())
        print(f"{replays} replays want {len(wanted)} maps")
    have = on_disk(songs)
    missing = {h: r for h, r in wanted.items() if h not in have}
    if options.with_audio:
        # A map already here as a bare .osu is not missing, but it is silent —
        # and silent is the thing this flag exists to fix, so it counts as
        # wanted until the archive replaces it.
        silent = {
            h: r
            for h, r in wanted.items()
            if h in have and not (songs / f"{h}.osz").exists()
        }
        missing.update(silent)
        print(f"{len(missing)} of {len(wanted)} are missing or silent")
    else:
        print(f"{len(missing)} of {len(wanted)} are missing")

    resolved = {}
    # An id worth pinning is one this run had to look up, whether or not the
    # file itself was missing — so rows that are only missing an id still get
    # one, without a download.
    unpinned = {h: r for h, r in wanted.items() if h not in pinned}
    if options.manifest and not options.dry_run:
        for want_hash, names in sorted(unpinned.items()):
            if want_hash in missing:
                continue  # resolved below, on the way to fetching it
            found, _how = beatmap_id(want_hash, names)
            if found is not None:
                resolved[want_hash] = found

    fetched, failed = 0, []
    for want_hash, for_replays in sorted(missing.items()):
        name = pathlib.Path(for_replays[0]).name
        if options.dry_run:
            print(f"  ?? {want_hash}  {name[:56]}")
            continue
        if want_hash in pinned:
            found, how = pinned[want_hash], "pinned"
        else:
            found, how = beatmap_id(want_hash, for_replays)
            if found is not None:
                resolved[want_hash] = found
        if found is None:
            failed.append((want_hash, name, how))
            print(f"  !! {want_hash}  {how}")
            continue
        if options.with_audio:
            why = fetch_set(want_hash, songs)
            if why:
                failed.append((want_hash, name, why))
                print(f"  !! {want_hash}  {why}")
            else:
                fetched += 1
                print(f"  ok {want_hash}  set with audio")
            continue
        try:
            content = get(PPY_RAW.format(id=found))
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            failed.append((want_hash, name, str(error)))
            print(f"  !! {want_hash}  id {found}: {error}")
            continue
        why = keep(content, want_hash, songs / f"{want_hash}.osu")
        if why:
            failed.append((want_hash, name, why))
            print(f"  !! {want_hash}  id {found}: {why}")
        else:
            fetched += 1
            print(f"  ok {want_hash}  id {found} ({how})")

    if options.dry_run:
        return 0
    if options.manifest and resolved:
        filled = write_manifest(options.manifest, resolved)
        print(f"{filled} row(s) pinned to {len(resolved)} beatmap id(s) in {options.manifest}")
    print(f"{fetched} fetched, {len(failed)} still missing")
    return 0 if fetched or not failed else 1


if __name__ == "__main__":
    sys.exit(main())

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
"""

import argparse
import hashlib
import json
import os
import pathlib
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
# Server-downloaded replays carry the beatmap id in their name.
SOLO_REPLAY = re.compile(r"solo-replay-\w+_(\d+)_\d+")
TIMEOUT = 20


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "dossier/0.1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read(MAX_BEATMAP_BYTES + 1)


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


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("replays", nargs="+", type=pathlib.Path)
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
    parser.add_argument("--dry-run", action="store_true", help="say what is missing, fetch nothing")
    options = parser.parse_args()
    if not options.songs:
        parser.error("--songs is required (or set DOSSIER_SONGS_DIR)")
    songs = pathlib.Path(options.songs).expanduser()
    songs.mkdir(parents=True, exist_ok=True)

    wanted = headers_of(options.dossier, options.replays)
    if not wanted:
        print("no replay headers could be read — is --dossier right?", file=sys.stderr)
        return 1
    have = on_disk(songs)
    missing = {h: r for h, r in wanted.items() if h not in have}

    replays = sum(len(r) for r in wanted.values())
    print(f"{replays} replays want {len(wanted)} maps; {len(missing)} are missing")
    if not missing:
        return 0

    fetched, failed = 0, []
    for want_hash, for_replays in sorted(missing.items()):
        name = pathlib.Path(for_replays[0]).name
        if options.dry_run:
            print(f"  ?? {want_hash}  {name[:56]}")
            continue
        found, how = beatmap_id(want_hash, for_replays)
        if found is None:
            failed.append((want_hash, name, how))
            print(f"  !! {want_hash}  {how}")
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
    print(f"\n{fetched} fetched, {len(failed)} still missing")
    return 0 if fetched or not failed else 1


if __name__ == "__main__":
    sys.exit(main())

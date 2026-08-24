"""Find code that went out with the comments.

Deleting a comment is a judgement call and nobody needs a tool for it. What
needs a tool is the line *under* the comment, which goes with it when a hand
slips, and which nothing announces until something falls over far away from the
edit.

Three of those turned up in one pass over 67 files:

    utils/osu/beatmap_link.py   `_HOST = r"..."` went with the paragraph above
                                it, and the module stopped importing
    utils/osu/api_client.py     `endpoint = f"scores/..."` went with the
                                docstring above it, and `get_score` would have
                                raised NameError on every call
    utils/osu_helpers.py        the import split across two lines

Two of those were syntax errors, which at least announce themselves the moment
anything imports the file. The third did not: the module parsed, imported, and
would have failed only when somebody asked for a score by id.

So this compares what a file *binds* — every function, class, assignment and
import, at any depth — before and after. A name that was there and is not is
either a mistake or something the author meant; the script cannot tell which
and does not try. It prints them and leaves the reading to a person.

    $ python3 scripts/deleted_code.py
    utils/osu/beatmap_link.py
        gone (name): _HOST

    $ python3 scripts/deleted_code.py --since main
    $ python3 scripts/deleted_code.py utils/            # just this subtree

## Why this is not a test

Tests assert things that should always hold. "No name ever disappears" is not
one: renaming `commit_of` to `build_of` is a name disappearing, and a test that
failed on it would be wrong rather than useful. This is a thing to run after a
batch of edits and read, which is why it prints a list rather than passing or
failing — though it does exit non-zero when it found something, so a hook can
use it if somebody wants one.

## What it does not catch

A deleted statement that binds nothing: a `return`, a call made for its effect,
a mutated argument. Those need the tests, and the tests are what found the
label ones. This covers the specific failure above — a definition disappearing
quietly — and does not pretend to more.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def bound_names(source: str) -> set[tuple[str, str]]:
    """Every name this source binds, with what kind of thing bound it.

    Arguments are deliberately left out. A renamed parameter is a local matter
    that no caller can see, and including them buried the real findings under
    every signature anybody had touched.
    """
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(("def", node.name))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            found.add(("name", node.id))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                found.add(("import", alias.asname or alias.name.split(".")[0]))
    return found


def git(*args: str) -> str | None:
    """Git's answer, or `None` if it had none to give."""
    done = subprocess.run(
        ("git", *args), cwd=REPO, capture_output=True, text=True, check=False
    )
    return done.stdout if done.returncode == 0 else None


def changed_files(since: str | None, within: list[str]) -> list[str]:
    """The Python files to look at: what differs from `since`, or what is
    uncommitted when no ref is given."""
    args = ["diff", "--name-only"]
    if since:
        args.append(since)
    listing = git(*args, "--", *within) if within else git(*args)
    if listing is None:
        return []
    return [f for f in listing.split() if f.endswith(".py")]


def report(path: str, since: str) -> int:
    """One file's losses, printed. Returns how many there were."""
    before = git("show", f"{since}:{path}")
    if before is None:
        return 0  # New file, or one that did not exist back then.
    now = (REPO / path).read_text() if (REPO / path).exists() else ""

    try:
        after_names = bound_names(now)
    except SyntaxError as broken:
        # Reported rather than skipped: a file that no longer parses is the
        # loudest version of exactly what this looks for.
        print(f"{path}\n    does not parse: line {broken.lineno}, {broken.msg}")
        return 1

    try:
        before_names = bound_names(before)
    except SyntaxError:
        return 0  # It was already broken; not this pass's doing.

    gone = sorted(before_names - after_names)
    if gone:
        print(path)
        for kind, name in gone:
            print(f"    gone ({kind}): {name}")
    return len(gone)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="names a Python file used to bind and no longer does",
        epilog="a rename shows up here too — this prints a list to read, "
        "not a verdict",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="limit to these paths (default: everything that changed)",
    )
    parser.add_argument(
        "--since",
        metavar="REF",
        help="compare against this ref instead of the uncommitted working tree",
    )
    args = parser.parse_args()

    if git("rev-parse", "--git-dir") is None:
        print("not a git repository", file=sys.stderr)
        return 2

    since = args.since or "HEAD"
    files = changed_files(args.since, args.paths)
    if not files:
        print("nothing changed" if not args.paths else "nothing changed under those paths")
        return 0

    losses = sum(report(path, since) for path in files)
    print(
        f"\n{losses} name(s) gone across {len(files)} changed file(s)"
        if losses
        else f"\nnothing lost across {len(files)} changed file(s)"
    )
    return 1 if losses else 0


if __name__ == "__main__":
    raise SystemExit(main())

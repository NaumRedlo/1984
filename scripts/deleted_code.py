from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def bound_names(source: str) -> set[tuple[str, str]]:
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
    done = subprocess.run(
        ("git", *args), cwd=REPO, capture_output=True, text=True, check=False
    )
    return done.stdout if done.returncode == 0 else None

def changed_files(since: str | None, within: list[str]) -> list[str]:
    args = ["diff", "--name-only"]
    if since:
        args.append(since)
    listing = git(*args, "--", *within) if within else git(*args)
    if listing is None:
        return []
    return [f for f in listing.split() if f.endswith(".py")]

def report(path: str, since: str) -> int:
    before = git("show", f"{since}:{path}")
    if before is None:
        return 0
    now = (REPO / path).read_text() if (REPO / path).exists() else ""

    try:
        after_names = bound_names(now)
    except SyntaxError as broken:

        print(f"{path}\n    does not parse: line {broken.lineno}, {broken.msg}")
        return 1

    try:
        before_names = bound_names(before)
    except SyntaxError:
        return 0

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

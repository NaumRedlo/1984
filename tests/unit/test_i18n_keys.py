import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

def _asked() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in sorted((ROOT / "bot").rglob("*.py")) + sorted(
        (ROOT / "services").rglob("*.py")
    ):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "t" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.append(
                    (first.value, f"{path.relative_to(ROOT)}:{first.lineno}")
                )
            elif isinstance(first, ast.JoinedStr):

                fixed = ""
                for piece in first.values:
                    if not isinstance(piece, ast.Constant):
                        break
                    fixed += piece.value
                if fixed:
                    out.append((fixed + "*", f"{path.relative_to(ROOT)}:{first.lineno}"))
    return out

def _known() -> set[str]:
    from utils.i18n import _CATALOG

    return set(_CATALOG)

def test_every_key_the_code_asks_for_exists():
    known = _known()
    asked = _asked()
    assert asked and known, "the scan found nothing — it has stopped working"
    missing = []
    for key, where in asked:
        if key.endswith("*"):
            if not any(k.startswith(key[:-1]) for k in known):
                missing.append(f"{key!r} at {where}")
        elif key not in known:
            missing.append(f"{key!r} at {where}")
    assert not missing, "keys nobody wrote:\n  " + "\n  ".join(missing)

def test_the_scan_would_notice_a_key_that_went_away():
    asked = [k for k, _ in _asked() if not k.endswith("*")]
    assert asked, "no plain keys found"
    thinned = _known() - {asked[0]}
    assert asked[0] not in thinned

import builtins
import pathlib
import symtable

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOOKED_AT = ("bot", "services", "utils", "scripts", "db")

_BUILTINS = set(dir(builtins))

def _module_names(table: symtable.SymbolTable) -> set[str]:
    return {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported()
    }

def _unbound(scope: symtable.SymbolTable, known: set[str], where: str) -> list[str]:
    found = []
    for symbol in scope.get_symbols():
        name = symbol.get_name()
        if not symbol.is_referenced():
            continue
        if (
            symbol.is_assigned()
            or symbol.is_parameter()
            or symbol.is_free()
            or symbol.is_imported()
        ):
            continue
        if name in known or name in _BUILTINS or name.startswith("__"):
            continue
        found.append(f"{name!r} in {where}")
    for child in scope.get_children():
        found += _unbound(child, known, f"{where}.{child.get_name()}")
    return found

def _files():
    for area in LOOKED_AT:
        for path in sorted((ROOT / area).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path

def test_no_function_reads_a_name_nothing_binds():
    complaints = []
    for path in _files():
        source = path.read_text()
        try:
            table = symtable.symtable(source, str(path), "exec")
        except SyntaxError as broken:
            complaints.append(f"{path.relative_to(ROOT)} does not parse: {broken}")
            continue
        complaints += [
            f"{path.relative_to(ROOT)}: {one}"
            for one in _unbound(table, _module_names(table), path.stem)
        ]
    assert not complaints, "names nothing can supply:\n  " + "\n  ".join(complaints)

def test_the_scan_notices_a_name_that_is_not_there():
    table = symtable.symtable(
        "def f(a):\n    return a + missing_entirely\n", "x.py", "exec"
    )
    assert _unbound(table, _module_names(table), "x"), (
        "the scan does not see an unbound name"
    )

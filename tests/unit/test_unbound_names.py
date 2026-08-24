"""Names a function reads and nothing ever binds.

Python resolves a name when the line runs, so a variable that exists in the
function a line was copied *from* and not in the one it was copied *to* is
found by whoever hits that branch. Twice in two days that was somebody on the
server: `video() got an unexpected keyword argument 'meter'` and then
`name 'result' is not defined`, the second at the end of every render.

`symtable` answers this without running anything: for each scope it says which
names are read, which are bound there, which come from an enclosing scope and
which are global. A name that is read and is none of those is a name nothing
can supply.
"""

import builtins
import pathlib
import symtable

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOOKED_AT = ("bot", "services", "utils", "scripts", "db")

_BUILTINS = set(dir(builtins))


def _module_names(table: symtable.SymbolTable) -> set[str]:
    """Everything the module itself binds: imports, functions, classes,
    constants, and anything assigned at the top level."""
    return {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported()
    }


def _unbound(scope: symtable.SymbolTable, known: set[str], where: str) -> list[str]:
    """Names read here that neither this scope, an enclosing one, the module
    nor the builtins can supply.

    The discriminator is not `is_global`. Python assumes module scope for any
    name a function only reads, so `is_global` is true of the fault as well as
    of every ordinary reference to a constant — which is why the first version
    of this scan found nothing anywhere, including in code that was crashing.
    """
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
    """A guard on the guard: a scan that found nothing wrong in anything would
    look exactly like a clean codebase."""
    table = symtable.symtable(
        "def f(a):\n    return a + missing_entirely\n", "x.py", "exec"
    )
    assert _unbound(table, _module_names(table), "x"), (
        "the scan does not see an unbound name"
    )

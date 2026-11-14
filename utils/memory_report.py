import ctypes
import gc
import os
import sys
from collections import Counter
from typing import Any

WIDE = 50
ROWS = 25
ROOTS = ("bot", "services", "tasks", "utils", "db")


def resident() -> float:
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1048576 if sys.platform == "darwin" else peak / 1024


def _status() -> list[str]:
    wanted = ("VmRSS", "RssAnon", "RssFile", "VmSwap", "Threads")
    found = []
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                name = line.split(":", 1)[0]
                if name in wanted:
                    found.append(" ".join(line.split()))
    except OSError:
        found.append(f"resident at most {resident():.1f} MB")
    return found


class _Mallinfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_size_t) for name in
                ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")]


def _allocator() -> list[str]:
    try:
        told = ctypes.CDLL("libc.so.6").mallinfo2
    except (OSError, AttributeError):
        return []
    told.restype = _Mallinfo
    info = told()
    mb = 1048576
    return [f"malloc: {info.arena / mb:.1f} MB from the system, {info.hblkhd / mb:.1f} MB mapped, "
            f"{info.uordblks / mb:.1f} MB in use, {info.fordblks / mb:.1f} MB free inside"]


def _picture(thing: Any) -> int:
    try:
        width, height = thing.size
        return int(width) * int(height) * len(thing.getbands())
    except Exception:
        return 0


def _objects() -> list[str]:
    counts: Counter = Counter()
    sizes: Counter = Counter()
    pictures: Counter = Counter()
    picture_bytes: Counter = Counter()
    image = sys.modules.get("PIL.Image")
    kind = getattr(image, "Image", None) if image is not None else None
    for thing in gc.get_objects():
        name = f"{type(thing).__module__}.{type(thing).__qualname__}"
        counts[name] += 1
        try:
            sizes[name] += sys.getsizeof(thing)
        except Exception:
            pass
        if kind is not None and isinstance(thing, kind):
            held = _picture(thing)
            label = f"{thing.size[0]}x{thing.size[1]} {thing.mode}"
            pictures[label] += 1
            picture_bytes[label] += held
    lines = [f"objects the collector tracks: {sum(counts.values())}"]
    lines.append(f"fonts loaded: {counts.get('PIL.ImageFont.FreeTypeFont', 0)}")
    lines.append("by size of the objects themselves:")
    for name, size in sizes.most_common(ROWS):
        lines.append(f"  {size / 1048576:8.1f} MB  {counts[name]:8d}  {name}")
    if pictures:
        lines.append(f"pictures: {sum(pictures.values())}, {sum(picture_bytes.values()) / 1048576:.1f} MB of pixels")
        for label, held in picture_bytes.most_common(ROWS):
            lines.append(f"  {held / 1048576:8.1f} MB  {pictures[label]:6d}  {label}")
    return lines


def _weight(thing: Any, depth: int = 0) -> int:
    if isinstance(thing, (bytes, bytearray, memoryview)):
        return len(thing)
    if isinstance(thing, str):
        return len(thing)
    held = _picture(thing) if hasattr(thing, "getbands") else 0
    if held:
        return held
    if depth >= 3:
        return 0
    if isinstance(thing, dict):
        return sum(_weight(inner, depth + 1) for inner in thing.values())
    if isinstance(thing, (list, tuple, set, frozenset)):
        return sum(_weight(inner, depth + 1) for inner in thing)
    getvalue = getattr(thing, "getbuffer", None)
    if callable(getvalue):
        try:
            return len(getvalue())
        except Exception:
            return 0
    return 0


def _inner(value: Any):
    if isinstance(value, (dict, list, set, frozenset)):
        return value
    for name in ("_items", "_jobs", "_seen", "_known"):
        inner = getattr(value, name, None)
        if isinstance(inner, (dict, list, set)):
            return inner
    return None


def _holders(roots: tuple[str, ...]) -> list[str]:
    found = {}
    for module_name, module in list(sys.modules.items()):
        if module is None or module_name.split(".")[0] not in roots:
            continue
        for name, value in list(vars(module).items()):
            if name.startswith("__") or isinstance(value, type):
                continue
            named = [(f"{module_name}.{name}", _inner(value))]
            if type(value).__module__.split(".")[0] in roots and hasattr(value, "__dict__"):
                named += [(f"{module_name}.{name}.{part}", inner) for part, inner in list(vars(value).items())
                          if isinstance(inner, (dict, list, set))]
            for label, inner in named:
                if inner is None or not inner or id(inner) in found:
                    continue
                weight = _weight(inner)
                if len(inner) >= WIDE or weight >= 1048576:
                    found[id(inner)] = (weight, len(inner), label)
    lines = ["what module-level holders keep (bytes, text and pixels three levels deep):"]
    for weight, held, name in sorted(found.values(), reverse=True)[:ROWS]:
        lines.append(f"  {weight / 1048576:8.1f} MB  {held:8d}  {name}")
    return lines


def report(roots: tuple[str, ...] = ROOTS) -> list[str]:
    lines = [f"memory report of pid {os.getpid()}"]
    lines += _status()
    lines += _allocator()
    lines.append("modules loaded: %d, aiogram among them: %s" % (len(sys.modules), "aiogram" in sys.modules))
    lines += _objects()
    lines += _holders(roots)
    return lines

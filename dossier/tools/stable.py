#!/usr/bin/env python3
"""Read osu!stable's own client: its assets, and the names it could not hide.

osu!stable is closed source, so every rule this engine implements has had to be
taken from reimplementations that set out to match it. The client itself is a
third source, and a better one for two questions in particular — what the game
draws when a skin supplies nothing, and which `skin.ini` keys it actually reads.

## What is readable and what is not

    osu!.exe           4 MB    a real assembly: 30,317 metadata names
    osu!gameplay.dll  32 MB    fifty names — a resource assembly, no code
    osu!ui.dll        26 MB    the same
    osu!seasonal.dll   8 MB    the same
    osu!auth.dll               no CLR header at all; anti-cheat, left alone

The three big DLLs are `ResourcesStore` shells: a couple of kilobytes of
metadata against tens of megabytes of payload. That payload is not encrypted —
it is a plain `.resources` container holding the game's pictures and sounds
under the names the game asks for them by, which is what `assets` pulls out.

`osu!.exe` is the game, and it is obfuscated: Eazfuscator has renamed most
types and methods to `#=z...` and moved the string literals out of `#US` (708
bytes, on a four-megabyte assembly) into an encrypted blob. Reading its IL
would mean reading `#=zlfIhj$7r1tyi` calling `#=zL50oaQClP8SHZOraJw==`.

What obfuscation cannot touch is anything the game looks up *by name* at
runtime, and a `skin.ini` key is exactly that. Eight and a half thousand names
survive, and `names` and `type` are for finding them — `type SkinOsu` prints
stable's whole osu!standard skin vocabulary, field by field.

## Usage

    stable.py assets <assembly> <out-dir>     write every picture and sound out
    stable.py names  <assembly> [pattern]     the names obfuscation left behind
    stable.py type   <assembly> <name>        one type's fields, by its own name

Nothing is installed for any of it: the PE section table, the CLR header, the
`.resources` container and the ECMA-335 metadata tables are all read here.

The assets are ppy's. Extract them to read, and to grade this engine's own
sizes and fallbacks against; they are not ours to redistribute, so nothing this
writes belongs in the repository.
"""

import pathlib
import struct
import sys

# ── the PE and CLR headers ───────────────────────────────────────────────────


def sections(b):
    pe = struct.unpack("<I", b[0x3C:0x40])[0]
    count = struct.unpack("<H", b[pe + 6 : pe + 8])[0]
    opt_size = struct.unpack("<H", b[pe + 20 : pe + 22])[0]
    first = pe + 24 + opt_size
    for i in range(count):
        s = first + i * 40
        va, vsz = struct.unpack("<II", b[s + 12 : s + 20])
        rawsz, raw = struct.unpack("<II", b[s + 16 : s + 24])
        yield va, vsz, rawsz, raw


def rva_to_off(b, rva):
    for va, vsz, rawsz, raw in sections(b):
        if va <= rva < va + max(vsz, rawsz):
            return raw + (rva - va)
    return None


def clr_header(b):
    """The CLR directory, or None for a native image."""
    pe = struct.unpack("<I", b[0x3C:0x40])[0]
    opt = pe + 24
    magic = struct.unpack("<H", b[opt : opt + 2])[0]
    dirs = opt + (112 if magic == 0x20B else 96)
    rva, size = struct.unpack("<II", b[dirs + 14 * 8 : dirs + 14 * 8 + 8])
    return rva_to_off(b, rva) if size else None


def heaps_of(b):
    """The metadata heaps, by name, as (offset, size)."""
    clr = clr_header(b)
    if clr is None:
        raise SystemExit("not a managed assembly")
    md = rva_to_off(b, struct.unpack("<I", b[clr + 8 : clr + 12])[0])
    if b[md : md + 4] != b"BSJB":
        raise SystemExit("no metadata root")
    vlen = struct.unpack("<I", b[md + 12 : md + 16])[0]
    # Version string, then a two-byte flags field and a two-byte stream count.
    p = md + 16 + vlen
    count = struct.unpack("<H", b[p + 2 : p + 4])[0]
    p += 4
    out = {}
    for _ in range(count):
        off, size = struct.unpack("<II", b[p : p + 8])
        p += 8
        end = b.index(b"\0", p)
        out[b[p:end].decode("latin1")] = (md + off, size)
        p = (end + 1 + 3) & ~3
    return out


# ── the .resources container ─────────────────────────────────────────────────

RESOURCE_MAGIC = 0xBEEFCACE
BYTE_ARRAY, STREAM, STRING = 0x20, 0x21, 1


def seven_bit(b, i):
    """.NET's own length prefix. Returns (value, the index after it)."""
    value = shift = 0
    while True:
        byte = b[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def resource_items(b, at):
    """The named items of the `.resources` blob starting at `at`."""
    assert struct.unpack("<I", b[at : at + 4])[0] == RESOURCE_MAGIC
    i = at + 8
    skip = struct.unpack("<I", b[i : i + 4])[0]
    i += 4 + skip
    _version, count, type_count = struct.unpack("<III", b[i : i + 12])
    i += 12
    for _ in range(type_count):
        n, i = seven_bit(b, i)
        i += n
    # Eight-aligned against the *stream's* start, not the file's: the blob sits
    # at an arbitrary offset inside the assembly, and aligning the absolute
    # position lands somewhere else entirely.
    i += -(i - at) % 8
    i += 4 * count  # name hashes, which nothing here needs
    offsets = struct.unpack(f"<{count}I", b[i : i + 4 * count])
    i += 4 * count
    data_start = at + struct.unpack("<I", b[i : i + 4])[0]
    names_at = i + 4

    for offset in offsets:
        j = names_at + offset
        n, j = seven_bit(b, j)
        name = b[j : j + n].decode("utf-16-le", "replace")
        j += n
        at_data = data_start + struct.unpack("<I", b[j : j + 4])[0]
        code, k = seven_bit(b, at_data)
        if code in (BYTE_ARRAY, STREAM):
            size = struct.unpack("<i", b[k : k + 4])[0]
            yield name, k + 4, size
        elif code == STRING:
            size, k = seven_bit(b, k)
            yield name, k, size
        else:
            # A user type — a BinaryFormatter stream. `assets` digs the picture
            # out of it by signature; there is no length to trust here.
            yield name, k, None


def blobs(b):
    """Every `.resources` container in the assembly's resource area."""
    clr = clr_header(b)
    rva, size = struct.unpack("<II", b[clr + 24 : clr + 32])
    at, end = rva_to_off(b, rva), rva_to_off(b, rva) + size
    while at < end:
        length = struct.unpack("<I", b[at : at + 4])[0]
        blob = at + 4
        if b[blob : blob + 4] == struct.pack("<I", RESOURCE_MAGIC):
            yield blob, length
        at = (blob + length + 3) & ~3


# ── the ECMA-335 metadata tables ─────────────────────────────────────────────
#
# Only as much of the schema as it takes to walk from a type to its fields:
# every table's row size has to be right, because the tables are laid end to
# end and a wrong size in an early one puts every later one at the wrong offset.

TYPEDEF_OR_REF = (2, 1, 27)
HAS_CONSTANT = (4, 8, 23)
HAS_CUSTOM_ATTRIBUTE = (6, 4, 1, 2, 8, 9, 10, 0, 14, 17, 20, 23, 26, 27, 28, 32, 35, 38, 39, 40)
HAS_FIELD_MARSHAL = (4, 8)
HAS_DECL_SECURITY = (2, 6, 32)
MEMBER_REF_PARENT = (2, 1, 26, 6, 10)
HAS_SEMANTICS = (20, 17)
METHOD_DEF_OR_REF = (6, 10)
IMPLEMENTATION = (38, 39, 35)
CUSTOM_ATTRIBUTE_TYPE = (None, None, 6, 10, None)
RESOLUTION_SCOPE = (0, 26, 35, 1)
TYPE_OR_METHOD_DEF = (2, 6)

SCHEMA = {
    0x00: ["u2", "s", "g", "g", "g"],
    0x01: [RESOLUTION_SCOPE, "s", "s"],
    0x02: ["u4", "s", "s", TYPEDEF_OR_REF, ("~", 4), ("~", 6)],
    0x03: [("~", 4)],
    0x04: ["u2", "s", "b"],
    0x05: [("~", 6)],
    0x06: ["u4", "u2", "u2", "s", "b", ("~", 8)],
    0x07: [("~", 8)],
    0x08: ["u2", "u2", "s"],
    0x09: [("~", 2), TYPEDEF_OR_REF],
    0x0A: [MEMBER_REF_PARENT, "s", "b"],
    0x0B: ["u1", "u1", HAS_CONSTANT, "b"],
    0x0C: [HAS_CUSTOM_ATTRIBUTE, CUSTOM_ATTRIBUTE_TYPE, "b"],
    0x0D: [HAS_FIELD_MARSHAL, "b"],
    0x0E: ["u2", HAS_DECL_SECURITY, "b"],
    0x0F: ["u2", "u4", ("~", 2)],
    0x10: ["u4", ("~", 4)],
    0x11: [("~", 2), "b"],
    0x12: [("~", 6), METHOD_DEF_OR_REF, METHOD_DEF_OR_REF],
    0x14: ["u2", "s", "s"],
    0x15: ["b"],
    0x16: [TYPEDEF_OR_REF],
    0x17: ["b"],
    0x18: [HAS_SEMANTICS, METHOD_DEF_OR_REF, METHOD_DEF_OR_REF],
    0x19: [("~", 2), METHOD_DEF_OR_REF, METHOD_DEF_OR_REF],
    0x1A: ["s"],
    0x1B: ["b"],
    0x1C: [("~", 2), ("~", 38)],
    0x1D: ["u4", "s", IMPLEMENTATION],
    0x20: ["u4", "u2", "u2", "u2", "u4", "b", "s", "s", "g"],
    0x21: ["b"],
    0x22: ["u2", "u2", "u2", "u2", "u4", "b", "s", "s", "b"],
    0x23: ["u2", "u2", "u2", "u2", "u4", "b", "s", "s", "b"],
    0x24: ["b"],
    0x25: [IMPLEMENTATION, "s", "s"],
    0x26: ["u4", "s", IMPLEMENTATION],
    0x27: ["u2", "u2", "u4", "s", "s", IMPLEMENTATION],
    0x28: ["u4", "u4", "s", IMPLEMENTATION],
    0x29: [("~", 2), ("~", 2)],
    0x2A: ["u2", "u2", TYPE_OR_METHOD_DEF, "s"],
    0x2B: [("~", 42), TYPEDEF_OR_REF],
    0x2C: ["u4", ("~", 2), ("~", 6)],
}

TYPE_DEF, FIELD = 0x02, 0x04


class Tables:
    def __init__(self, b, heaps):
        self.b, self.heaps = b, heaps
        at = heaps["#~"][0]
        sizes = b[at + 6]
        self.heap_width = {
            "s": 4 if sizes & 1 else 2,
            "g": 4 if sizes & 2 else 2,
            "b": 4 if sizes & 4 else 2,
        }
        valid = struct.unpack("<Q", b[at + 8 : at + 16])[0]
        present = [i for i in range(64) if valid >> i & 1]
        p = at + 24
        self.rows = {}
        for t in present:
            self.rows[t] = struct.unpack("<I", b[p : p + 4])[0]
            p += 4
        self.starts = {}
        for t in present:
            self.starts[t] = p
            p += self.rows[t] * sum(self._width(c) for c in SCHEMA[t])

    def _width(self, col):
        if isinstance(col, str):
            return {"u1": 1, "u2": 2, "u4": 4}.get(col) or self.heap_width[col]
        if col and col[0] == "~":
            return 2 if self.rows.get(col[1], 0) < (1 << 16) else 4
        bits = max(1, (len(col) - 1).bit_length())
        largest = max((self.rows.get(t, 0) for t in col if t is not None), default=0)
        return 2 if largest < (1 << (16 - bits)) else 4

    def read(self, t):
        if t not in self.starts:
            return []
        widths = [self._width(c) for c in SCHEMA[t]]
        stride = sum(widths)
        out = []
        for r in range(self.rows[t]):
            at = self.starts[t] + r * stride
            row = []
            for w in widths:
                row.append(int.from_bytes(self.b[at : at + w], "little"))
                at += w
            out.append(tuple(row))
        return out

    def string(self, index):
        at = self.heaps["#Strings"][0] + index
        return self.b[at : self.b.index(b"\0", at)].decode("utf-8", "replace")


# ── the three commands ───────────────────────────────────────────────────────


def png_from(b, start, limit):
    """The whole PNG at or after `start`, walked to its own `IEND`."""
    sig = b.find(b"\x89PNG\r\n\x1a\n", start, limit)
    if sig < 0:
        return None
    i = sig + 8
    while i + 8 <= len(b):
        length = struct.unpack(">I", b[i : i + 4])[0]
        kind = b[i + 4 : i + 8]
        i += 12 + length
        if kind == b"IEND":
            return b[sig:i]
    return None


def extension(blob):
    for magic, ext in [
        (b"\x89PNG\r\n\x1a\n", ".png"),
        (b"RIFF", ".wav"),
        (b"OggS", ".ogg"),
        (b"ID3", ".mp3"),
        (b"\xff\xfb", ".mp3"),
    ]:
        if blob.startswith(magic):
            return ext
    return ".bin"


def assets(path, out):
    b = pathlib.Path(path).read_bytes()
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written, missed = 0, []
    for blob, length in blobs(b):
        items = list(resource_items(b, blob))
        # Each entry runs until the next one begins, so a picture is looked for
        # inside its own span and cannot borrow its neighbour's.
        starts = sorted(off for _, off, _ in items)
        bounds = dict(zip(starts, starts[1:] + [blob + length]))
        for name, off, size in items:
            data = b[off : off + size] if size else png_from(b, off, bounds[off])
            if not data:
                missed.append(name)
                continue
            (out / f"{name}{extension(data)}").write_bytes(data)
            written += 1
    print(f"{pathlib.Path(path).name}: {written} files -> {out}")
    if missed:
        print(f"  {len(missed)} not recognised, e.g. {missed[:6]}")


def names(path, pattern=None):
    b = pathlib.Path(path).read_bytes()
    at, size = heaps_of(b)["#Strings"]
    every = [s.decode("utf-8", "replace") for s in b[at : at + size].split(b"\0") if s]
    plain = [n for n in every if not n.startswith("#=z")]
    print(f"{len(every)} names, {len(plain)} of them not renamed")
    if pattern:
        hits = sorted({n for n in plain if pattern.lower() in n.lower()})
        print(f"{len(hits)} matching {pattern!r}:")
        for n in hits:
            print(f"  {n}")


def show_type(path, wanted):
    b = pathlib.Path(path).read_bytes()
    t = Tables(b, heaps_of(b))
    types, fields = t.read(TYPE_DEF), t.read(FIELD)
    for i, td in enumerate(types):
        if t.string(td[1]) != wanted:
            continue
        start = td[4] - 1
        end = (types[i + 1][4] - 1) if i + 1 < len(types) else len(fields)
        full = f"{t.string(td[2])}.{t.string(td[1])}".lstrip(".")
        print(f"{full}   flags {td[0]:#x}   {end - start} fields")
        for k in range(start, end):
            name = t.string(fields[k][1])
            print(f"  {'(renamed)' if name.startswith('#=z') else name}")
        return
    print(f"no type named {wanted!r}")


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__.split("## Usage")[1].strip())
    command, path = sys.argv[1], sys.argv[2]
    rest = sys.argv[3:]
    if command == "assets":
        assets(path, rest[0])
    elif command == "names":
        names(path, rest[0] if rest else None)
    elif command == "type":
        show_type(path, rest[0])
    else:
        raise SystemExit(f"no command {command!r}")


if __name__ == "__main__":
    main()

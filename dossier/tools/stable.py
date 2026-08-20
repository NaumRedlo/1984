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
    stable.py type   <assembly> <name>        one type's fields, properties, methods
    stable.py il     <assembly> <type> [n]    what those methods call, in order

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
MEMBER_FORWARDED = (4, 6)

SCHEMA = {
    0x00: ["u2", "s", "g", "g", "g"],                                    # Module
    0x01: [RESOLUTION_SCOPE, "s", "s"],                                  # TypeRef
    0x02: ["u4", "s", "s", TYPEDEF_OR_REF, ("~", 4), ("~", 6)],          # TypeDef
    0x03: [("~", 4)],                                                    # FieldPtr
    0x04: ["u2", "s", "b"],                                              # Field
    0x05: [("~", 6)],                                                    # MethodPtr
    0x06: ["u4", "u2", "u2", "s", "b", ("~", 8)],                        # MethodDef
    0x07: [("~", 8)],                                                    # ParamPtr
    0x08: ["u2", "u2", "s"],                                             # Param
    0x09: [("~", 2), TYPEDEF_OR_REF],                                    # InterfaceImpl
    0x0A: [MEMBER_REF_PARENT, "s", "b"],                                 # MemberRef
    0x0B: ["u1", "u1", HAS_CONSTANT, "b"],                               # Constant
    0x0C: [HAS_CUSTOM_ATTRIBUTE, CUSTOM_ATTRIBUTE_TYPE, "b"],            # CustomAttribute
    0x0D: [HAS_FIELD_MARSHAL, "b"],                                      # FieldMarshal
    0x0E: ["u2", HAS_DECL_SECURITY, "b"],                                # DeclSecurity
    0x0F: ["u2", "u4", ("~", 2)],                                        # ClassLayout
    0x10: ["u4", ("~", 4)],                                              # FieldLayout
    0x11: ["b"],                                                         # StandAloneSig
    0x12: [("~", 2), ("~", 20)],                                         # EventMap
    0x13: [("~", 20)],                                                   # EventPtr
    0x14: ["u2", "s", TYPEDEF_OR_REF],                                   # Event
    0x15: [("~", 2), ("~", 23)],                                         # PropertyMap
    0x16: [("~", 23)],                                                   # PropertyPtr
    0x17: ["u2", "s", "b"],                                              # Property
    0x18: ["u2", ("~", 6), HAS_SEMANTICS],                               # MethodSemantics
    0x19: [("~", 2), METHOD_DEF_OR_REF, METHOD_DEF_OR_REF],              # MethodImpl
    0x1A: ["s"],                                                         # ModuleRef
    0x1B: ["b"],                                                         # TypeSpec
    0x1C: ["u2", MEMBER_FORWARDED, "s", ("~", 26)],                      # ImplMap
    0x1D: ["u4", ("~", 4)],                                              # FieldRVA
    0x20: ["u4", "u2", "u2", "u2", "u2", "u4", "b", "s", "s"],           # Assembly
    0x21: ["u4"],                                                        # AssemblyProcessor
    0x22: ["u4", "u4", "u4"],                                            # AssemblyOS
    0x23: ["u2", "u2", "u2", "u2", "u4", "b", "s", "s", "b"],            # AssemblyRef
    0x24: ["u4", ("~", 35)],                                             # AssemblyRefProcessor
    0x25: ["u4", "u4", "u4", ("~", 35)],                                 # AssemblyRefOS
    0x26: ["u4", "s", "b"],                                              # File
    0x27: ["u4", "u4", "s", "s", IMPLEMENTATION],                        # ExportedType
    0x28: ["u4", "u4", "s", IMPLEMENTATION],                             # ManifestResource
    0x29: [("~", 2), ("~", 2)],                                          # NestedClass
    0x2A: ["u2", "u2", TYPE_OR_METHOD_DEF, "s"],                         # GenericParam
    0x2B: [METHOD_DEF_OR_REF, "b"],                                      # MethodSpec
    0x2C: [("~", 42), TYPEDEF_OR_REF],                                   # GenericParamConstraint
}

TYPE_DEF, FIELD, METHOD_DEF = 0x02, 0x04, 0x06
PROPERTY_MAP, PROPERTY = 0x15, 0x17
MANIFEST_RESOURCE = 0x28


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

    def owner_of(self, table, index, map_table, list_column):
        """Which row of `map_table` owns row `index` of `table`.

        The tables carry ranges rather than back-pointers: a type names the
        first of its fields and the next type's first field ends the run.
        """
        rows = self.read(map_table)
        for r, row in enumerate(rows):
            start = row[list_column] - 1
            end = rows[r + 1][list_column] - 1 if r + 1 < len(rows) else None
            if index >= start and (end is None or index < end):
                return r
        return None

    def token(self, value):
        """A metadata token as a name, for reading calls out of IL.

        `Type::Method` where both are known. A call into mscorlib keeps its
        real name however hard the assembly itself has been obfuscated, which
        is what makes an obfuscated method readable at all: the shape of what
        it calls survives even when nothing it is called is legible.
        """
        table, row = value >> 24, (value & 0xFFFFFF) - 1
        if table == 0x0A:  # MemberRef — usually a call out of the assembly
            rows = self.read(0x0A)
            if not 0 <= row < len(rows):
                return f"token {value:#x}"
            parent, name, _sig = rows[row]
            kind, index = parent & 7, (parent >> 3) - 1
            owner = "?"
            if kind == 1 and 0 <= index < len(self.read(0x01)):  # TypeRef
                tr = self.read(0x01)[index]
                owner = f"{self.string(tr[2])}.{self.string(tr[1])}".lstrip(".")
            elif kind == 0 and 0 <= index < len(self.read(0x02)):  # TypeDef
                td = self.read(0x02)[index]
                owner = f"{self.string(td[2])}.{self.string(td[1])}".lstrip(".")
            return f"{owner}::{self.string(name)}"
        if table == 0x06:  # MethodDef — a call inside this assembly
            rows = self.read(0x06)
            if not 0 <= row < len(rows):
                return f"token {value:#x}"
            at = self.owner_of(0x06, row, 0x02, 5)
            owner = "?"
            if at is not None:
                td = self.read(0x02)[at]
                owner = f"{self.string(td[2])}.{self.string(td[1])}".lstrip(".")
            return f"{owner}::{self.string(rows[row][3])}"
        if table == 0x2B:  # MethodSpec — a generic method at one instantiation
            rows = self.read(0x2B)
            if 0 <= row < len(rows):
                method, _inst = rows[row]
                kind, index = method & 1, (method >> 1)
                return "<" + self.token(((0x06 if kind == 0 else 0x0A) << 24) | index) + ">"
        if table == 0x01:
            rows = self.read(0x01)
            if 0 <= row < len(rows):
                return f"{self.string(rows[row][2])}.{self.string(rows[row][1])}".lstrip(".")
        if table == 0x04:  # Field
            rows = self.read(0x04)
            if 0 <= row < len(rows):
                return f"field {self.string(rows[row][1])}"
        if table == 0x70:  # a string literal, from #US
            return f"ldstr {self.user_string(row + 1)!r}"
        return f"token {value:#x}"

    def user_string(self, offset):
        at = self.heaps["#US"][0] + offset
        length, at = seven_bit(self.b, at)
        if length <= 1:
            return ""
        return self.b[at : at + length - 1].decode("utf-16-le", "replace")

    def string(self, index):
        at = self.heaps["#Strings"][0] + index
        return self.b[at : self.b.index(b"\0", at)].decode("utf-8", "replace")


# ── reading IL ───────────────────────────────────────────────────────────────
#
# Not a disassembler. The only thing wanted here is what a method *calls*, in
# order, which is enough to read an obfuscated method: `#=zlfIhj$7r1tyi` is
# opaque, and "opens a directory, lowercases a name, looks it up in a
# dictionary, falls back" is not.

# Operand widths, straight off ECMA-335's opcode table. Every one has to be
# right: the stream is read one instruction at a time, so a single wrong width
# desynchronises everything after it and the calls come out as noise.
WIDTH = [0] * 256
for _op in (0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x1F, 0xDE):
    WIDTH[_op] = 1
for _op in range(0x2B, 0x38):  # br.s through blt.un.s
    WIDTH[_op] = 1
for _op in range(0x38, 0x45):  # br through blt.un
    WIDTH[_op] = 4
for _op in (0x20, 0x22, 0x27, 0x28, 0x29, 0x6F, 0x70, 0x71, 0x72, 0x73, 0x74,
            0x75, 0x79, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F, 0x80, 0x81, 0x8C, 0x8D,
            0x8F, 0xA3, 0xA4, 0xA5, 0xC2, 0xC6, 0xD0, 0xDD):
    WIDTH[_op] = 4
for _op in (0x21, 0x23):
    WIDTH[_op] = 8
SWITCH = 0x45
PREFIX = 0xFE
# The `fe xx` family: a token, a two-byte local index, a one-byte hint, or
# nothing at all.
WIDE_WIDTH = {0x06: 4, 0x07: 4, 0x15: 4, 0x16: 4, 0x1C: 4,
              0x09: 2, 0x0A: 2, 0x0B: 2, 0x0C: 2, 0x0D: 2, 0x0E: 2,
              0x12: 1, 0x19: 1}

CALL_OPCODES = {0x28: "call", 0x6F: "callvirt", 0x73: "newobj", 0x27: "jmp"}
# A typed getter reads `(key, default)`, so the default is an `ldc` sitting
# immediately before the call. Those constants are the point of reading these
# methods at all: the key names are encrypted and the defaults are not.
LOAD_CONST = {0x20: "i4", 0x1F: "i4.s", 0x21: "i8", 0x22: "r4", 0x23: "r8"}
SMALL_INT = {op: op - 0x16 for op in range(0x16, 0x1F)}  # ldc.i4.0 … ldc.i4.8
SMALL_INT[0x15] = -1  # ldc.i4.m1
FIELD_OPCODES = {0x7B: "ldfld", 0x7D: "stfld", 0x7E: "ldsfld", 0x80: "stsfld"}
LDSTR = 0x72


def method_body(b, rva):
    """The IL of one method, or None for an abstract or native one."""
    if rva == 0:
        return None
    at = rva_to_off(b, rva)
    if at is None:
        return None
    first = b[at]
    if first & 3 == 2:  # tiny header: the size is in the top six bits
        return b[at + 1 : at + 1 + (first >> 2)]
    size = struct.unpack("<I", b[at + 4 : at + 8])[0]
    header = (struct.unpack("<H", b[at : at + 2])[0] >> 12) * 4
    return b[at + header : at + header + size]


def calls_in(tables, il):
    """Every call, field access and string literal, in the order they run."""
    out = []
    i = 0
    while i < len(il):
        op = il[i]
        i += 1
        if op == PREFIX:
            second = il[i]
            i += 1 + WIDE_WIDTH.get(il[i], 0)
            continue
        if op == SWITCH:
            count = struct.unpack("<I", il[i : i + 4])[0]
            i += 4 + 4 * count
            continue
        if op in CALL_OPCODES or op in FIELD_OPCODES or op == LDSTR:
            token = struct.unpack("<I", il[i : i + 4])[0]
            i += 4
            kind = CALL_OPCODES.get(op) or FIELD_OPCODES.get(op) or "ldstr"
            out.append((kind, tables.token(token)))
            continue
        if op in SMALL_INT:
            out.append(("ldc", str(SMALL_INT[op])))
            continue
        if op in LOAD_CONST:
            width = WIDTH[op]
            raw = il[i : i + width]
            i += width
            if op == 0x1F:
                value = str(struct.unpack("<b", raw)[0])
            elif op == 0x20:
                value = str(struct.unpack("<i", raw)[0])
            elif op == 0x21:
                value = str(struct.unpack("<q", raw)[0])
            elif op == 0x22:
                value = f"{struct.unpack('<f', raw)[0]:g}"
            else:
                value = f"{struct.unpack('<d', raw)[0]:g}"
            out.append(("ldc", value))
            continue
        if op == 0x14:
            out.append(("ldc", "null"))
            continue
        i += WIDTH[op]
    return out


# ── the commands ─────────────────────────────────────────────────────────────


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


def members_of(t, index):
    """One type's fields, properties and methods, by row range."""
    types = t.read(TYPE_DEF)
    fields, methods = t.read(FIELD), t.read(METHOD_DEF)

    def run(column, table):
        start = types[index][column] - 1
        end = types[index + 1][column] - 1 if index + 1 < len(types) else len(table)
        return range(start, end)

    props = []
    maps, all_props = t.read(PROPERTY_MAP), t.read(PROPERTY)
    for r, row in enumerate(maps):
        if row[0] - 1 != index:
            continue
        start = row[1] - 1
        end = maps[r + 1][1] - 1 if r + 1 < len(maps) else len(all_props)
        props = list(range(start, end))
    return run(4, fields), props, run(5, methods)


def find_type(t, wanted):
    for i, td in enumerate(t.read(TYPE_DEF)):
        if t.string(td[1]) == wanted:
            return i
    return None


def readable(name):
    return "(renamed)" if name.startswith("#=z") else name


def show_type(path, wanted):
    b = pathlib.Path(path).read_bytes()
    t = Tables(b, heaps_of(b))
    index = find_type(t, wanted)
    if index is None:
        print(f"no type named {wanted!r}")
        return
    td = t.read(TYPE_DEF)[index]
    full = f"{t.string(td[2])}.{t.string(td[1])}".lstrip(".")
    fields, props, methods = members_of(t, index)
    print(f"{full}   flags {td[0]:#x}")

    all_fields, all_props, all_methods = t.read(FIELD), t.read(PROPERTY), t.read(METHOD_DEF)
    print(f"  {len(fields)} fields")
    for k in fields:
        print(f"    {readable(t.string(all_fields[k][1]))}")
    if props:
        print(f"  {len(props)} properties")
        for k in props:
            print(f"    {readable(t.string(all_props[k][1]))}")
    print(f"  {len(methods)} methods")
    for k in methods:
        rva = all_methods[k][0]
        il = method_body(b, rva)
        size = f"{len(il)} bytes of IL" if il else "no body"
        print(f"    [{k}] {readable(t.string(all_methods[k][3])):26} {size}")


def show_il(path, wanted, which=None):
    """What a type's methods call, in order.

    The method names are gone; what they call is not. A method that opens a
    directory, lowercases a name and looks it up in a dictionary says what it
    is for whatever it has been renamed to.
    """
    b = pathlib.Path(path).read_bytes()
    t = Tables(b, heaps_of(b))
    index = find_type(t, wanted)
    if index is None:
        print(f"no type named {wanted!r}")
        return
    all_methods = t.read(METHOD_DEF)
    _, _, methods = members_of(t, index)
    for k in methods:
        if which is not None and str(k) != which and t.string(all_methods[k][3]) != which:
            continue
        il = method_body(b, all_methods[k][0])
        if not il:
            continue
        print(f"\n── [{k}] {readable(t.string(all_methods[k][3]))}  ({len(il)} bytes)")
        for kind, name in calls_in(t, il):
            print(f"     {kind:9} {name}")


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
    elif command == "il":
        show_il(path, rest[0], rest[1] if len(rest) > 1 else None)
    else:
        raise SystemExit(f"no command {command!r}")


if __name__ == "__main__":
    main()

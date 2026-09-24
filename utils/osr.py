import struct
from dataclasses import dataclass
from typing import Optional

@dataclass
class Header:
    mode: int
    version: int
    beatmap_md5: str
    player: str
    replay_md5: str
    count300: int
    count100: int
    count50: int
    geki: int
    katu: int
    misses: int
    score: int
    combo: int
    perfect: bool
    mods: int

class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.at = 0

    def take(self, size: int) -> bytes:
        if self.at + size > len(self.data):
            raise ValueError("the replay ends too early")
        piece = self.data[self.at:self.at + size]
        self.at += size
        return piece

    def number(self, form: str) -> int:
        size = struct.calcsize(form)
        return struct.unpack(form, self.take(size))[0]

    def uleb(self) -> int:
        value, shift = 0, 0
        while True:
            byte = self.take(1)[0]
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 35:
                raise ValueError("a length that does not end")

    def words(self) -> str:
        mark = self.take(1)[0]
        if mark == 0x00:
            return ""
        if mark != 0x0B:
            raise ValueError("a string without its mark")
        return self.take(self.uleb()).decode("utf-8", errors="replace")

def header(data: bytes) -> Optional[Header]:
    try:
        read = _Reader(data)
        mode = read.number("<B")
        version = read.number("<i")
        beatmap_md5 = read.words()
        player = read.words()
        replay_md5 = read.words()
        counts = [read.number("<H") for _ in range(6)]
        score = read.number("<i")
        combo = read.number("<H")
        perfect = bool(read.number("<B"))
        mods = read.number("<i")
    except (ValueError, struct.error):
        return None
    if len(beatmap_md5) != 32 or not all(c in "0123456789abcdefABCDEF" for c in beatmap_md5):
        return None
    return Header(mode, version, beatmap_md5.lower(), player, replay_md5, counts[0], counts[1], counts[2],
                  counts[3], counts[4], counts[5], score, combo, perfect, mods)

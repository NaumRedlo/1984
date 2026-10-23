import re
from dataclasses import dataclass, field
from typing import Any, Optional

BANCHO_BOT = "BanchoBot"

@dataclass(frozen=True)
class Line:
    prefix: str
    command: str
    params: tuple[str, ...]
    text: Optional[str]

    @property
    def nick(self) -> str:
        return self.prefix.split("!", 1)[0]

def parse_line(raw: str) -> Optional[Line]:
    line = raw.rstrip("\r\n")
    if not line:
        return None
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    text = None
    if " :" in line:
        line, _, text = line.partition(" :")
    elif line.startswith(":"):
        text, line = line[1:], ""
    parts = line.split()
    if not parts:
        return None
    return Line(prefix=prefix, command=parts[0].upper(), params=tuple(parts[1:]), text=text)

@dataclass
class Said:
    kind: str
    channel: str
    data: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

_MATCH = re.compile(r"^Created the tournament match https://osu\.ppy\.sh/mp/(\d+) (.+)$")
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("joined", re.compile(r"^(?P<user>.+) joined in slot (?P<slot>\d+)(?: for team (?P<team>red|blue))?\.$")),
    ("moved", re.compile(r"^(?P<user>.+) moved to slot (?P<slot>\d+)$")),
    ("left", re.compile(r"^(?P<user>.+) left the game\.$")),
    ("host", re.compile(r"^(?P<user>.+) became the host\.$")),
    ("map_changed", re.compile(r"^Beatmap changed to: (?P<title>.+) \(https://osu\.ppy\.sh/b/(?P<beatmap>\d+)\)$")),
    ("map_set", re.compile(r"^Changed beatmap to https://osu\.ppy\.sh/b/(?P<beatmap>\d+) (?P<title>.+)$")),
    ("started", re.compile(r"^The match has started!$")),
    ("finished_all", re.compile(r"^The match has finished!$")),
    ("finished", re.compile(r"^(?P<user>.+) finished playing \(Score: (?P<score>\d+), (?P<state>PASSED|FAILED)\)\.$")),
    ("all_ready", re.compile(r"^All players are ready$")),
    ("aborted", re.compile(r"^Aborted the match$")),
    ("closed", re.compile(r"^Closed the match$")),
    ("countdown", re.compile(r"^Match starts in (?P<seconds>\d+) seconds?$")),
    ("countdown_done", re.compile(r"^Countdown finished$")),
    ("room", re.compile(r"^Room name: (?P<name>.+), History: https://osu\.ppy\.sh/mp/(?P<match>\d+)$")),
    ("settings_map", re.compile(r"^Beatmap: https://osu\.ppy\.sh/b/(?P<beatmap>\d+) (?P<title>.+)$")),
    ("settings_mode", re.compile(r"^Team mode: (?P<team>\w+), Win condition: (?P<win>\w+)$")),
    ("settings_mods", re.compile(r"^Active mods: (?P<mods>.+)$")),
    ("settings_players", re.compile(r"^Players: (?P<count>\d+)$")),
    ("settings_slot", re.compile(
        r"^Slot (?P<slot>\d+)\s+(?P<state>Not Ready|Ready|No Map)\s+https://osu\.ppy\.sh/u/(?P<user_id>\d+) (?P<user>.+?)\s*(?:\[(?P<tags>[^\]]*)\])?$")),
    ("mods_set", re.compile(r"^(?:Enabled (?P<mods>.+?)(?:, disabled FreeMod)?|Disabled all mods, enabled FreeMod|Disabled all mods, disabled FreeMod|Disabled all mods)$")),
    ("settings_set", re.compile(r"^Changed match settings to (?P<size>\d+) slots, (?P<team>\w+), (?P<score>\w+)$")),
    ("size_set", re.compile(r"^Changed match to size (?P<size>\d+)$")),
    ("password_set", re.compile(r"^Changed the match password$")),
    ("password_removed", re.compile(r"^Removed the match password$")),
    ("invited", re.compile(r"^Invited (?P<user>.+) to the room$")),
    ("locked", re.compile(r"^Locked the match$")),
    ("unlocked", re.compile(r"^Unlocked the match$")),
    ("referee_added", re.compile(r"^Added (?P<user>.+) to the match referees$")),
    ("refused", re.compile(r"^You cannot create any more tournament matches.*$")),
)

def said(line: Line) -> Optional[Said]:
    if line.command != "PRIVMSG" or line.nick != BANCHO_BOT or not line.params or line.text is None:
        return None
    channel = line.params[0]
    text = line.text.strip()
    created = _MATCH.match(text)
    if created:
        return Said("created", channel, {"match": int(created.group(1)), "name": created.group(2)}, text)
    for kind, pattern in _PATTERNS:
        found = pattern.match(text)
        if found:
            data = {key: value for key, value in found.groupdict().items() if value is not None}
            for key in ("slot", "beatmap", "score", "match", "count", "user_id", "seconds", "size"):
                if key in data and str(data[key]).isdigit():
                    data[key] = int(data[key])
            if kind == "settings_slot" and "tags" in data:
                data["tags"] = [tag.strip() for tag in data["tags"].split("/") if tag.strip()]
            return Said(kind, channel, data, text)
    return Said("unknown", channel, {}, text)

def match_channel(match_id: int) -> str:
    return f"#mp_{match_id}"

def mods_line(mods: list[str], free: bool = False) -> str:
    named = [name for name in mods if name]
    if free:
        named.append("Freemod")
    return "!mp mods " + (" ".join(named) if named else "None")

SETUP_TEAM_HEAD_TO_HEAD = 0
SETUP_SCORE_V2 = 3

def irc_name(name: str) -> str:
    return name.replace(" ", "_").lower()

def setup_lines(password: str, players: list[str], size: int = 2, referee: str = "") -> list[str]:
    lines = [f"!mp set {SETUP_TEAM_HEAD_TO_HEAD} {SETUP_SCORE_V2} {size}", f"!mp password {password}", "!mp lock"]
    lines.extend(f"!mp invite {name.replace(' ', '_')}" for name in players if irc_name(name) != irc_name(referee))
    return lines

@dataclass
class Settings:
    name: Optional[str] = None
    match: Optional[int] = None
    beatmap: Optional[int] = None
    team: Optional[str] = None
    win: Optional[str] = None
    mods: list[str] = field(default_factory=list)
    players: Optional[int] = None
    slots: list[dict[str, Any]] = field(default_factory=list)

    def take(self, event: Said) -> bool:
        if event.kind == "room":
            self.name, self.match = event.data["name"], event.data["match"]
        elif event.kind == "settings_map":
            self.beatmap = event.data["beatmap"]
        elif event.kind == "settings_mode":
            self.team, self.win = event.data["team"], event.data["win"]
        elif event.kind == "settings_mods":
            self.mods = [name.strip() for name in event.data["mods"].split(",") if name.strip()]
        elif event.kind == "settings_players":
            self.players = event.data["count"]
        elif event.kind == "settings_slot":
            self.slots.append(event.data)
        else:
            return False
        return True

    @property
    def complete(self) -> bool:
        return self.players is not None and len(self.slots) >= self.players

def problems(settings: Settings, beatmap: int, players: set[int], mods: set[str], free: bool) -> list[str]:
    found = []
    if settings.team != "HeadToHead":
        found.append("team_mode")
    if settings.win != "ScoreV2":
        found.append("score_mode")
    if settings.beatmap != beatmap:
        found.append("beatmap")
    active = {name for name in settings.mods if name not in ("Freemod", "None")}
    if {_MOD_NAMES.get(name, name) for name in active} != mods:
        found.append("mods")
    if ("Freemod" in settings.mods) != free:
        found.append("freemod")
    seated = {slot["user_id"] for slot in settings.slots}
    if seated - players:
        found.append("stranger")
    if players - seated:
        found.append("missing")
    return found

_MOD_NAMES = {"NoFail": "NF", "Hidden": "HD", "HardRock": "HR", "DoubleTime": "DT", "Nightcore": "NC", "Easy": "EZ",
              "HalfTime": "HT", "Flashlight": "FL", "Relax": "RX", "Relax2": "AP", "SpunOut": "SO", "SuddenDeath": "SD",
              "Perfect": "PF", "ScoreV2": "V2"}

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from services.duels.generator import SLOT_MODS

LANGS = ("ru", "en")
FOLDER = Path(__file__).resolve().parents[2] / "assets" / "story"
KINDS = ("scene", "task", "choice", "end")
OUTCOMES = ("passed", "fc", "ss")
NAME = re.compile(r"^[a-z][a-z0-9_]*$")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
PLACES = frozenset({"player"})
OPTIONS_LEAST = 2
OPTIONS_MOST = 3
ALLOWED_MODS = frozenset({"NF", "EZ", "HD", "HR", "DT", "NC", "HT", "FL", "SD", "PF"})


class ChapterError(ValueError):
    pass


@dataclass(frozen=True)
class Words:
    ru: str
    en: str

    def said(self, lang: str) -> str:
        return self.ru if lang == "ru" else self.en


@dataclass(frozen=True)
class Line:
    who: str
    words: Words
    when: tuple[str, ...] = ()


@dataclass(frozen=True)
class Goal:
    accuracy: Optional[float] = None
    full_combo: bool = False
    mods: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Task:
    slot: str
    below: float
    goal: Goal
    then: str


@dataclass(frozen=True)
class Option:
    id: str
    words: Words
    flag: str
    next: str


@dataclass(frozen=True)
class Step:
    id: str
    kind: str
    lines: tuple[Line, ...] = ()
    next: Optional[str] = None
    task: Optional[Task] = None
    options: tuple[Option, ...] = ()
    next_chapter: Optional[str] = None


@dataclass(frozen=True)
class Chapter:
    id: str
    title: Words
    start: str
    speakers: dict[str, Words]
    steps: dict[str, Step]

    def flag(self, name: str) -> str:
        return name if ":" in name else f"{self.id}:{name}"

    def flags(self) -> set[str]:
        found = set()
        for step in self.steps.values():
            for option in step.options:
                found.add(self.flag(option.flag))
            if step.task is not None:
                found.update(self.flag(f"{step.id}.{outcome}") for outcome in OUTCOMES)
        return found


def _text(raw: Any, where: str) -> Words:
    if not isinstance(raw, dict):
        raise ChapterError(f"{where}: words must name {' and '.join(LANGS)}")
    said = {}
    for lang in LANGS:
        value = raw.get(lang)
        if not isinstance(value, str) or not value.strip():
            raise ChapterError(f"{where}: no {lang} text")
        unknown = sorted(set(PLACEHOLDER.findall(value)) - PLACES)
        if unknown:
            raise ChapterError(f"{where}: {lang} text names {', '.join('{' + one + '}' for one in unknown)}, only {{player}} is known")
        said[lang] = value.strip()
    return Words(**said)


def _name(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not NAME.match(raw):
        raise ChapterError(f"{where}: {raw!r} is not a name of lowercase letters, digits and underscores")
    return raw


def _condition(raw: Any, where: str) -> str:
    if not isinstance(raw, str):
        raise ChapterError(f"{where}: a condition is a flag name")
    bare = raw[1:] if raw.startswith("!") else raw
    chapter, _, name = bare.rpartition(":")
    if chapter:
        _name(chapter, where)
    stem, _, outcome = name.partition(".")
    _name(stem, where)
    if outcome and outcome not in OUTCOMES:
        raise ChapterError(f"{where}: {outcome!r} is not one of {', '.join(OUTCOMES)}")
    return raw


def _lines(raw: Any, where: str, speakers: dict[str, Words]) -> tuple[Line, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ChapterError(f"{where}: lines must be a list")
    found = []
    for at, line in enumerate(raw):
        here = f"{where} line {at + 1}"
        if not isinstance(line, dict):
            raise ChapterError(f"{here}: a line is a table")
        who = line.get("who")
        if who not in speakers:
            raise ChapterError(f"{here}: {who!r} is not among the speakers")
        when = line.get("when", [])
        if isinstance(when, str):
            when = [when]
        if not isinstance(when, list):
            raise ChapterError(f"{here}: when must be a flag or a list of flags")
        found.append(Line(who, _text(line, here), tuple(_condition(one, here) for one in when)))
    return tuple(found)


def _goal(raw: Any, where: str) -> Goal:
    if raw is None:
        return Goal()
    if not isinstance(raw, dict):
        raise ChapterError(f"{where}: goal must be a table")
    unknown = set(raw) - {"accuracy", "full_combo", "mods"}
    if unknown:
        raise ChapterError(f"{where}: unknown goal fields {sorted(unknown)}")
    accuracy = raw.get("accuracy")
    if accuracy is not None and (isinstance(accuracy, bool) or not isinstance(accuracy, (int, float)) or not 0 < accuracy <= 100):
        raise ChapterError(f"{where}: accuracy is a percentage above 0 and at most 100")
    full_combo = raw.get("full_combo", False)
    if not isinstance(full_combo, bool):
        raise ChapterError(f"{where}: full_combo is true or false")
    mods = raw.get("mods", [])
    if not isinstance(mods, list) or not all(isinstance(mod, str) and mod.upper() in ALLOWED_MODS for mod in mods):
        raise ChapterError(f"{where}: mods must be a list of {', '.join(sorted(ALLOWED_MODS))}")
    return Goal(float(accuracy) if accuracy is not None else None, full_combo, frozenset(mod.upper() for mod in mods))


def _task(raw: dict, where: str) -> Task:
    slot = raw.get("slot")
    if slot not in SLOT_MODS:
        raise ChapterError(f"{where}: {slot!r} is not a slot")
    below = raw.get("below", 0.0)
    if isinstance(below, bool) or not isinstance(below, (int, float)) or not 0 <= below <= 3:
        raise ChapterError(f"{where}: below is between 0 and 3 stars")
    return Task(slot, float(below), _goal(raw.get("goal"), where), _name(raw.get("then"), f"{where} then"))


def _options(raw: Any, where: str) -> tuple[Option, ...]:
    if not isinstance(raw, list) or not OPTIONS_LEAST <= len(raw) <= OPTIONS_MOST:
        raise ChapterError(f"{where}: a choice has {OPTIONS_LEAST} or {OPTIONS_MOST} options")
    found = []
    for at, option in enumerate(raw):
        here = f"{where} option {at + 1}"
        if not isinstance(option, dict):
            raise ChapterError(f"{here}: an option is a table")
        found.append(Option(_name(option.get("id"), here), _text(option, here), _name(option.get("flag"), here),
                            _name(option.get("next"), f"{here} next")))
    if len({option.id for option in found}) != len(found) or len({option.flag for option in found}) != len(found):
        raise ChapterError(f"{where}: options need their own ids and flags")
    return tuple(found)


def _step(raw: Any, at: int, speakers: dict[str, Words]) -> Step:
    if not isinstance(raw, dict):
        raise ChapterError(f"step {at + 1}: a step is a table")
    step_id = _name(raw.get("id"), f"step {at + 1}")
    where = f"step {step_id}"
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ChapterError(f"{where}: kind is one of {', '.join(KINDS)}")
    lines = _lines(raw.get("lines"), where, speakers)
    if kind == "scene":
        if not lines:
            raise ChapterError(f"{where}: a scene says something")
        return Step(step_id, kind, lines, next=_name(raw.get("next"), f"{where} next"))
    if kind == "task":
        return Step(step_id, kind, lines, task=_task(raw, where))
    if kind == "choice":
        return Step(step_id, kind, lines, options=_options(raw.get("options"), where))
    following = raw.get("next_chapter")
    return Step(step_id, kind, lines, next_chapter=_name(following, f"{where} next_chapter") if following is not None else None)


def _targets(step: Step) -> list[str]:
    if step.kind == "scene":
        return [step.next]
    if step.kind == "task":
        return [step.task.then]
    if step.kind == "choice":
        return [option.next for option in step.options]
    return []


def _check_graph(chapter: Chapter) -> None:
    for step in chapter.steps.values():
        for target in _targets(step):
            if target not in chapter.steps:
                raise ChapterError(f"step {step.id}: leads to {target!r}, which is not a step")
    state: dict[str, int] = {}

    def walk(name: str) -> None:
        if state.get(name) == 1:
            raise ChapterError(f"step {name}: the chapter loops back to it")
        if state.get(name) == 2:
            return
        state[name] = 1
        for target in _targets(chapter.steps[name]):
            walk(target)
        state[name] = 2

    walk(chapter.start)
    lost = sorted(set(chapter.steps) - set(state))
    if lost:
        raise ChapterError(f"steps never reached from {chapter.start}: {', '.join(lost)}")


def _check_conditions(chapter: Chapter, known: set[str]) -> None:
    for step in chapter.steps.values():
        for line in step.lines:
            for condition in line.when:
                flag = chapter.flag(condition.lstrip("!"))
                if flag.startswith(f"{chapter.id}:") and flag not in known:
                    raise ChapterError(f"step {step.id}: {condition!r} is never set in this chapter")


def parse(text: str) -> Chapter:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ChapterError(f"not TOML: {error}") from error
    chapter_id = _name(raw.get("id"), "chapter id")
    speakers_raw = raw.get("speakers")
    if not isinstance(speakers_raw, dict) or not speakers_raw:
        raise ChapterError("a chapter names its speakers")
    speakers = {_name(key, "speaker"): _text(value, f"speaker {key}") for key, value in speakers_raw.items()}
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ChapterError("a chapter has steps")
    steps: dict[str, Step] = {}
    for at, one in enumerate(steps_raw):
        step = _step(one, at, speakers)
        if step.id in steps:
            raise ChapterError(f"step {step.id}: named twice")
        steps[step.id] = step
    start = _name(raw.get("start"), "start")
    if start not in steps:
        raise ChapterError(f"start {start!r} is not a step")
    chapter = Chapter(chapter_id, _text(raw.get("title"), "title"), start, speakers, steps)
    _check_graph(chapter)
    _check_conditions(chapter, chapter.flags())
    return chapter


def load(folder: Path = FOLDER) -> dict[str, Chapter]:
    found: dict[str, Chapter] = {}
    for path in sorted(folder.glob("*.toml")):
        try:
            chapter = parse(path.read_text(encoding="utf-8"))
        except ChapterError as error:
            raise ChapterError(f"{path.name}: {error}") from error
        if chapter.id in found:
            raise ChapterError(f"{path.name}: chapter {chapter.id} is written twice")
        found[chapter.id] = chapter
    for chapter in found.values():
        for step in chapter.steps.values():
            if step.next_chapter is not None and step.next_chapter not in found:
                raise ChapterError(f"{chapter.id}: step {step.id} leads to chapter {step.next_chapter}, which is not written")
    return found

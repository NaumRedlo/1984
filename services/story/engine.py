from dataclasses import dataclass, field
from typing import Any, Optional

from services.story.chapters import Chapter, Line, Step


class StoryError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Assignment:
    beatmap_id: int
    stars: float
    opened_at: str
    tries: int = 0
    eased: bool = False

    def body(self) -> dict[str, Any]:
        return {"beatmap_id": self.beatmap_id, "stars": self.stars, "opened_at": self.opened_at, "tries": self.tries, "eased": self.eased}

    @staticmethod
    def of(raw: Any) -> Optional["Assignment"]:
        if not isinstance(raw, dict):
            return None
        try:
            return Assignment(int(raw["beatmap_id"]), float(raw["stars"]), str(raw["opened_at"]), int(raw.get("tries", 0)), bool(raw.get("eased", False)))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class Progress:
    chapter: str
    step: str
    flags: set[str] = field(default_factory=set)
    task: Optional[Assignment] = None
    done: list[str] = field(default_factory=list)


def begin(chapter: Chapter, flags: Optional[set[str]] = None, done: Optional[list[str]] = None) -> Progress:
    return Progress(chapter.id, chapter.start, set(flags or ()), None, list(done or []))


def holds(chapter: Chapter, flags: set[str], condition: str) -> bool:
    wanted = not condition.startswith("!")
    return (chapter.flag(condition.lstrip("!")) in flags) == wanted


def heard(chapter: Chapter, flags: set[str], step: Step) -> list[Line]:
    return [line for line in step.lines if all(holds(chapter, flags, condition) for condition in line.when)]


def _current(chapter: Chapter, progress: Progress) -> Step:
    if progress.chapter != chapter.id or progress.step not in chapter.steps:
        raise StoryError("LOST_STEP")
    return chapter.steps[progress.step]


def _expect(progress: Progress, step_id: Optional[str]) -> None:
    if step_id is not None and step_id != progress.step:
        raise StoryError("STEP_MOVED")


def spoken(text: str, player: str) -> str:
    return text.replace("{player}", player)


def shown(chapter: Chapter, progress: Progress, lang: str, player: str = "") -> dict[str, Any]:
    step = _current(chapter, progress)
    body: dict[str, Any] = {
        "chapter": chapter.id,
        "title": chapter.title.said(lang),
        "step": step.id,
        "kind": step.kind,
        "lines": [{"who": line.who, "name": chapter.speakers[line.who].said(lang), "text": spoken(line.words.said(lang), player)}
                  for line in heard(chapter, progress.flags, step)],
    }
    if step.kind == "task":
        goal = step.task.goal
        body["task"] = {
            "slot": step.task.slot,
            "goal": {"accuracy": goal.accuracy, "full_combo": goal.full_combo, "mods": sorted(goal.mods)},
            "map": progress.task.body() if progress.task is not None else None,
        }
    if step.kind == "choice":
        body["options"] = [{"id": option.id, "text": spoken(option.words.said(lang), player)} for option in step.options]
    if step.kind == "end":
        body["next_chapter"] = step.next_chapter
    return body


def go_on(chapter: Chapter, progress: Progress, step_id: Optional[str] = None) -> Progress:
    _expect(progress, step_id)
    step = _current(chapter, progress)
    if step.kind != "scene":
        raise StoryError("NOT_A_SCENE")
    progress.step = step.next
    return progress


def choose(chapter: Chapter, progress: Progress, option_id: str, step_id: Optional[str] = None) -> Progress:
    _expect(progress, step_id)
    step = _current(chapter, progress)
    if step.kind != "choice":
        raise StoryError("NOT_A_CHOICE")
    option = next((one for one in step.options if one.id == option_id), None)
    if option is None:
        raise StoryError("NO_SUCH_OPTION")
    progress.flags.add(chapter.flag(option.flag))
    progress.step = option.next
    return progress


def settle(chapter: Chapter, progress: Progress, outcomes: set[str]) -> Progress:
    step = _current(chapter, progress)
    if step.kind != "task":
        raise StoryError("NOT_A_TASK")
    progress.flags.update(chapter.flag(f"{step.id}.{outcome}") for outcome in outcomes | {"passed"})
    progress.task = None
    progress.step = step.task.then
    return progress


def close(chapter: Chapter, progress: Progress, following: Optional[Chapter], step_id: Optional[str] = None) -> Progress:
    _expect(progress, step_id)
    step = _current(chapter, progress)
    if step.kind != "end":
        raise StoryError("NOT_AN_END")
    done = progress.done + ([chapter.id] if chapter.id not in progress.done else [])
    if following is None:
        progress.done = done
        return progress
    return begin(following, progress.flags, done)

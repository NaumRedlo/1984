from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TitleDef:
    code: str
    name: str
    description: str
    target: int
    category: str       # "score", "stats", "special"
    color: tuple[int, int, int]


TITLE_REGISTRY: dict[str, TitleDef] = {
    "star_hunter": TitleDef(
        code="star_hunter",
        name="Star Hunter",
        description="Get S rank or higher on 100 maps with 5+ stars",
        target=100,
        category="score",
        color=(255, 200, 50),       # gold
    ),
    "score_lord": TitleDef(
        code="score_lord",
        name="Score Lord",
        description="Reach 100 billion total score",
        target=100_000_000_000,
        category="stats",
        color=(100, 180, 255),      # blue
    ),
    "contributor": TitleDef(
        code="contributor",
        name="Contributor",
        description="Contribute to the development of Project 1984",
        target=1,
        category="special",
        color=(180, 100, 255),      # purple
    ),
}

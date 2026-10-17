import logging
from typing import Optional

logger = logging.getLogger(__name__)

RULESETS = {0: "osu", 1: "taiko", 2: "fruits", 3: "mania"}
TITLES = {0: "osu!", 1: "osu!taiko", 2: "osu!catch", 3: "osu!mania"}

_ALIASES = {
    "osu": 0, "o": 0, "std": 0, "standard": 0,
    "taiko": 1, "t": 1, "тайко": 1,
    "catch": 2, "c": 2, "ctb": 2, "fruits": 2, "кетч": 2,
    "mania": 3, "m": 3, "мания": 3,
}

def parse(word: str) -> Optional[int]:
    """The ruleset a word names ("taiko", "m", "-m 3" split into words is not handled), or None."""
    word = (word or "").strip().lower().lstrip("-")
    return _ALIASES.get(word)

def split_args(text: str) -> tuple[Optional[int], str]:
    """Take a ruleset named as the first or last word off an argument line: "mania cookiezi" → (3, "cookiezi")."""
    words = (text or "").split()
    if words and parse(words[0]) is not None:
        return parse(words[0]), " ".join(words[1:])
    if len(words) > 1 and parse(words[-1]) is not None:
        return parse(words[-1]), " ".join(words[:-1])
    return None, (text or "").strip()

def of_score(raw: dict) -> int:
    """The ruleset a score was played in — for a converted map not the map's own mode."""
    rid = raw.get("ruleset_id")
    if isinstance(rid, int) and rid in RULESETS:
        return rid
    mode = raw.get("mode_int")
    if isinstance(mode, int) and mode in RULESETS:
        return mode
    name = raw.get("mode") or (raw.get("beatmap") or {}).get("mode")
    return {v: k for k, v in RULESETS.items()}.get(name, 0)

async def preferred(telegram_id) -> Optional[int]:
    from sqlalchemy import select
    from db.database import get_db_session
    from db.models.player import Player
    if not telegram_id:
        return None
    try:
        async with get_db_session() as session:
            held = (await session.execute(select(Player.command_ruleset).where(Player.telegram_id == telegram_id))).scalar_one_or_none()
    except Exception:
        logger.warning("rulesets: the preferred mode of %s was not read", telegram_id, exc_info=True)
        return None
    return held if held in RULESETS else None

async def chosen(text: str, telegram_id) -> tuple[Optional[int], str]:
    ruleset, rest = split_args(text)
    if ruleset is None:
        ruleset = await preferred(telegram_id)
    return ruleset, rest

async def prefer(telegram_id, ruleset: int) -> bool:
    from sqlalchemy import select
    from db.database import get_db_session
    from db.models.player import Player
    if ruleset not in RULESETS or not telegram_id:
        return False
    async with get_db_session() as session:
        player = (await session.execute(select(Player).where(Player.telegram_id == telegram_id))).scalar_one_or_none()
        if player is None:
            return False
        player.command_ruleset = ruleset
        await session.commit()
    return True

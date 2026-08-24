"""A judged replay, in the shape the osu! API gives a score.

The bot already draws a result card — the one `rs` sends — and it is built from
a raw API score. A replay is the same event arriving by a different road: a
player, a map, four counts and a combo. So rather than a second card renderer
with its own layout to drift, this turns the one into the other and hands it to
the renderer that exists.

Two things are not in a replay and are worked out here. **The rank** is a
letter osu! computes from the counts and never writes into the file, and **the
pp** the card asks for is computed downstream from the map and the mods. What
*is* in the file is used as it stands: the counts, the combo and the accuracy
are the client's own, not this engine's judgement of them, because the card
says what the player got and the engine's opinion of that lives behind the
buttons under it.
"""

from typing import Optional

# Every osu! mod acronym is two characters, `V2` and `TD` included, so a joined
# string splits without a table to consult.
_ACRONYM = 2


def _mod_list(joined: str) -> list[str]:
    text = (joined or "").replace(",", "").strip()
    if text.upper() in ("", "NM", "NONE"):
        return []
    return [text[at : at + _ACRONYM] for at in range(0, len(text), _ACRONYM)]


def rank_of(counts: dict, mods: list[str]) -> str:
    """The letter osu! would put on this play.

    stable's own ladder, which is stated in shares of the object count rather
    than in accuracy:

    * **SS** — nothing but 300s.
    * **S** — over 90% of them 300s, under 1% 50s, and nothing missed.
    * **A** — over 80% 300s and nothing missed, or over 90% either way.
    * **B** — over 70% 300s and nothing missed, or over 80% either way.
    * **C** — over 60%.
    * **D** — anything else.

    Hidden or Flashlight turn S and SS into their silver forms. That is a
    presentation rule rather than a scoring one, which is why it sits at the end
    rather than in the ladder.
    """
    great = counts.get("300", 0)
    ok = counts.get("100", 0)
    meh = counts.get("50", 0)
    miss = counts.get("miss", 0)
    total = great + ok + meh + miss
    if total <= 0:
        return "F"

    share_300 = great / total
    share_50 = meh / total
    silver = "HD" in mods or "FL" in mods

    if ok == 0 and meh == 0 and miss == 0:
        return "XH" if silver else "X"
    if share_300 > 0.90 and share_50 < 0.01 and miss == 0:
        return "SH" if silver else "S"
    if (share_300 > 0.80 and miss == 0) or share_300 > 0.90:
        return "A"
    if (share_300 > 0.70 and miss == 0) or share_300 > 0.80:
        return "B"
    if share_300 > 0.60:
        return "C"
    return "D"


def score_from_replay(result: dict, beatmap: Optional[dict]) -> dict:
    """The judged replay as a raw API score, ready for `build_recent_card_data`.

    `beatmap` is the API's own record — the one `ensure_map` hands back — so the
    artist, the title, the difficulty and the star rating come from osu! rather
    than from the `.osu` the engine happened to read.
    """
    counts = result.get("theirs") or {}
    mods = _mod_list(result.get("mods", ""))
    beatmap = beatmap or {}
    return {
        # The client's own numbers, not this engine's reading of them. What the
        # engine thinks is a separate question and has its own buttons.
        "statistics": {
            "count_300": counts.get("300", 0),
            "count_100": counts.get("100", 0),
            "count_50": counts.get("50", 0),
            "count_miss": counts.get("miss", 0),
        },
        # The API states accuracy as a fraction and the engine as a percentage.
        "accuracy": float(result.get("their_accuracy", 0.0)) / 100.0,
        "max_combo": result.get("their_max_combo", 0),
        "mods": mods,
        "rank": rank_of(counts, mods),
        # A replay that ran out of objects is a replay that finished. A failed
        # one stops early, and the engine says where.
        "passed": bool(result.get("finished", True)),
        "beatmap": beatmap,
        "beatmapset": beatmap.get("beatmapset", {}) or {},
        # Left for the card to compute: it already asks the pp calculator, and
        # a figure invented here would be a second answer to the same question.
        "pp": None,
        # No date. A replay carries when it was *played*, which the header does
        # not state in a form worth guessing at, and an invented timestamp on a
        # card that shows one is worse than a blank.
        "ended_at": "",
    }

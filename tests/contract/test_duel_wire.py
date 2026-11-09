import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from services.duels import series, service, states
from services.duels.verifier import Expected, judge
from tests.unit.test_duels_series import Clock, _ban_all, _drafting, _play, _view, factory

WIRE = Path(__file__).parent / "wire"
WRITING = os.environ.get("WIRE_WRITE") == "1"

def shape(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        merged = None
        for item in value:
            merged = shape(item) if merged is None else merge(merged, shape(item))
        return [merged] if merged is not None else []
    return {key: shape(inner) for key, inner in value.items()}

def merge(first, second):
    if isinstance(first, dict) and isinstance(second, dict):
        return {key: merge(first.get(key, "absent"), second.get(key, "absent")) for key in sorted(set(first) | set(second))}
    if isinstance(first, list) and isinstance(second, list):
        if not first or not second:
            return first or second
        return [merge(first[0], second[0])]
    if first == second:
        return first
    names = set()
    for side in (first, second):
        names |= set(side.split("|")) if isinstance(side, str) else {json.dumps(side, sort_keys=True)}
    return "|".join(sorted(names))

def kept(name: str, fresh: dict) -> None:
    file = WIRE / f"{name}.json"
    if WRITING:
        file.write_text(json.dumps(fresh, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    held = json.loads(file.read_text(encoding="utf-8"))
    assert shape(fresh) == shape(held), f"{name}: the answer changed its shape; write the samples again and copy them to Dossier"

async def test_the_duel_answers_dossier_reads_keep_their_shape(factory):
    clock = Clock()
    invited = await service.invite(factory, 1, 2, clock.op(), now=clock.now)
    kept("duel_invited", await _view(factory, invited["id"], 2))
    await service.decline(factory, invited["id"], 2, clock.op(), now=clock.tick())

    duel_id = await _drafting(factory, clock)
    kept("duel_drafting", await _view(factory, duel_id, 1))
    await _ban_all(factory, clock, duel_id)
    await _play(factory, clock, duel_id, "A")
    duel = await _view(factory, duel_id, 1)
    _, side = series.turn_side(len(duel["draft"]))
    free = [e["slot"] for e in duel["pool"]["entries"] if e["slot"] != "TB" and not e["banned_by"] and not e["used"]]
    picker = next(p["player"] for p in duel["participants"] if p["side"] == side)
    await series.draft(factory, duel_id, picker, free[0], clock.op(), now=clock.tick())
    await series.round_configured(factory, duel_id, clock.tick())
    ready = await _view(factory, duel_id, 1)
    assert ready["state"] == states.WAITING_READY
    kept("duel_ready", ready)
    await series.room_all_ready(factory, duel_id, clock.tick())
    await series.round_started(factory, duel_id, 777, clock.tick())
    await series.round_finished(factory, duel_id, clock.tick(200))
    scores = {str(p["osu_user_id"]): (900000 if p["side"] == "A" else 800000) for p in ready["participants"]}
    winner = next(p["osu_user_id"] for p in ready["participants"] if p["side"] == "A")
    await series.round_verdict(factory, duel_id, {"state": "valid", "winner": winner, "scores": scores}, clock.tick())
    done = await _play(factory, clock, duel_id, "A")
    assert done["state"] == states.COMPLETED
    kept("duel_completed", await _view(factory, duel_id, 1))

def test_a_real_match_answer_from_osu_names_its_winner():
    game = json.loads((WIRE / "osu_match_game.json").read_text(encoding="utf-8"))
    expected = Expected(beatmap_id=4143588, players=frozenset({26253598, 18767984}), mods=frozenset({"NF"}), tiebreak=False,
                        started_after=datetime(2026, 11, 8, 14, 58, 0))
    verdict = judge(game, expected)
    assert (verdict.state, verdict.winner, verdict.scores) == ("valid", 26253598, {26253598: 374888, 18767984: 173532})

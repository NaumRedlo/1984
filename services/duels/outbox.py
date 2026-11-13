from typing import Any

from db.models.duel import DuelCommand
from services.outbox import Handler, Outbox

CLAIM_SECONDS = 60
MOST_ATTEMPTS = 10
LONGEST_PAUSE = 300

HANDLERS: dict[str, Handler] = {}


def _told(row) -> dict[str, Any]:
    return {"id": row.id, "duel": row.duel_id, "kind": row.kind, "payload": row.payload,
            "attempts": row.attempts, "epoch": row.room_epoch}


_box = Outbox(DuelCommand, lambda: HANDLERS, "duel command", told=_told, claim_seconds=CLAIM_SECONDS,
              most_attempts=MOST_ATTEMPTS, longest_pause=LONGEST_PAUSE)

claim = _box.claim
settle = _box.settle
sweep = _box.sweep

import uuid

from sqlalchemy import (BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text,
                        UniqueConstraint, text)

from db.database import Base
from utils.timeutils import utcnow

def new_id() -> str:
    return str(uuid.uuid4())

class Duel(Base):
    __tablename__ = "duel_series"

    id = Column(String(36), primary_key=True, default=new_id)
    mode = Column(String(16), nullable=False)
    state = Column(String(24), nullable=False)
    state_version = Column(Integer, nullable=False, default=1)
    rules_version = Column(String(32), nullable=False)
    seed = Column(String(64), nullable=False)
    created_by = Column(Integer, ForeignKey("players.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)
    finalized_at = Column(DateTime, nullable=True)
    outcome_reason = Column(String(32), nullable=True)
    winner_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    wins_a = Column(Integer, nullable=False, default=0)
    wins_b = Column(Integer, nullable=False, default=0)
    paused_from = Column(String(24), nullable=True)
    pause_reason = Column(String(32), nullable=True)

    __table_args__ = (Index("ix_duel_series_state", "state"),)

class DuelParticipant(Base):
    __tablename__ = "duel_participants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    duel_id = Column(String(36), ForeignKey("duel_series.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    osu_user_id = Column(BigInteger, nullable=False)
    side = Column(String(1), nullable=True)
    invited = Column(Boolean, nullable=False, default=False)
    accepted_at = Column(DateTime, nullable=True)
    connection_state = Column(String(16), nullable=False, default="unknown")
    calibration_snapshot = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("duel_id", "player_id", name="uq_duel_participants_player"),
        UniqueConstraint("duel_id", "side", name="uq_duel_participants_side"),
        Index("ix_duel_participants_player", "player_id"),
    )

class DuelLease(Base):
    __tablename__ = "duel_leases"

    player_id = Column(Integer, ForeignKey("players.id"), primary_key=True)
    kind = Column(String(8), nullable=False)
    duel_id = Column(String(36), ForeignKey("duel_series.id", ondelete="CASCADE"), nullable=True)
    since = Column(DateTime, nullable=False, default=utcnow)

class DuelQueueEntry(Base):
    __tablename__ = "duel_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    mode = Column(String(16), nullable=False)
    enqueued_at = Column(DateTime, nullable=False, default=utcnow)
    heartbeat_at = Column(DateTime, nullable=False, default=utcnow)
    status = Column(String(16), nullable=False)
    left_at = Column(DateTime, nullable=True)
    profile_snapshot_id = Column(String(36), nullable=True)

    __table_args__ = (Index("ix_duel_queue_status", "status", "enqueued_at"),)

class DuelOperation(Base):
    __tablename__ = "duel_operations"

    operation_id = Column(String(64), primary_key=True)
    actor_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    kind = Column(String(24), nullable=False)
    duel_id = Column(String(36), nullable=True)
    response = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)

class DuelEvent(Base):
    __tablename__ = "duel_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    duel_id = Column(String(36), ForeignKey("duel_series.id", ondelete="CASCADE"), nullable=False)
    sequence = Column(Integer, nullable=False)
    type = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False)
    actor_id = Column(Integer, nullable=True)
    state_version = Column(Integer, nullable=False)
    occurred_at = Column(DateTime, nullable=False, default=utcnow)
    received_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("duel_id", "sequence", name="uq_duel_events_sequence"),)

class DuelCommand(Base):
    __tablename__ = "duel_commands"

    id = Column(Integer, primary_key=True, autoincrement=True)
    duel_id = Column(String(36), ForeignKey("duel_series.id", ondelete="CASCADE"), nullable=True)
    room_epoch = Column(Integer, nullable=True)
    kind = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=utcnow)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    done_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_duel_commands_due", "status", "next_attempt_at"),)

class DuelDeadline(Base):
    __tablename__ = "duel_deadlines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    duel_id = Column(String(36), ForeignKey("duel_series.id", ondelete="CASCADE"), nullable=False)
    state_version = Column(Integer, nullable=False)
    kind = Column(String(32), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    outcome = Column(String(16), nullable=True)

    __table_args__ = (
        UniqueConstraint("duel_id", "state_version", "kind", name="uq_duel_deadlines_once"),
        Index("ix_duel_deadlines_due", "processed_at", "expires_at"),
    )

class DuelNotification(Base):
    __tablename__ = "duel_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("duel_events.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    channel = Column(String(16), nullable=False)
    payload = Column(JSON, nullable=False)
    delivery_state = Column(String(16), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("event_id", "player_id", "channel", name="uq_duel_notifications_once"),)

class DuelPool(Base):
    __tablename__ = "duel_pools"

    id = Column(String(36), primary_key=True, default=new_id)
    duel_id = Column(String(36), ForeignKey("duel_series.id", ondelete="CASCADE"), nullable=False, unique=True)
    manifest_hash = Column(String(64), nullable=False)
    catalog_version = Column(String(32), nullable=False)
    analyzer_version = Column(String(32), nullable=False)
    profile_versions = Column(JSON, nullable=False)
    relaxation_level = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    revealed_at = Column(DateTime, nullable=True)

class DuelPoolEntry(Base):
    __tablename__ = "duel_pool_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pool_id = Column(String(36), ForeignKey("duel_pools.id", ondelete="CASCADE"), nullable=False)
    slot = Column(String(4), nullable=False)
    beatmap_id = Column(BigInteger, nullable=False)
    revision_hash = Column(String(64), nullable=False)
    mods = Column(JSON, nullable=False)
    metrics_snapshot = Column(JSON, nullable=False)
    confidence = Column(Float, nullable=True)
    review_state = Column(String(16), nullable=False)
    banned_by = Column(Integer, ForeignKey("players.id"), nullable=True)
    used = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("pool_id", "slot", name="uq_duel_pool_entries_slot"),
        UniqueConstraint("pool_id", "beatmap_id", name="uq_duel_pool_entries_map"),
    )

class DuelPoolDelivery(Base):
    __tablename__ = "duel_pool_deliveries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pool_id = Column(String(36), ForeignKey("duel_pools.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    manifest_hash = Column(String(64), nullable=False)
    shown_at = Column(DateTime, nullable=False, default=utcnow)
    acknowledged_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("pool_id", "player_id", name="uq_duel_pool_deliveries_player"),)

class DuelDraftAction(Base):
    __tablename__ = "duel_draft_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    duel_id = Column(String(36), ForeignKey("duel_series.id", ondelete="CASCADE"), nullable=False)
    turn_index = Column(Integer, nullable=False)
    actor_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    type = Column(String(8), nullable=False)
    slot = Column(String(4), nullable=False)
    automatic = Column(Boolean, nullable=False, default=False)
    operation_id = Column(String(64), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("duel_id", "turn_index", name="uq_duel_draft_turn"),)

class DuelRound(Base):
    __tablename__ = "duel_series_rounds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    duel_id = Column(String(36), ForeignKey("duel_series.id", ondelete="CASCADE"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    slot = Column(String(4), nullable=False)
    state = Column(String(16), nullable=False)
    selected_by = Column(Integer, ForeignKey("players.id"), nullable=True)
    tb_mods = Column(JSON, nullable=True)
    winner_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    tie_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("duel_id", "ordinal", name="uq_duel_series_rounds_ordinal"),)

class DuelAttempt(Base):
    __tablename__ = "duel_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey("duel_series_rounds.id", ondelete="CASCADE"), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    match_id = Column(BigInteger, nullable=True)
    game_id = Column(BigInteger, nullable=True, unique=True)
    expected_mods = Column(JSON, nullable=False)
    last_event_id = Column(BigInteger, nullable=True)
    start_at = Column(DateTime, nullable=True)
    end_at = Column(DateTime, nullable=True)
    validity = Column(String(16), nullable=False, default="pending")
    invalid_reason = Column(String(32), nullable=True)

    __table_args__ = (UniqueConstraint("round_id", "attempt_no", name="uq_duel_attempts_number"),)

class DuelEvidence(Base):
    __tablename__ = "duel_evidence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(BigInteger, nullable=False)
    event_id = Column(BigInteger, nullable=True)
    game_id = Column(BigInteger, nullable=True)
    payload_hash = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    received_at = Column(DateTime, nullable=False, default=utcnow)
    source_version = Column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("match_id", "event_id", "payload_hash", name="uq_duel_evidence_version"),
        Index("ix_duel_evidence_game", "game_id"),
    )

class DuelRoundScore(Base):
    __tablename__ = "duel_round_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attempt_id = Column(Integer, ForeignKey("duel_attempts.id", ondelete="CASCADE"), nullable=False)
    osu_user_id = Column(BigInteger, nullable=False)
    score_v2 = Column(BigInteger, nullable=False)
    mods = Column(JSON, nullable=False)
    accuracy = Column(Float, nullable=True)
    max_combo = Column(Integer, nullable=True)
    statistics = Column(JSON, nullable=True)
    passed = Column(Boolean, nullable=True)
    evidence_id = Column(Integer, ForeignKey("duel_evidence.id"), nullable=True)

    __table_args__ = (UniqueConstraint("attempt_id", "osu_user_id", name="uq_duel_round_scores_player"),)

class DuelRoom(Base):
    __tablename__ = "duel_rooms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    duel_id = Column(String(36), ForeignKey("duel_series.id", ondelete="CASCADE"), nullable=False)
    room_name = Column(String(64), nullable=False)
    channel = Column(String(32), nullable=True)
    match_id = Column(BigInteger, nullable=True)
    origin = Column(String(16), nullable=False)
    creator_osu_id = Column(BigInteger, nullable=True)
    capabilities = Column(JSON, nullable=True)
    lease_epoch = Column(Integer, nullable=False, default=0)
    lease_owner = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    closed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("uq_duel_rooms_active_match", "match_id", unique=True,
              sqlite_where=text("status NOT IN ('closed', 'released')"),
              postgresql_where=text("status NOT IN ('closed', 'released')")),
        Index("ix_duel_rooms_status", "status"),
    )

class DuelRoomCapacity(Base):
    __tablename__ = "duel_room_capacity"

    bot_account = Column(String(32), primary_key=True)
    capacity = Column(Integer, nullable=False)
    reserved_count = Column(Integer, nullable=False, default=0)
    reconciliation_state = Column(String(16), nullable=False, default="ok")
    updated_at = Column(DateTime, nullable=False, default=utcnow)

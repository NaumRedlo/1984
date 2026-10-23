from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint

from db.database import Base
from utils.timeutils import utcnow

class DuelMapRevision(Base):
    __tablename__ = "duel_map_revisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    beatmap_id = Column(BigInteger, nullable=False)
    beatmapset_id = Column(BigInteger, nullable=True)
    hash = Column(String(32), nullable=False)
    format_version = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default="pending")
    reason = Column(String(64), nullable=True)
    ranked_status = Column(String(16), nullable=True)
    mapper = Column(String(64), nullable=True)
    source = Column(String(32), nullable=True)
    object_count = Column(Integer, nullable=True)
    max_combo = Column(Integer, nullable=True)
    downloaded_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("beatmap_id", "hash", name="uq_duel_map_revisions_hash"),
        Index("ix_duel_map_revisions_map", "beatmap_id"),
    )

class DuelMapAssignment(Base):
    __tablename__ = "duel_map_assignments"

    beatmap_id = Column(BigInteger, primary_key=True)
    slot = Column(String(4), nullable=True)
    state = Column(String(16), nullable=False, default="UNASSIGNED")
    confidence = Column(Float, nullable=True)
    margin = Column(Float, nullable=True)
    assignment_version = Column(String(32), nullable=True)
    revision_hash = Column(String(32), nullable=True)
    admitted = Column(Boolean, nullable=False, default=False)
    locked_at = Column(DateTime, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_duel_map_assignments_slot", "slot", "state"),)

class DuelMapAnalysis(Base):
    __tablename__ = "duel_map_analyses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    revision_hash = Column(String(32), nullable=False)
    mods = Column(String(16), nullable=False)
    engine_version = Column(String(32), nullable=False)
    feature_version = Column(String(32), nullable=False)
    model_version = Column(String(32), nullable=False)
    stars = Column(Float, nullable=True)
    features = Column(JSON, nullable=False)
    predictions = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("revision_hash", "mods", "engine_version", "feature_version", "model_version",
                         name="uq_duel_map_analyses_versions"),
    )

class DuelAssignmentLog(Base):
    __tablename__ = "duel_assignment_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    beatmap_id = Column(BigInteger, nullable=False)
    actor_id = Column(Integer, nullable=True)
    action = Column(String(16), nullable=False)
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_duel_assignment_log_map", "beatmap_id"),)

class DuelTrainingExample(Base):
    __tablename__ = "duel_training_examples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corpus_version = Column(String(32), nullable=False)
    source = Column(String(256), nullable=False)
    year = Column(Integer, nullable=False)
    stage = Column(String(32), nullable=False)
    original_slot = Column(String(8), nullable=False)
    beatmap_id = Column(BigInteger, nullable=False)
    beatmapset_id = Column(BigInteger, nullable=True)
    mods = Column(String(16), nullable=True)
    revision_hash = Column(String(32), nullable=True)
    ranked_status = Column(String(16), nullable=True)
    normalized_label = Column(String(8), nullable=True)
    conflict = Column(Boolean, nullable=False, default=False)
    fetched_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("corpus_version", "year", "stage", "original_slot", "beatmap_id", name="uq_duel_training_examples_seen"),
        Index("ix_duel_training_examples_map", "beatmap_id"),
    )

class DuelCorpusLabel(Base):
    __tablename__ = "duel_corpus_labels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corpus_version = Column(String(32), nullable=False)
    year = Column(Integer, nullable=False)
    stage = Column(String(32), nullable=False)
    original_slot = Column(String(8), nullable=False)
    beatmap_id = Column(BigInteger, nullable=True)
    label = Column(String(8), nullable=False)
    reviewer_id = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_duel_corpus_labels_cell", "corpus_version", "year", "stage", "original_slot"),)

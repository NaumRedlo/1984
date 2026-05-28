"""HpsMapPool — HPS-side map pool for weekly bounty generation.

Plan: unified-giggling-tiger (step 4/9).

Counterpart to `bsk_map_pool`. The two pools are deliberately separate:

  * `bsk_map_pool` holds the BSK duel pool — narrow, ML-calibrated,
    per-axis skill stars + share weights.  Curated for duel quality.
  * `hps_map_pool` holds the HPS weekly bounty pool — wide, rule-tagged
    (genre / length / bpm buckets, per-bounty-type suitability hints),
    plus anti-repeat tracking (last_used_at / use_count).

A beatmap can live in both pools but is ingested through different
pipelines and refreshed on different schedules.  The HPS generator
reads `hps_map_pool`; the duel system reads `bsk_map_pool`.

`typing_hints` is a JSON-encoded dict of per-bounty-type suitability
scores produced by `services.hps.hps_profile.compute_hps_profile`.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text

from db.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HpsMapPool(Base):
    __tablename__ = "hps_map_pool"

    id = Column(Integer, primary_key=True, autoincrement=True)
    beatmap_id    = Column(Integer, unique=True, nullable=False, index=True)
    beatmapset_id = Column(Integer, nullable=False)

    # Metadata
    title   = Column(String(255), nullable=False)
    artist  = Column(String(255), nullable=False)
    version = Column(String(255), nullable=False)
    creator = Column(String(255), nullable=True)

    # Core difficulty (HPS-relevant only — no per-axis ML stars here)
    star_rating = Column(Float, nullable=False)
    bpm         = Column(Float, nullable=True)
    length      = Column(Integer, nullable=True)    # drain seconds
    ar          = Column(Float, nullable=True)
    od          = Column(Float, nullable=True)
    cs          = Column(Float, nullable=True)
    max_combo   = Column(Integer, nullable=True)    # used by Marathon condition

    # HPS-profile output (from compute_hps_profile)
    genre_tag     = Column(String(20), nullable=True)   # stream | jump | tech | mixed
    length_bucket = Column(String(10), nullable=True)   # short | medium | long | marathon
    bpm_bucket    = Column(String(10), nullable=True)   # slow  | mid    | fast | speedcore
    ranked_status = Column(String(20), nullable=True)   # ranked | loved | qualified
    typing_hints  = Column(Text,       nullable=True)   # JSON: {bounty_type: 0..1}

    # Anti-repeat tracking (weekly generator marks last_used_at on pick)
    last_used_at = Column(DateTime, nullable=True, index=True)
    use_count    = Column(Integer, default=0, nullable=False)

    enabled  = Column(Boolean,  default=True,    nullable=False)
    added_at = Column(DateTime, default=_utcnow, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<HpsMapPool(id={self.beatmap_id}, '{self.title}', "
            f"{self.star_rating}★, {self.length_bucket}/{self.genre_tag})>"
        )

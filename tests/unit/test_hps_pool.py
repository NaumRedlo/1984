"""Unit tests for services.hps.hps_pool (no-IO surface only).

Plan: unified-giggling-tiger (step 9/9).

We don't exercise add_map_to_hps_pool / refresh_hps_map here — those
require an osu! API client and live HTTP. The integration coverage for
them belongs in a future end-to-end staging test (see plan §Verification).

This module covers the pure helpers:
  - apply_profile_to_entry: dict → ORM row mapping + JSON serialisation
  - hps_map_is_broken: reason taxonomy
"""

from __future__ import annotations

import json

from db.models.hps_map_pool import HpsMapPool
from services.hps.hps_pool import apply_profile_to_entry, hps_map_is_broken


def _entry(**overrides) -> HpsMapPool:
    defaults = dict(
        beatmap_id=1, beatmapset_id=1, title="t", artist="a", version="v",
        star_rating=5.0,
    )
    defaults.update(overrides)
    return HpsMapPool(**defaults)


class TestApplyProfile:
    def test_writes_all_bucket_fields(self):
        entry = _entry()
        apply_profile_to_entry(entry, {
            "genre_tag": "stream", "length_bucket": "medium",
            "bpm_bucket": "fast",  "ranked_status": "loved",
            "typing_hints": {"SS": 0.8},
        })
        assert entry.genre_tag     == "stream"
        assert entry.length_bucket == "medium"
        assert entry.bpm_bucket    == "fast"
        assert entry.ranked_status == "loved"

    def test_typing_hints_serialised_to_json(self):
        entry = _entry()
        apply_profile_to_entry(entry, {
            "genre_tag": "tech", "length_bucket": "short",
            "bpm_bucket": "mid", "ranked_status": "ranked",
            "typing_hints": {"Accuracy": 0.7, "SS": 0.5},
        })
        assert isinstance(entry.typing_hints, str)
        assert json.loads(entry.typing_hints) == {"Accuracy": 0.7, "SS": 0.5}

    def test_empty_hints_stored_as_null(self):
        entry = _entry()
        apply_profile_to_entry(entry, {
            "genre_tag": "mixed", "length_bucket": "short",
            "bpm_bucket": "slow", "ranked_status": "ranked",
            "typing_hints": {},
        })
        assert entry.typing_hints is None

    def test_missing_keys_leave_fields_none(self):
        entry = _entry()
        apply_profile_to_entry(entry, {})
        for f in ("genre_tag", "length_bucket", "bpm_bucket", "ranked_status"):
            assert getattr(entry, f) is None


class TestIsBroken:
    def test_complete_entry_not_broken(self):
        entry = _entry(title="Real Title")
        apply_profile_to_entry(entry, {
            "genre_tag": "stream", "length_bucket": "medium",
            "bpm_bucket": "fast",  "ranked_status": "ranked",
            "typing_hints": {"SS": 0.5},
        })
        broken, reasons = hps_map_is_broken(entry)
        assert not broken
        assert reasons == []

    def test_missing_sr_flagged(self):
        entry = _entry(title="t", star_rating=0)
        broken, reasons = hps_map_is_broken(entry)
        assert broken
        assert "sr=0" in reasons

    def test_unknown_title_flagged(self):
        entry = _entry(title="Unknown", star_rating=5.0)
        broken, reasons = hps_map_is_broken(entry)
        assert broken
        assert "no_metadata" in reasons

    def test_missing_hints_flagged(self):
        entry = _entry(title="t", star_rating=5.0)
        # typing_hints not applied → still None.
        broken, reasons = hps_map_is_broken(entry)
        assert broken
        assert "no_typing_hints" in reasons

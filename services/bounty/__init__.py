"""Bounty system v2 — weekly tier pools + tier rules.

Plan: unified-giggling-tiger.

Public API:
  tier_rules.TIER_BSK_RANGES         — BSK_map ranges per tier.
  tier_rules.pick_for_tier(maps, tier, n=9) — pool selection for one tier.
  tier_rules.assign_bounty_type(map_row, tier) — bounty_type + conditions dict.
  tier_rules.compute_bsk_map(map_row) — Σ w·stars composite for a map.
  weekly_generator.generate_weekly_pool(session) — full weekly cycle.
"""

from services.bounty import tier_rules  # noqa: F401

__all__ = ["tier_rules"]

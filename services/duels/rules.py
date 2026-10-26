RULES_VERSION = "duels-v1"

PARAMETERS = {
    "series_wins_required": 3,
    "pool_size": 9,
    "bans_per_player": 2,
    "invitation_seconds": 120,
    "pair_confirm_seconds": 30,
    "range_offer_seconds": 60,
    "preparation_seconds": 120,
    "pool_ack_seconds": 15,
    "draft_action_seconds": 30,
    "ready_seconds": 90,
    "room_join_seconds": 600,
    "reconnect_seconds": 120,
    "recovery_seconds": 600,
    "evidence_wait_seconds": 600,
    "bonus_tb_seconds": 60,
    "room_linger_seconds": 300,
    "app_heartbeat_seconds": 5,
    "app_absence_seconds": 20,
    "created_room_limit": 4,
    "managed_room_limit": 4,
    "calibration_series": 5,
    "rating_period_seconds": 3600,
    "max_disconnect_recoveries": 2,
    "max_tie_replays": 2,
    "api_duels_requests_per_minute": 30,
}

SLOTS = ("NM1", "NM2", "NM3", "NM4", "HD2", "HR2", "DT1", "DT2", "TB")
RESERVE_GROUPS = ("NM", "HD", "HR", "DT", "FM", "EZ", "FL")
RESERVE_SLOTS = ("NM5", "NM6", "HD1", "HD3", "HD4", "HR1", "HR3", "HR4", "DT3", "DT4", "FM1", "FM2", "FM3", "FM4", "EZ1", "EZ2", "FL1", "FL2")

def reserved(slot: str) -> bool:
    return slot not in SLOTS and len(slot) == 3 and slot[:2] in RESERVE_GROUPS and slot[2] in "123456789"

STARS_LEAST = 5.0
STARS_MOST = 10.0

def in_catalogue(stars) -> bool:
    return stars is not None and STARS_LEAST <= float(stars) <= STARS_MOST

MODES = ("direct", "rated")

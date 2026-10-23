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
    "bonus_tb_seconds": 30,
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

MODES = ("direct", "rated")

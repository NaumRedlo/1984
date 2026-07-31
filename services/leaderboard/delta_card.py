"""Turn raw delta standings into the labelled payload the card renderer draws.

Keeps number/word formatting out of both the query layer (services/leaderboard/
service.py) and the drawing layer (services/image/render/leaderboard_delta.py).
"""

from __future__ import annotations

from datetime import timedelta

from services.leaderboard.periods import period_bounds_msk, week_number, MSK_OFFSET
from utils.formatting.text import plural_bucket
from utils.i18n import t
from utils.titles import TITLE_REGISTRY
from utils.timeutils import utcnow

_MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# Re-exported rather than reimplemented: the rule is in utils.formatting.text,
# which is where it belongs and where Dossier's reel captions read it from too.
plural_form = plural_bucket


def _thousands(value: float, digits: int = 0) -> str:
    """1234567 -> '1 234 567' (thin-ish space, matching the mockup)."""
    return f"{value:,.{digits}f}".replace(",", " ")


def _fmt_duration(seconds: float, lang: str) -> str:
    """Play time, with localised unit suffixes."""
    h_unit, m_unit = t("lb.unit.h", lang), t("lb.unit.m", lang)
    total = int(seconds)
    hours, minutes = total // 3600, (total % 3600) // 60
    if hours:
        return f"{hours}{h_unit} {minutes:02d}{m_unit}" if minutes else f"{hours}{h_unit}"
    return f"{minutes}{m_unit}"


def format_delta(key: str, value: float, lang: str) -> str:
    """The big green number, e.g. '+412 pp' / '+0.15 п.п.' / '450 хит/плей'."""
    if key == "pp":
        return f"+{_thousands(value)} pp"
    if key == "accuracy":
        return f"+{value:.2f} " + ("п.п." if lang.startswith("ru") else "pp.")
    if key == "play_count":
        return f"+{_thousands(value)}"
    if key == "play_time":
        return "+" + _fmt_duration(value, lang)
    if key == "ranked_score":
        return f"+{_thousands(value)}"
    if key == "hits_per_play":
        # A period ratio, not a difference — no "+" sign.
        return f"{value:,.1f}".replace(",", " ")
    return f"+{value:g}"


def format_absolute(key: str, value: float, lang: str) -> str:
    """The small grey line under it — the player's lifetime figure."""
    if key == "accuracy":
        return t("lb.delta.total", lang, value=f"{value:.2f}%")
    if key == "play_time":
        return t("lb.delta.total", lang, value=_fmt_duration(value, lang))
    if key == "hits_per_play":
        return t("lb.delta.total", lang, value=f"{value:,.1f}".replace(",", " "))
    return t("lb.delta.total", lang, value=_thousands(value))


def format_gap(key: str, value: float, place: int, lang: str) -> str:
    return t("lb.delta.gap", lang, value=format_delta(key, value, lang).lstrip("+"), place=place)


def active_title(code: str | None, lang: str):
    """(label, colour) of the player's active title — the one they pinned with
    `st`. Resolved exactly like the profile card does. ("", None) when they
    haven't set one, which makes the card centre their name instead."""
    if not code:
        return "", None
    td = TITLE_REGISTRY.get(code)
    if not td:
        return "", None
    return td.name_for(lang.lower()), td.color


def period_label(period_key: str, lang: str) -> str:
    """'неделя 30 · 20–26 июля' — the header's period line."""
    start, end = period_bounds_msk(period_key)
    months = _MONTHS_RU if lang.startswith("ru") else _MONTHS_EN
    if start.month == end.month:
        span = f"{start.day}–{end.day} {months[end.month - 1]}"
    else:
        span = f"{start.day} {months[start.month - 1]} – {end.day} {months[end.month - 1]}"
    return t("lb.delta.period", lang, week=week_number(period_key), span=span)


def build_absolute_payload(board: dict, lang: str) -> dict:
    """Same card, all-time mode: lifetime values, no growth and no movement."""
    key = board["key"]
    now_msk = utcnow() + MSK_OFFSET

    def label_row(row: dict) -> dict:
        out = dict(row)
        out["title_label"], out["title_color"] = active_title(row.get("active_title_code"), lang)
        out["value_label"] = row.get("value_label") or ""
        out["sub_label"] = row.get("sub_label") or ""
        return out

    participants = board.get("participants", 0)
    return {
        "lang": lang,
        "title": t("lb.abs.title", lang),
        "subtitle": t("lb.abs.subtitle", lang),
        "meta_right": t(f"lb.cat.{key}", lang),
        "meta_right_sub": t(f"lb.delta.participants.{plural_form(participants)}", lang, n=participants),
        "fmt": {"new": t("lb.delta.new", lang)},
        "rows": [label_row(r) for r in board.get("rows", [])],
        "self_row": label_row(board["self_row"]) if board.get("self_row") else None,
        "empty_label": t("lb.abs.empty", lang),
        "show_movement": False,
        "value_positive": False,
        "footer_right": t("lb.delta.updated", lang, time=now_msk.strftime("%d.%m %H:%M")),
    }


def build_payload(board: dict, lang: str) -> dict:
    """Everything services/image/render/leaderboard_delta.py needs."""
    key = board["key"]
    now_msk = utcnow() + MSK_OFFSET

    def label_row(row: dict) -> dict:
        out = dict(row)
        out["title_label"], out["title_color"] = active_title(row.get("active_title_code"), lang)
        out["value_label"] = format_delta(key, row.get("delta", 0.0), lang)
        out["sub_label"] = format_absolute(key, row.get("absolute", 0.0), lang)
        gap = row.get("gap_to_next")
        if gap:
            out["gap_label"] = format_gap(key, gap, (row.get("position") or 1) - 1, lang)
        return out

    participants = board.get("participants", 0)
    meta_sub = t(f"lb.delta.participants.{plural_form(participants)}", lang, n=participants)
    if board.get("no_gain"):
        meta_sub += " " + t("lb.delta.sat_out", lang, n=board["no_gain"])

    empty = t("lb.delta.no_gain", lang)
    if board.get("collecting"):
        start, _ = period_bounds_msk(board["period"])
        first = (start + timedelta(days=7)).strftime("%d.%m")
        empty = t("lb.delta.collecting", lang, date=first)

    return {
        "lang": lang,
        "title": t("lb.delta.title", lang),
        "subtitle": period_label(board["period"], lang),
        "meta_right": t(f"lb.cat.{key}", lang),
        "meta_right_sub": meta_sub,
        "fmt": {"new": t("lb.delta.new", lang)},
        "rows": [label_row(r) for r in board.get("rows", [])],
        "self_row": label_row(board["self_row"]) if board.get("self_row") else None,
        "self_note": t("lb.delta.not_played", lang) if board.get("self_not_played") else None,
        "empty_label": empty,
        "show_movement": True,
        "value_positive": True,
        "footer_right": t("lb.delta.updated", lang, time=now_msk.strftime("%d.%m %H:%M")),
    }

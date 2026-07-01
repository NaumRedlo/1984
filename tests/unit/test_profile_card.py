"""Headless render checks for the profile dashboard card
(services/image/render/profile.py), incl. the 2026-07-02b EN/RU translation."""

from PIL import Image, ImageDraw, ImageFont

from services.image.core import CardRenderer
from services.image.render.profile import _fmt_last_seen, _PF_STRINGS
from services.image.utils import _find_font
from services.image.constants import TORUS_SEMI, TORUS_BOLD


def _data(lang=None, **overrides):
    d = {
        "username": "kazaki1865", "handle": "@kazaki", "osu_id": 1,
        "pp": 8234, "global_rank": 15234, "country": "RU", "country_rank": 412,
        "accuracy": 98.45, "play_count": 45231, "play_time": "1523h",
        "ranked_score": 1234567890, "total_hits": 5234123, "total_score": 9876543210,
        "level": 102, "level_progress": 45,
        "join_date": "2018-05-12T00:00:00", "last_visit": "2026-06-30T10:00:00",
        "is_online": False,
        "grade_counts": {"a": 120, "s": 340, "sh": 90, "ss": 45, "ssh": 12},
        "total_maps": 15392, "maximum_combo": 3421, "replays_watched": 234,
        "title": None, "title_color": None,
        "top_scores": [], "rank_history": list(range(15000, 15400, 5)),
    }
    if lang is not None:
        d["lang"] = lang
    d.update(overrides)
    return d


def _render(data):
    return CardRenderer().generate_profile_dashboard(data, None, None, []).getvalue()


def test_renders_default_lang_when_missing():
    data = _data()
    assert "lang" not in data
    png = _render(data)
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_renders_russian():
    png = _render(_data(lang="ru", title="Стахановец", title_color=(229, 57, 53)))
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_renders_online_and_hidden_last_seen_states():
    png = _render(_data(lang="ru", is_online=True))
    assert png.startswith(b"\x89PNG")
    png2 = _render(_data(lang="ru", is_online=False, last_visit=None))
    assert png2.startswith(b"\x89PNG")


def test_renders_with_no_rank_history():
    # Fewer than 2 points -> "Not enough data" / "Недостаточно данных" path.
    png = _render(_data(lang="ru", rank_history=[]))
    assert png.startswith(b"\x89PNG")


def test_fmt_last_seen_hidden_translates():
    assert _fmt_last_seen(None, "en") == "Hidden"
    assert _fmt_last_seen(None, "ru") == "Скрыто"


def test_fmt_last_seen_relative_time_translates():
    import datetime
    recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
    en = _fmt_last_seen(recent, "en")
    ru = _fmt_last_seen(recent, "ru")
    assert en.endswith("ago")
    assert "назад" in ru


def test_stats_strip_labels_fit_their_columns():
    # 2026-07-02b regression: "Производительность"/"Дата регистрации" overflowed
    # their fixed-width columns — performance/join_date/last_seen were shortened.
    # Guard the actual budgets so a future re-translation can't silently reintroduce it.
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    semi_path = _find_font(TORUS_SEMI) or _find_font(TORUS_BOLD)
    font = ImageFont.truetype(semi_path, 16)

    def w(text):
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0]

    ru = _PF_STRINGS["ru"]
    # Performance/Accuracy/Play Count columns are ~214px apart.
    assert w(ru["performance"]) < 214
    assert w(ru["accuracy"]) < 210
    assert w(ru["play_count"]) < 204
    # Join Date / Last Seen sit right-aligned with ~246px before the card edge
    # (jx=990 — widened for "Зарегистрирован").
    assert w(ru["join_date"]) < 246
    assert w(ru["last_seen"]) < 246
    assert w(ru["last_seen"]) < 186

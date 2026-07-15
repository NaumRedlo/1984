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


def test_join_date_label_does_not_overflow_the_panel():
    # 2026-07-02c: "Зарегистрирован" sits right at the panel's right edge (jx
    # was nudged right a few times) — a raw font.textbbox estimate says it's a
    # few px over budget, but the actual multi-font render doesn't clip. Check
    # ground truth (rendered pixels), not an approximate width budget.
    from services.image.render.profile import INNER_R, STATS_Y0

    png = _render(_data(lang="ru"))
    img = Image.open(__import__("io").BytesIO(png)).convert("RGB")
    px = img.load()
    bg = (30, 24, 30)  # COL_PANEL

    def diff(c):
        return abs(c[0] - bg[0]) + abs(c[1] - bg[1]) + abs(c[2] - bg[2])

    rightmost = None
    for y in range(STATS_Y0 + 12, STATS_Y0 + 30):
        for x in range(INNER_R + 10, INNER_R - 110, -1):
            if diff(px[x, y]) > 40:
                rightmost = max(rightmost or 0, x)
                break
    assert rightmost is not None, "join_date label not found where expected"
    assert rightmost <= INNER_R, f"join_date label overflows the panel (x={rightmost} > {INNER_R})"


def test_renders_with_a_cover_banner():
    """2026-07-15: the hero's cover-art banner now goes through the shared
    _cover_bleed helper (bled across the whole hero, fully rounded on all 4
    corners) instead of its own top-only-rounded fade implementation."""
    cover = Image.new("RGB", (1280, 250), (80, 120, 200))
    png = CardRenderer().generate_profile_dashboard(_data(), None, cover, []).getvalue()
    assert png.startswith(b"\x89PNG")


def test_cover_banner_bottom_corners_are_rounded():
    """The hero banner's bottom-left corner must be visibly rounded — the
    cover's own colour (bright, saturated blue here) should be clipped away
    right at the corner but present a bit further in along the bottom edge.
    Compares the two points to each other (cover-tinted vs not) rather than
    to a hardcoded background constant, since the exact panel shading right
    at the corner pixel is an implementation detail."""
    from services.image.render.profile import CARD_M, HERO_BOTTOM

    cover_rgb = (80, 120, 200)
    cover = Image.new("RGB", (1280, 250), cover_rgb)
    png = CardRenderer().generate_profile_dashboard(_data(), None, cover, []).getvalue()
    img = Image.open(__import__("io").BytesIO(png)).convert("RGB")
    px = img.load()

    def cover_likeness(c):
        return -(abs(c[0] - cover_rgb[0]) + abs(c[1] - cover_rgb[1]) + abs(c[2] - cover_rgb[2]))

    corner_y = HERO_BOTTOM - 1
    at_corner = cover_likeness(px[CARD_M + 1, corner_y])
    inset = cover_likeness(px[CARD_M + 20, corner_y])
    assert inset > at_corner  # 20px in along the bottom edge reads closer to the cover's colour


# ── 2026-07-15: top-play poster tiles (~105px wide) ──────────────────────
# X/XH used to display as the 2-char "SS", which at the single-letter grade
# font's natural spacing ran wide enough to collide with the pp/accuracy
# text on the right of the same narrow tile. First fix was a smaller font +
# hand-kerned pair of "S" glyphs; ultimately simplified to just displaying
# osu!'s own single-character "X" code instead (same treatment S already
# gets for S/SH — colour alone marks gold vs silver) since that sidesteps
# the width problem entirely rather than working around it.

def _data_with_top_scores(scores):
    return _data(top_scores=scores)


def test_renders_with_x_and_single_letter_grades():
    scores = [
        {"rank": "X", "pp": 412, "accuracy": 100.0},
        {"rank": "XH", "pp": 389, "accuracy": 99.87},
        {"rank": "S", "pp": 350, "accuracy": 98.2},
        {"rank": "SH", "pp": 300, "accuracy": 97.65},
        {"rank": "A", "pp": 280, "accuracy": 96.1},
    ]
    png = CardRenderer().generate_profile_dashboard(_data_with_top_scores(scores), None, None, []).getvalue()
    assert png.startswith(b"\x89PNG")


def test_top_grade_displays_as_single_letter_x():
    from services.image.render.profile import _grade_letter
    assert _grade_letter("X") == "X"
    assert _grade_letter("XH") == "X"
    assert _grade_letter("S") == "S"
    assert _grade_letter("SH") == "S"


def test_x_grade_hides_accuracy_but_keeps_pp():
    """Accuracy is redundant for the top grade (X/XH) — always ~100% —
    so it's dropped for that tile only; pp stays for every grade. pp/
    accuracy are centre-aligned (in the space next to the grade letter),
    not right-aligned, so spy on _text_center here."""
    from services.image.core import CardRenderer as CR
    renderer = CR()
    center_calls = []
    original = renderer._text_center

    def spy(draw, cx, y, text, font, fill, **kwargs):
        center_calls.append(text)
        return original(draw, cx, y, text, font, fill, **kwargs)

    renderer._text_center = spy
    scores = [
        {"rank": "X", "pp": 412, "accuracy": 100.0},
        {"rank": "S", "pp": 350, "accuracy": 98.2},
    ]
    renderer.generate_profile_dashboard(_data_with_top_scores(scores), None, None, [])

    assert "412pp" in center_calls
    assert "100.00%" not in center_calls   # X tile: accuracy suppressed
    assert "350pp" in center_calls
    assert "98.20%" in center_calls        # non-X tile: accuracy still shown

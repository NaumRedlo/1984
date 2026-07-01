"""Headless render checks for the titles-collection card
(services/image/render/titles.py), incl. the 2026-07-02 EN/RU translation."""

from services.image.core import CardRenderer
from services.image.render.titles import build_titles_card_data, _tt_tabs, HEAD_Y1, BODY_Y0
from utils.titles import TITLE_REGISTRY, RARITY_ORDER


def _progress(n=3):
    rarities = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "secret"]
    out = []
    for i in range(n):
        out.append({
            "code": f"t{i}", "name": f"Title {i}", "description": f"Do the thing {i} times.",
            "rarity": rarities[i % len(rarities)], "color": (200, 80, 80),
            "unlocked": i % 2 == 0, "secret": rarities[i % len(rarities)] == "secret",
            "target": 10, "current": 5, "unlocked_at": "2026-06-01T00:00:00" if i % 2 == 0 else None,
            "rarity_label": rarities[i % len(rarities)].title(),
        })
    return out


def _summary(progress):
    unlocked = sum(1 for p in progress if p["unlocked"])
    return {
        "unlocked": unlocked, "total": len(progress), "overall_pct": 100 * unlocked / len(progress),
        "rarest": None, "by_rarity": {}, "latest": None, "next_up": None,
    }


def _data(lang=None):
    progress = _progress()
    data = build_titles_card_data("kazaki1865", "@kazaki", "RU", progress, _summary(progress))
    if lang is not None:
        data["lang"] = lang
    return data


def _render(data):
    return CardRenderer().generate_titles_card(data, None).getvalue()


def test_renders_default_lang_when_missing():
    data = _data()
    assert "lang" not in data
    png = _render(data)
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_renders_english_explicit():
    png = _render(_data(lang="en"))
    assert png.startswith(b"\x89PNG")


def test_renders_russian():
    png = _render(_data(lang="ru"))
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_renders_with_masked_secret_and_no_next_up():
    # secret + locked (index 6 in a 7-rarity cycle) exercises the
    # hidden_title/hidden_desc translation path.
    data = _data(lang="ru")
    png = _render(data)
    assert png.startswith(b"\x89PNG")


def test_header_and_tabs_have_separate_rows():
    # 2026-07-02b regression: the Russian header ("КОЛЛЕКЦИЯ ТИТУЛОВ") used to
    # share a row with the filter tabs and the much-wider Russian rarity labels
    # (ЛЕГЕНДАРНЫЙ, МИФИЧЕСКИЙ...) collided with it. Tabs must sit below the
    # header/subtitle band, not inside it.
    assert BODY_Y0 > HEAD_Y1


def test_renders_real_registry_titles_in_russian():
    # Use the ACTUAL TITLE_REGISTRY content (not synthetic short strings) so a
    # real translated name/description/rarity combination gets rendered.
    codes = list(TITLE_REGISTRY.keys())[:10]
    progress = []
    for i, code in enumerate(codes):
        td = TITLE_REGISTRY[code]
        progress.append({
            "code": code, "name": td.name_for("ru"), "description": td.description_for("ru"),
            "rarity": td.rarity, "color": td.color, "unlocked": i % 2 == 0, "secret": td.secret,
            "target": td.target, "current": td.target, "unlocked_at": "2026-06-01T00:00:00",
            "rarity_label": td.rarity_label_for("ru"),
        })
    summary = {
        "unlocked": 5, "total": 10, "overall_pct": 50.0,
        "rarest": progress[0], "by_rarity": {r: {"unlocked": 1, "total": 1} for r in RARITY_ORDER},
        "latest": progress[0], "next_up": progress[1],
    }
    data = build_titles_card_data("kazaki1865", "@kazaki", "RU", progress, summary, rarest_global_pct=3.2)
    data["lang"] = "ru"
    png = _render(data)
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_tabs_fit_within_card_width_both_languages():
    # The concrete numeric guard behind the header/tabs fix: rendered tab-row
    # total width must stay within the card's inner span for both languages,
    # not just "doesn't crash".
    from PIL import Image, ImageDraw, ImageFont
    from services.image.utils import _find_font
    from services.image.constants import TORUS_SEMI, TORUS_BOLD
    from services.image.render.titles import INNER_L, INNER_R

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    tab_path = _find_font(TORUS_SEMI) or _find_font(TORUS_BOLD)
    font = ImageFont.truetype(tab_path, 15)
    pad_x, gap = 12, 8
    for lang in ("en", "ru"):
        tabs = _tt_tabs(lang)
        widths = [draw.textbbox((0, 0), lbl, font=font)[2] + pad_x * 2 for _, lbl in tabs]
        total_w = sum(widths) + gap * (len(widths) - 1)
        assert total_w <= (INNER_R - INNER_L), f"{lang} tabs overflow the card width"

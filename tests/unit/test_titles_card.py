from services.image.core import CardRenderer
from services.image.render.titles import build_titles_card_data, _tt_tabs, HEAD_Y1, BODY_Y0
from utils.titles import TITLE_REGISTRY, RARITY_ORDER

def _progress(n=3):
    rarities = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "anomaly"]
    out = []
    for i in range(n):
        out.append({
            "code": f"t{i}", "name": f"Title {i}", "description": f"Do the thing {i} times.",
            "rarity": rarities[i % len(rarities)], "color": (200, 80, 80),
            "unlocked": i % 2 == 0,
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

def test_renders_without_a_next_title():

    data = _data(lang="ru")
    png = _render(data)
    assert png.startswith(b"\x89PNG")

def test_header_and_tabs_have_separate_rows():

    assert BODY_Y0 > HEAD_Y1

def test_renders_real_registry_titles_in_russian():

    codes = list(TITLE_REGISTRY.keys())[:10]
    progress = []
    for i, code in enumerate(codes):
        td = TITLE_REGISTRY[code]
        progress.append({
            "code": code, "name": td.name_for("ru"), "description": td.description_for("ru"),
            "rarity": td.rarity, "color": td.color, "unlocked": i % 2 == 0,
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

    from PIL import Image, ImageDraw, ImageFont
    from services.image.utils import _find_font
    from services.image.constants import SANS_SEMI, SANS_BOLD
    from services.image.render.titles import INNER_L, INNER_R

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    tab_path = _find_font(SANS_SEMI) or _find_font(SANS_BOLD)
    font = ImageFont.truetype(tab_path, 15)
    pad_x, gap = 12, 8
    for lang in ("en", "ru"):
        tabs = _tt_tabs(lang)
        widths = [draw.textbbox((0, 0), lbl, font=font)[2] + pad_x * 2 for _, lbl in tabs]
        total_w = sum(widths) + gap * (len(widths) - 1)
        assert total_w <= (INNER_R - INNER_L), f"{lang} tabs overflow the card width"

def test_a_long_title_name_in_the_bottom_bar_stays_in_its_own_column():
    from PIL import Image
    from io import BytesIO
    from services.image.render.titles import BOTTOM_Y0, BOTTOM_Y1, INNER_L

    def bar(name, latest_name):
        td = TITLE_REGISTRY["mod_passport"]
        item = {"code": td.code, "name": name, "description": td.description, "rarity": td.rarity, "color": td.color, "unlocked": False,
                "target": 5, "current": 4, "progress_pct": 80.0, "unlocked_at": None, "rarity_label": td.rarity_label}
        done = {**item, "name": latest_name, "unlocked": True, "unlocked_at": "2026-06-01T00:00:00"}
        summary = {"unlocked": 1, "total": 2, "overall_pct": 50.0, "rarest": done, "by_rarity": {r: {"unlocked": 0, "total": 0} for r in RARITY_ORDER}, "latest": done, "next_up": item}
        data = build_titles_card_data("kazaki1865", "@kazaki", "RU", [done, item], summary)
        data["lang"] = "ru"
        return Image.open(BytesIO(_render(data))).convert("RGB")

    short = bar("Паспорт", "Идеал")
    long = bar("Модифицированный паспорт и ещё множество слов подряд для проверки", "Очень-очень длинное название титула, которое не помещается")
    first, second = INNER_L + 360, INNER_L + 720
    for left, right, below in ((first - 10, first + 22, 10), (second - 10, second + 22, 10), (second + 22, second + 340, 10)):
        box = (left, BOTTOM_Y0 + 30, right, BOTTOM_Y1 - below)
        assert list(short.crop(box).getdata()) == list(long.crop(box).getdata()), "a name has crossed into the next column"

def test_a_dimmed_star_pill_has_its_star_and_its_number_in_one_colour():
    from PIL import Image

    renderer = CardRenderer()
    fonts = renderer._tt_fonts()
    for token in ("2*", "5*+", "7*"):
        img = Image.new("RGB", (160, 60), (28, 24, 29))
        end = renderer._tt_sr_pill(img, 10, 30, token, fonts, dim=True)
        pill = img.crop((10, 18, end, 42))
        width = pill.width
        light = lambda box: sum(1 for px in pill.crop(box).getdata() if sum(px) / 3 > 215)
        assert light((0, 0, int(width * 0.45), 24)) >= 8, f"{token}: the star is not light"
        assert light((int(width * 0.5), 0, width, 24)) >= 8, f"{token}: the number is not as light as the star"

def test_the_tier_pill_of_a_row_stops_short_of_the_progress_beside_it():
    from PIL import Image, ImageDraw
    from services.image.render.titles import BADGE_GAP, _tt_lang

    renderer = CardRenderer()
    fonts = renderer._tt_fonts()
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    right = 1000
    for lang in ("ru", "en"):
        S = _tt_lang({"lang": lang})
        for label in ("МИФИЧЕСКИЙ", "АНОМАЛЬНЫЙ", "MYTHIC", "ANOMALY"):
            for unlocked, current, target in ((False, 32631, 150000), (False, 4, 5), (False, 0, 1), (True, 0, 1)):
                t = {"unlocked": unlocked, "target": target, "current": current, "unlocked_at": "2026-06-01T00:00:00"}
                lines = renderer._tt_status_lines(t, S, 100)
                left, edge, status_left = renderer._tt_badge_span(draw, label, lines, fonts, right)
                assert edge + BADGE_GAP <= status_left, (label, current, target)
                assert left < edge < right
    wide = renderer._tt_status_lines({"unlocked": False, "target": 150000, "current": 32631}, _tt_lang({"lang": "ru"}), 100)
    dated = renderer._tt_status_lines({"unlocked": True, "target": 1, "current": 1, "unlocked_at": "2026-06-01T00:00:00"}, _tt_lang({"lang": "ru"}), 100)
    assert renderer._tt_badge_span(draw, "МИФИЧЕСКИЙ", wide, fonts, right)[1] < renderer._tt_badge_span(draw, "МИФИЧЕСКИЙ", dated, fonts, right)[1], "a wider progress pushes the pill further left"

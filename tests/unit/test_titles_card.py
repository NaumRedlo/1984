"""Headless render checks for the titles-collection card
(services/image/render/titles.py), incl. the 2026-07-02 EN/RU translation."""

from services.image.core import CardRenderer
from services.image.render.titles import build_titles_card_data


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

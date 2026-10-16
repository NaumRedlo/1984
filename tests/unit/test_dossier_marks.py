import asyncio

from services.image.utils import load_badge
from services.render_farm import billing


def test_every_tier_and_stage_has_its_picture():
    for rank in range(5):
        for stage in range(1, 5):
            picture = load_badge(rank, stage, 40)
            assert picture is not None and picture.size == (40, 40)
            assert picture.getbbox() is not None


def test_the_creator_wears_the_purple_last_stage_and_nobody_else_is_given_one():
    assert asyncio.run(billing.worn_mark(None, billing.CREATORS[0])) == {"rank": 2, "stage": 4}
    assert asyncio.run(billing.worn_mark_of_osu(billing.CREATORS[0])) == {"rank": 2, "stage": 4}
    assert asyncio.run(billing.worn_mark(None, 123)) is None
    assert asyncio.run(billing.worn_mark_of_osu(0)) is None


def test_a_profile_shows_the_mark_and_as_many_hearts_as_the_support_level():
    import sys
    sys.path.insert(0, "tests/unit")
    from io import BytesIO

    from PIL import Image, ImageChops
    from services.image.core import CardRenderer
    from test_profile_card import _data

    def picture(**extra):
        return Image.open(BytesIO(CardRenderer().generate_profile_dashboard(_data(**extra), None, None, []).getvalue())).convert("RGB")

    plain = picture(is_supporter=True, support_level=1)
    marked = picture(is_supporter=True, support_level=1, dossier_mark={"rank": 2, "stage": 4})
    assert ImageChops.difference(plain, marked).getbbox() is not None
    one, three = picture(is_supporter=True, support_level=1), picture(is_supporter=True, support_level=3)
    left, top, right, bottom = ImageChops.difference(one, three).getbbox()
    assert bottom < 200


def test_cards_take_the_colour_of_the_tier_and_keep_neutral_and_other_colours():
    from services.image import tint

    red, grey, green = (226, 72, 72), (140, 140, 160), (120, 220, 130)
    assert tint.tone(red) == red
    tint.wear({"rank": 1, "stage": 1})
    try:
        blue = tint.tone(red)
        assert blue[2] > blue[0], "the accent turns blue"
        assert tint.tone(grey) == grey and tint.tone(green) == green
        assert tint.tone(red + (55,))[3] == 55
        flat = tint.tones(red, red)
        assert flat[0] == flat[1], "the first stage is one flat colour"
        tint.wear({"rank": 1, "stage": 4})
        start, end = tint.tones(red, red)
        assert start != end, "the last stage shimmers along a bar"
    finally:
        tint.wear(None)
    assert tint.tone(red) == red


def test_the_creator_cards_are_purple_at_the_last_stage():
    assert asyncio.run(billing.palette_of(None, billing.CREATORS[0])) == {"rank": 2, "stage": 4}
    assert asyncio.run(billing.palette_of(None, 123)) is None


def test_a_title_takes_the_subscription_colour_only_when_asked():
    from services.image import tint

    assert tint.title_colour(None) is None
    purple = tint.title_colour({"rank": 2, "stage": 1})
    assert purple[2] > purple[1], "a purple tier makes a purple title"
    assert tint.worn() is None, "naming the colour leaves no palette worn"
    assert asyncio.run(billing.palette_of(None, billing.CREATORS[0], "tint_titles")) == {"rank": 2, "stage": 4}

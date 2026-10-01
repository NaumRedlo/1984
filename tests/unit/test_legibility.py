from PIL import Image, ImageDraw

from services.image import legibility
from services.image.constants import RECENT_PANEL, TEXT_SECONDARY
from services.image.core import CardRenderer

def test_quiet_text_keeps_its_colour_where_it_already_reads():
    under = legibility.luminance(RECENT_PANEL)
    assert legibility.contrast(legibility.luminance(TEXT_SECONDARY), under) >= legibility.FLOOR
    assert legibility.readable(TEXT_SECONDARY, under) == TEXT_SECONDARY
    assert legibility.readable(TEXT_SECONDARY, None) == TEXT_SECONDARY

def test_quiet_text_is_lifted_until_it_reads_on_a_lighter_ground():
    for ground in ((50, 50, 60), (70, 62, 66), (40, 70, 110)):
        under = legibility.luminance(ground)
        lifted = legibility.readable(TEXT_SECONDARY, under)
        assert lifted != TEXT_SECONDARY
        assert legibility.contrast(legibility.luminance(lifted), under) >= legibility.FLOOR, ground
    kept = legibility.readable(TEXT_SECONDARY + (200,), legibility.luminance((70, 62, 66)))
    assert len(kept) == 4 and kept[3] == 200

def test_a_colour_that_means_something_is_never_changed():
    under = legibility.luminance((90, 90, 90))
    for colour in ((228, 72, 72), (255, 204, 64), (120, 200, 140)):
        assert not legibility.is_quiet(colour)
        assert legibility.readable(colour, under) == colour

def test_a_bright_cover_is_brought_under_the_ceiling_and_a_dark_one_is_left_alone():
    bright = Image.new("RGBA", (40, 20), (250, 248, 240, 255))
    ImageDraw.Draw(bright).rectangle((0, 0, 19, 19), fill=(255, 60, 60, 255))
    calmed = legibility.calm(bright)
    assert calmed.mode == "RGBA" and calmed.getpixel((30, 10))[3] == 255
    assert max(legibility.luminance(pixel) for pixel in calmed.convert("RGB").get_flattened_data()) <= legibility.COVER_CEILING * 1.15
    red = calmed.getpixel((5, 5))
    assert red[0] > red[1] * 2, "the hue is kept"
    dark = Image.new("RGB", (40, 20), (38, 30, 44))
    assert legibility.calm(dark).tobytes() == dark.tobytes()

def test_on_a_calmed_cover_quiet_text_reads_and_stays_under_the_main_text():
    under = legibility.COVER_CEILING
    lifted = legibility.readable(TEXT_SECONDARY, under)
    assert legibility.contrast(legibility.luminance(lifted), under) >= legibility.FLOOR
    assert legibility.luminance(lifted) < legibility.luminance(legibility.LIGHTEST)

def test_the_renderer_measures_what_a_label_lies_on():
    renderer = CardRenderer()
    seen = {}
    for name, ground in (("panel", RECENT_PANEL), ("cover", (84, 80, 86))):
        img = Image.new("RGB", (240, 60), ground)
        draw = ImageDraw.Draw(img)
        renderer._draw_text(draw, (12, 16), "mapped by", renderer.font_label, TEXT_SECONDARY)
        seen[name] = max(img.get_flattened_data(), key=legibility.luminance)
    assert seen["panel"] == TEXT_SECONDARY
    assert legibility.luminance(seen["cover"]) > legibility.luminance(TEXT_SECONDARY)
    assert legibility.contrast(legibility.luminance(seen["cover"]), legibility.luminance((84, 80, 86))) >= legibility.FLOOR

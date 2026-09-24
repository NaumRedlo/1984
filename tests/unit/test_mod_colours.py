from PIL import Image

from services.image.constants import MOD_ACRONYMS, MOD_COLORS, MOD_INK, MOD_TYPE_COLORS
from services.image.core import CardRenderer
from services.image.utils import mod_ink

def test_mods_take_the_colour_of_their_kind_as_in_the_game():
    assert MOD_COLORS["HR"] == MOD_COLORS["DT"] == MOD_TYPE_COLORS["increase"] == (255, 102, 102)
    assert MOD_COLORS["EZ"] == MOD_COLORS["NF"] == MOD_TYPE_COLORS["reduction"]
    assert MOD_COLORS["CL"] == MOD_COLORS["MR"] == MOD_TYPE_COLORS["conversion"]
    assert MOD_COLORS["RX"] == MOD_COLORS["AP"] == MOD_TYPE_COLORS["automation"]
    assert MOD_COLORS["WU"] == MOD_TYPE_COLORS["fun"]
    assert MOD_COLORS["TD"] == MOD_COLORS["SV2"] == MOD_TYPE_COLORS["system"]

def test_every_mod_the_cards_know_has_a_colour():
    assert not (MOD_ACRONYMS - {"NM"}) - set(MOD_COLORS)

def test_the_glyph_is_dark_on_every_kind():
    assert all(mod_ink(colour) == MOD_INK for colour in MOD_TYPE_COLORS.values())
    assert mod_ink((40, 40, 60)) == (255, 255, 255)

def test_a_mapper_avatar_is_a_ringed_circle_with_or_without_a_picture():
    renderer = CardRenderer()
    for avatar in (Image.new("RGB", (64, 64), (0, 0, 255)), None):
        img = Image.new("RGB", (80, 80), (0, 0, 0))
        renderer._paste_ringed_avatar(img, avatar, 26, 26, 28)
        assert img.getpixel((2, 2)) == (0, 0, 0)
        red, green, _ = img.getpixel((26 + 14, 25))
        assert red > 150 and green < 120, "no red ring at the top edge"

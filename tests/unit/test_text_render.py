"""Multi-font fallback dispatch (services/image/text_render.py), including the
2026-07-02 Cyrillic-specific fallback tier: Cyrillic-block glyphs the primary
doesn't cover prefer cyrillic_fallback over the general fallback."""

from PIL import Image, ImageDraw, ImageFont

from services.image import text_render as tr
from services.image.constants import TORUS_BOLD, MPLUS_BOLD, PROXIMA_BOLD
from services.image.utils import _find_font


def _draw():
    img = Image.new("RGB", (400, 100), (0, 0, 0))
    return ImageDraw.Draw(img)


def _fonts():
    torus = ImageFont.truetype(_find_font(TORUS_BOLD), 24)
    mplus = ImageFont.truetype(_find_font(MPLUS_BOLD), 24)
    proxima = ImageFont.truetype(_find_font(PROXIMA_BOLD), 24)
    return torus, mplus, proxima


def test_cyrillic_range_covers_russian_alphabet():
    alphabet = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    assert all(ord(c) in tr._CYRILLIC_RANGE for c in alphabet)


def test_ascii_uses_primary_regardless_of_fallbacks():
    torus, mplus, proxima = _fonts()
    for ch in "Hello 123":
        f = tr._pick_font(ch, torus, mplus, proxima)
        assert f is torus


def test_cyrillic_prefers_cyrillic_fallback_over_general():
    torus, mplus, proxima = _fonts()
    f = tr._pick_font("Ж", torus, mplus, proxima)
    assert f is proxima


def test_cyrillic_falls_back_to_general_when_no_cyrillic_fallback_given():
    torus, mplus, proxima = _fonts()
    f = tr._pick_font("Ж", torus, mplus, None)
    assert f is mplus


def test_non_cyrillic_unsupported_char_uses_general_fallback():
    # A CJK character: Torus doesn't cover it, and it's outside the Cyrillic
    # block, so it must go to the general fallback even with a Cyrillic one set.
    torus, mplus, proxima = _fonts()
    f = tr._pick_font("あ", torus, mplus, proxima)
    assert f is mplus


def test_draw_text_multifont_accepts_cyrillic_fallback():
    torus, mplus, proxima = _fonts()
    draw = _draw()
    # Must not raise, and must advance x past the string start.
    end_x = tr.draw_text_multifont(
        draw, (5, 5), "Точность", torus, mplus,
        (255, 255, 255), cyrillic_fallback=proxima,
    )
    assert end_x > 5


def test_text_size_multifont_accepts_cyrillic_fallback():
    torus, mplus, proxima = _fonts()
    draw = _draw()
    w, h = tr.text_size_multifont(draw, "Точность", torus, mplus, cyrillic_fallback=proxima)
    assert w > 0 and h > 0


def test_degrades_gracefully_with_no_fallbacks_at_all():
    torus, _, _ = _fonts()
    f = tr._pick_font("Ж", torus, None, None)
    assert f is torus  # tofu with primary, not a crash

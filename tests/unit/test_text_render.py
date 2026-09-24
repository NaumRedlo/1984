from PIL import Image, ImageDraw, ImageFont

from services.image import text_render as tr
from services.image.constants import MONO_BOLD, MPLUS_BOLD, SANS_BOLD
from services.image.utils import _find_font

def _draw():
    img = Image.new("RGB", (400, 100), (0, 0, 0))
    return ImageDraw.Draw(img)

def _fonts():
    sans = ImageFont.truetype(_find_font(SANS_BOLD), 24)
    mplus = ImageFont.truetype(_find_font(MPLUS_BOLD), 24)
    mono = ImageFont.truetype(_find_font(MONO_BOLD), 24)
    return sans, mplus, mono

def test_cyrillic_range_covers_russian_alphabet():
    alphabet = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    assert all(ord(c) in tr._CYRILLIC_RANGE for c in alphabet)

def test_the_bot_s_own_fonts_write_latin_and_russian_themselves():
    sans, mplus, mono = _fonts()
    for primary in (sans, mono):
        for ch in "Hello 123 Точность ё":
            assert tr._pick_font(ch, primary, mplus, None) is primary

def test_japanese_goes_to_m_plus():
    sans, mplus, mono = _fonts()
    assert tr._pick_font("あ", sans, mplus, None) is mplus
    assert tr._pick_font("漢", mono, mplus, None) is mplus

def test_a_primary_without_cyrillic_prefers_the_cyrillic_fallback(monkeypatch):
    sans, mplus, mono = _fonts()
    monkeypatch.setattr(tr, "_covers", lambda font, ch: font is not mono or ch.isascii())
    assert tr._pick_font("Ж", mono, mplus, sans) is sans
    assert tr._pick_font("Ж", mono, mplus, None) is mplus
    assert tr._pick_font("Ж", mono, None, None) is mono

def test_draw_and_measure_russian_text():
    sans, mplus, _ = _fonts()
    draw = _draw()
    assert tr.draw_text_multifont(draw, (5, 5), "Точность", sans, mplus, (255, 255, 255)) > 5
    w, h = tr.text_size_multifont(draw, "Точность", sans, mplus)
    assert w > 0 and h > 0

def test_no_torus_or_proxima_is_left():
    import os

    from services.image.constants import FONT_DIR

    left = [name for name in os.listdir(FONT_DIR) if name.startswith(("Torus", "Proxima"))]
    assert left == []

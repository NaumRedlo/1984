import types as pytypes

import pytest

from bot.handlers.common import help as help_menu
from utils.i18n import t

@pytest.fixture
def testers(monkeypatch):
    from config import settings

    def _set(ids):
        monkeypatch.setattr(settings, "RENDER_OPEN_TO_ALL", False)
        monkeypatch.setattr(settings, "RENDER_TESTER_IDS", ids)

    return _set

def test_the_dossier_category_is_gone_for_everybody(testers, monkeypatch):
    from config import settings

    testers([42])
    monkeypatch.setattr(settings, "RENDER_OPEN_TO_ALL", True)
    assert "dossier" not in help_menu._sections(42)
    assert "dossier" not in help_menu._sections(43)

def test_everyone_still_gets_the_ordinary_categories(testers):
    testers([])
    assert help_menu._sections(43) == ("osu", "account")
    assert help_menu._sections(None) == ("osu", "account")

def test_every_category_the_menu_can_offer_has_its_text(testers):
    testers([42])
    for code in help_menu._sections(42):
        for part in ("label", "body"):
            key = f"help.sec.{code}.{part}"
            for lang in ("en", "ru"):
                assert t(key, lang) != key, f"{key} is missing in {lang}"

class _Message:
    def __init__(self):
        self.shown = []

    async def edit_text(self, text, **_):
        self.shown.append(text)

class _Callback:
    def __init__(self, data, user_id):
        self.data = data
        self.from_user = pytypes.SimpleNamespace(id=user_id)
        self.message = _Message()

    async def answer(self, *_a, **_kw):
        pass

@pytest.fixture
def speaks_russian(monkeypatch):
    async def _lang(_id):
        return "ru"

    monkeypatch.setattr(help_menu, "get_language", _lang)

@pytest.mark.asyncio
async def test_a_typed_callback_does_not_open_a_category_you_do_not_have(
    testers, speaks_russian
):
    testers([])
    callback = _Callback("help_dossier", 43)
    await help_menu.process_help_callback(callback)
    assert callback.message.shown == [], "the gate answered the button, not the id"

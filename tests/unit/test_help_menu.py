"""The help menu, and the one category that is not for everybody.

Dossier sits behind the render gate, so the category describing it does too. A
button nobody outside the gate is shown is only half of that: the callback data
is a string in a message, and `help_dossier` can be typed by hand.
"""

import types as pytypes

import pytest

# Imported first on purpose. Reaching `bot.handlers.common` before this walks
# into a cycle older than this file: its `__init__` pulls in auth, auth reaches
# services.oauth, which reaches the miniapp, which comes back round to
# bot.handlers.profile, which asks for the auth module it is standing inside.
# Importing profile first settles the order. Not this file's bug to fix, but it
# is this file's bug to survive — the suite only gets away with it today
# because some earlier test happens to import profile first.
import bot.handlers.profile  # noqa: F401
from bot.handlers.common import help as help_menu
from utils.i18n import t


@pytest.fixture
def testers(monkeypatch):
    """Who is through the gate, set where the gate reads it."""
    from config import settings

    def _set(ids):
        monkeypatch.setattr(settings, "RENDER_OPEN_TO_ALL", False)
        monkeypatch.setattr(settings, "RENDER_TESTER_IDS", ids)

    return _set


def test_the_dossier_category_is_only_for_whoever_can_render(testers):
    testers([42])
    assert "dossier" in help_menu._sections(42)
    assert "dossier" not in help_menu._sections(43)


def test_everyone_still_gets_the_ordinary_categories(testers):
    testers([])
    assert help_menu._sections(43) == ("osu", "account")
    assert help_menu._sections(None) == ("osu", "account")


def test_opening_it_wide_gives_it_to_everybody(testers, monkeypatch):
    from config import settings

    testers([])
    monkeypatch.setattr(settings, "RENDER_OPEN_TO_ALL", True)
    assert "dossier" in help_menu._sections(43)


def test_every_category_the_menu_can_offer_has_its_text(testers):
    """`t()` prints the key it was handed when the catalogue has nothing, which
    is the right thing to do and is silent. A category added without text would
    otherwise be a button that opens its own name."""
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


@pytest.mark.asyncio
async def test_somebody_through_the_gate_gets_the_body(testers, speaks_russian):
    testers([42])
    callback = _Callback("help_dossier", 42)
    await help_menu.process_help_callback(callback)
    assert callback.message.shown == [t("help.sec.dossier.body", "ru")]

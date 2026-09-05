import inspect
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services.render_farm import http as farm

def _asked_for() -> set[str]:
    from dossier import worker

    source = inspect.getsource(worker)

    found = re.findall(r'\{(?:self\.)?base\}(/render/[^"\']*)', source)

    return {re.sub(r"\{[^}]*\}", "{}", path) for path in found}

def _served() -> set[str]:
    return {re.sub(r"\{[^}]*\}", "{}", route.path)
            for route in farm.make_routes()}

def test_every_address_the_client_asks_for_is_one_this_bot_serves():
    missing = _asked_for() - _served()
    assert not missing, (
        f"the render client asks for {', '.join(sorted(missing))} and this bot "
        f"does not serve it — the two repositories have drifted, and the way "
        f"that shows up in the wild is a worker that claims nothing and says "
        f"nothing is wrong"
    )

def test_the_client_is_actually_being_read():
    asked = _asked_for()
    assert len(asked) >= 5, asked
    assert "/render/claim" in asked, asked

def test_the_check_asks_the_bot_without_taking_anybodys_replay():
    assert "/render/hello" in _served()

def test_the_bot_logs_the_same_fingerprint():
    from bot import main

    source = inspect.getsource(main)
    assert "Render worker token: %d chars, %s" in source

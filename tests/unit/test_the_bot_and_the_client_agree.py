"""The conversation between the bot and a render client, from the bot's end.

The client is a separate repository now — `NaumRedlo/Dossier`, installed as
`dossier` — and its own tests went with it. These stayed because they are about
the seam rather than about either side: a worker asks for addresses this
codebase has to serve, and a mismatch is not a failing test on anybody's
machine. It is a worker that starts, says nothing is wrong, and never renders.

Two repositories that talk to each other need one of them to hold the
conversation as a fact. This is that.
"""

import inspect
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services.render_farm import http as farm  # noqa: E402


def _asked_for() -> set[str]:
    """Every `/render/...` address the client builds, read off its source.

    Read rather than called: reaching them for real would need a server, and
    the question here is which addresses exist in the client at all — including
    the ones only a failing render ever asks for.
    """
    from dossier import worker

    source = inspect.getsource(worker)
    # `f"{self.base}/render/job/{job_id}/replay"` and friends. The leading
    # placeholder is the server; everything after it is the address.
    found = re.findall(r'\{(?:self\.)?base\}(/render/[^"\']*)', source)
    # `{job_id}` here against `{job_id}` there is a coincidence of naming, and
    # a rename on either side would break this test rather than the code. So
    # both sides are flattened to the same shape.
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
    """The guard above passes trivially if the regex stops matching — an empty
    set is a subset of everything."""
    asked = _asked_for()
    assert len(asked) >= 5, asked
    assert "/render/claim" in asked, asked


def test_the_check_asks_the_bot_without_taking_anybodys_replay():
    """`--check` reaches a real server. The endpoint it uses has to answer the
    same two questions `claim` does — is this token good, do the builds agree —
    and must not hand back a job while doing it."""
    assert "/render/hello" in _served()


def test_the_bot_logs_the_same_fingerprint():
    """Both sides or neither: a fingerprint one side prints is not a
    comparison."""
    from bot import main

    source = inspect.getsource(main)
    assert "Render worker token: %d chars, %s" in source

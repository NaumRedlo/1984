"""The bot's own address, stated in more than one place.

It moved from one domain to another and only some of the places knew. The
address had been read out of `OSU_OAUTH_REDIRECT_URI`'s *default* — which is
not the deployment's address, only what a deployment gets when it says nothing
— and written into a guide and a message for a chat, where it pointed at a
domain that was not the bot's.

Nothing catches that on its own: every one of those files is prose to a test
runner. So this is the one place that reads them all and insists they say the
same host.

Deliberately compared against the literal in `config/settings.py` rather than
against the environment: a deployment sets its own redirect URI, and a test
that read `os.environ` would pass or fail depending on whose machine it ran on.
The literal is the project's own statement of where this bot lives, and the
documents are supposed to be repeating it.
"""

import os
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Every file that names the bot's address, and what it is for.
REPEATED_IN = {
    "docs/guide.ru.html": "the guide people follow to set a worker up",
    "docs/worker-call.ru.txt": "the message pasted into the chat",
}


def declared_host() -> str:
    """The host in `OSU_OAUTH_REDIRECT_URI`'s default, read out of the source."""
    source = open(os.path.join(ROOT, "config", "settings.py"), encoding="utf-8").read()
    match = re.search(
        r'OSU_OAUTH_REDIRECT_URI\s*=\s*os\.getenv\([^,]+,\s*"([^"]+)"\)', source
    )
    assert match, "OSU_OAUTH_REDIRECT_URI no longer has a default to read"
    host = urlparse(match.group(1)).netloc
    assert host, f"the default is not a URL: {match.group(1)}"
    return host


def test_every_document_names_the_same_host():
    host = declared_host()
    for path, what in REPEATED_IN.items():
        text = open(os.path.join(ROOT, path), encoding="utf-8").read()
        assert host in text, (
            f"{path} — {what} — does not name {host}. The bot's address is "
            f"stated in {len(REPEATED_IN) + 1} places and they have drifted; "
            f"see config/settings.py for the one that counts."
        )


def test_no_document_names_a_host_the_settings_do_not():
    """The half that actually bit. Both documents kept a stale domain while the
    deployment had moved, and each of them read as authoritative on its own."""
    host = declared_host()
    # Any bare hostname that looks like this project's own, in any document.
    looks_like_ours = re.compile(r"https://([a-z0-9-]+\.[a-z0-9.-]+\.[a-z]{2,})")
    for path in REPEATED_IN:
        text = open(os.path.join(ROOT, path), encoding="utf-8").read()
        for found in set(looks_like_ours.findall(text)):
            if found.endswith("github.com") or "fonts.g" in found:
                continue  # somebody else's address, and meant to be
            assert found == host, (
                f"{path} points at {found}, and this bot lives at {host}"
            )

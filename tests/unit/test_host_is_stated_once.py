import os
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPEATED_IN = {
    "docs/guide.ru.html": "the guide people follow to set a worker up",
    "docs/worker-call.ru.txt": "the message pasted into the chat",
}

def declared_host() -> str:
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
    host = declared_host()

    looks_like_ours = re.compile(r"https://([a-z0-9-]+\.[a-z0-9.-]+\.[a-z]{2,})")
    for path in REPEATED_IN:
        text = open(os.path.join(ROOT, path), encoding="utf-8").read()
        for found in set(looks_like_ours.findall(text)):

            if any(
                found.endswith(theirs)
                for theirs in ("github.com", "rustup.rs", "brew.sh")
            ) or "fonts.g" in found:
                continue
            assert found == host, (
                f"{path} points at {found}, and this bot lives at {host}"
            )

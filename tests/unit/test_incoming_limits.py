"""How big a file this deployment can be handed, and what it says when it cannot.

Two limits meet here and they call for opposite answers. One is this bot's own
disk, which whoever runs it can raise with `MAX_SKIN_MB`. The other is
Telegram's: the cloud Bot API will not serve a file over 20 MB through
`getFile` however large the upload was allowed to be, and only a self-hosted
API server lifts that.

Getting them the wrong way round is what the old fixed 32 MB did. It sat
*above* the cloud limit, so a 25 MB skin passed the bot's check and then failed
at the download, with an error about something else entirely.
"""

import pytest

from bot.handlers.dossier import handlers


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr(handlers, "TELEGRAM_BOT_API_URL", "")


@pytest.fixture
def own_server(monkeypatch):
    monkeypatch.setattr(handlers, "TELEGRAM_BOT_API_URL", "http://localhost:8081")


class TestWhatCanBeHandedOver:
    def test_the_cloud_api_stops_at_twenty_megabytes(self, cloud):
        assert handlers._max_incoming_bytes() == 20 * 1024 * 1024

    def test_a_self_hosted_one_goes_to_two_gigabytes(self, own_server):
        assert handlers._max_incoming_bytes() == 2000 * 1024 * 1024

    def test_it_is_read_at_call_time_not_at_import(self, monkeypatch):
        """The sending limit already works this way, and for the same reason:
        the answer should follow the config rather than whatever was true when
        the module loaded."""
        monkeypatch.setattr(handlers, "TELEGRAM_BOT_API_URL", "")
        small = handlers._max_incoming_bytes()
        monkeypatch.setattr(handlers, "TELEGRAM_BOT_API_URL", "http://localhost:8081")
        assert handlers._max_incoming_bytes() > small


class TestTheSkinCeiling:
    def test_it_never_promises_more_than_telegram_will_give(self, cloud, monkeypatch):
        """The fault this replaces: a ceiling above what could be downloaded."""
        monkeypatch.setattr(handlers, "MAX_SKIN_MB", 128)
        assert handlers._max_skin_bytes() == handlers._max_incoming_bytes()

    def test_with_a_server_of_its_own_the_deployment_decides(self, own_server, monkeypatch):
        monkeypatch.setattr(handlers, "MAX_SKIN_MB", 128)
        assert handlers._max_skin_bytes() == 128 * 1024 * 1024

    def test_a_deployment_may_ask_for_less_than_telegram_allows(self, own_server, monkeypatch):
        """The disk is the binding constraint, not the protocol."""
        monkeypatch.setattr(handlers, "MAX_SKIN_MB", 16)
        assert handlers._max_skin_bytes() == 16 * 1024 * 1024


def test_the_store_unpacks_to_more_than_an_archive_may_be():
    """A skin is mostly PNGs and WAVs: the PNGs barely shrink and the WAVs
    shrink a great deal, so an archive at the ceiling can unpack to well past
    it and still be an ordinary skin."""
    from config.settings import MAX_SKIN_MB
    from services.dossier.skins import MAX_UNPACKED_BYTES

    assert MAX_UNPACKED_BYTES > MAX_SKIN_MB * 1024 * 1024

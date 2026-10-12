from services.render_farm import channels


def test_only_public_channel_names_are_read():
    assert channels.fits("osunewsru") and channels.fits("a_b1")
    for name in ["", "abc", "1abc", "has-dash", "x" * 33, "+invite", "a/b", "имя"]:
        assert not channels.fits(name), name


def test_a_page_is_kept_for_a_while_and_the_oldest_gives_way(monkeypatch):
    monkeypatch.setattr(channels, "_kept", {})
    channels.keep("OsuNews", "page", now=100)
    assert channels.held("osunews", now=100 + channels.KEPT_FOR - 1) == "page"
    assert channels.held("osunews", now=100 + channels.KEPT_FOR) is None
    assert channels.held("other", now=100) is None
    for at in range(channels.KEPT_MOST + 5):
        channels.keep(f"name{at}", str(at), now=200 + at)
    assert len(channels._kept) <= channels.KEPT_MOST
    assert channels.held("name0", now=300) is None
    assert channels.held(f"name{channels.KEPT_MOST + 4}", now=300) == str(channels.KEPT_MOST + 4)

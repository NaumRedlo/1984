"""The Python↔Rust bridge: running the binary and finding the map.

Every failure here reaches a human as a message, so the tests care about *what
is said* as much as that an exception was raised — "движок не собран" and "карта
не найдена" call for completely different fixes.
"""

import pytest

from services.dossier import maps, runner


# ── running the binary ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_missing_binary_says_how_to_build_it(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(tmp_path / "nope"))
    with pytest.raises(runner.DossierError) as excinfo:
        await runner.judge("replay.osr", str(tmp_path))
    assert "cargo build --release" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_non_executable_binary_counts_as_missing(monkeypatch, tmp_path):
    fake = tmp_path / "dossier"
    fake.write_text("not really a program")
    fake.chmod(0o644)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(fake))
    assert runner.is_available() is False
    with pytest.raises(runner.DossierError):
        await runner.inspect("replay.osr")


@pytest.mark.asyncio
async def test_output_is_read_even_when_the_exit_code_is_non_zero(monkeypatch, tmp_path):
    """`judge` exits non-zero when any replay was skipped, but still reports the
    ones it managed. Treating the exit code as fatal would throw those away."""
    script = tmp_path / "dossier"
    script.write_text('#!/bin/sh\necho \'{"replay":"a.osr","exact":true}\'\nexit 1\n')
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    result = await runner.inspect("a.osr")
    assert result["exact"] is True


@pytest.mark.asyncio
async def test_garbage_output_becomes_a_readable_error(monkeypatch, tmp_path):
    script = tmp_path / "dossier"
    script.write_text('#!/bin/sh\necho "segfault or something" >&2\nexit 101\n')
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    with pytest.raises(runner.DossierError) as excinfo:
        await runner.inspect("a.osr")
    assert "segfault" in str(excinfo.value)


# ── finding the map ──────────────────────────────────────────────────────

class _Api:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.asked = []

    async def lookup_beatmap_by_checksum(self, checksum):
        self.asked.append(checksum)
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_an_empty_hash_never_reaches_the_api():
    api = _Api()
    with pytest.raises(maps.MapUnavailable):
        await maps.ensure_map(api, "")
    assert api.asked == []


@pytest.mark.asyncio
async def test_an_unknown_map_is_reported_as_unfetchable():
    """Unsubmitted or locally edited maps aren't a transient failure, so the
    message must not read like one."""
    with pytest.raises(maps.MapUnavailable) as excinfo:
        await maps.ensure_map(_Api(result=None), "deadbeef")
    assert "не найдена" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_api_failure_is_distinguished_from_a_missing_map():
    with pytest.raises(maps.MapUnavailable) as excinfo:
        await maps.ensure_map(_Api(error=RuntimeError("timeout")), "deadbeef")
    assert "osu! API" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_found_map_is_downloaded_by_set_id(monkeypatch):
    downloaded = []

    async def fake_download(beatmapset_id):
        downloaded.append(beatmapset_id)
        return True

    monkeypatch.setattr(maps, "download_beatmap", fake_download)
    record = {"id": 7, "beatmapset_id": 4242, "version": "Insane"}
    assert await maps.ensure_map(_Api(result=record), "abc") is record
    assert downloaded == [4242]


@pytest.mark.asyncio
async def test_a_failed_download_is_surfaced(monkeypatch):
    async def fake_download(_beatmapset_id):
        return False

    monkeypatch.setattr(maps, "download_beatmap", fake_download)
    with pytest.raises(maps.MapUnavailable) as excinfo:
        await maps.ensure_map(_Api(result={"beatmapset_id": 1}), "abc")
    assert "зеркал" in str(excinfo.value)


def test_describe_falls_back_when_the_set_is_absent():
    assert maps.describe(None) == "неизвестная карта"
    assert maps.describe({"id": 5, "version": "Hard"}) == "Hard"
    assert (
        maps.describe(
            {"version": "Insane", "beatmapset": {"artist": "Rita", "title": "dorchadas"}}
        )
        == "Rita — dorchadas [Insane]"
    )


# ── which skin the bot renders in ────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_render_uses_the_configured_skin(monkeypatch, tmp_path):
    """The bot renders in the project's own look by default. Leaving the flag
    off meant the engine fell back to `classic` and the house skin was
    reachable only from the command line."""
    seen = tmp_path / "args.txt"
    script = tmp_path / "dossier"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > {seen}\n'
        'while [ "$1" != "--out" ]; do shift; done\n'
        'echo made > "$2"\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))
    monkeypatch.setattr(runner, "DOSSIER_SKIN", "1984")

    out = tmp_path / "video.mp4"
    await runner.video("replay.osr", str(tmp_path), str(out))

    args = seen.read_text().split()
    assert "--skin" in args
    assert args[args.index("--skin") + 1] == "1984"


@pytest.mark.asyncio
async def test_a_caller_can_ask_for_a_different_skin(monkeypatch, tmp_path):
    seen = tmp_path / "args.txt"
    script = tmp_path / "dossier"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > {seen}\n'
        'while [ "$1" != "--out" ]; do shift; done\n'
        'echo made > "$2"\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))
    monkeypatch.setattr(runner, "DOSSIER_SKIN", "1984")

    out = tmp_path / "video.mp4"
    await runner.video("replay.osr", str(tmp_path), str(out), skin="classic")

    args = seen.read_text().split()
    assert args[args.index("--skin") + 1] == "classic"


# ── the engine's own report ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_render_report_reaches_the_caller(monkeypatch, tmp_path):
    """The engine writes its thread count and timing to stderr. That was being
    captured and thrown away on the success path, so a slow render on the
    server could not be diagnosed at all."""
    script = tmp_path / "dossier"
    script.write_text(
        "#!/bin/sh\n"
        'echo "   3 render thread(s), 2 frame buffers each" >&2\n'
        'printf "\\r120/720 frames, 40/s\\r" >&2\n'
        'echo "   6.2ms of drawing per frame, 4.4ms piping" >&2\n'
        'while [ "$1" != "--out" ]; do shift; done\n'
        'echo made > "$2"\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    report = await runner.video("r.osr", str(tmp_path), str(tmp_path / "v.mp4"))
    joined = "\n".join(report)
    assert "3 render thread(s)" in joined
    assert "4.4ms piping" in joined
    # The progress ticker redraws one line thousands of times; keeping it would
    # bury the two lines worth reading.
    assert "40/s" not in joined


@pytest.mark.asyncio
async def test_a_failed_render_still_reports_what_the_engine_said(monkeypatch, tmp_path):
    script = tmp_path / "dossier"
    script.write_text('#!/bin/sh\necho "ffmpeg not found" >&2\nexit 1\n')
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    with pytest.raises(runner.DossierError) as excinfo:
        await runner.video("r.osr", str(tmp_path), str(tmp_path / "v.mp4"))
    assert "ffmpeg not found" in str(excinfo.value)

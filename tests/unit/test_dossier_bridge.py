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
    monkeypatch.setattr(runner, "DOSSIER_SKIN", "classic")

    out = tmp_path / "video.mp4"
    await runner.video("replay.osr", str(tmp_path), str(out))

    args = seen.read_text().split()
    assert "--skin" in args
    assert args[args.index("--skin") + 1] == "classic"


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
    monkeypatch.setattr(runner, "DOSSIER_SKIN", "classic")

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

    result = await runner.video("r.osr", str(tmp_path), str(tmp_path / "v.mp4"))
    joined = "\n".join(result.report)
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


@pytest.mark.asyncio
async def test_the_encoder_knobs_come_from_settings(monkeypatch, tmp_path):
    """Once drawing is parallel the encoder is the wall, so preset and CRF stop
    being defaults nobody touches and become the main thing to tune — which
    means they belong in config rather than in the call."""
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
    monkeypatch.setattr(runner, "DOSSIER_PRESET", "superfast")
    monkeypatch.setattr(runner, "DOSSIER_CRF", "23")

    await runner.video("r.osr", str(tmp_path), str(tmp_path / "v.mp4"))
    args = seen.read_text().split()
    assert args[args.index("--preset") + 1] == "superfast"
    assert args[args.index("--crf") + 1] == "23"


def test_the_finished_video_reports_its_own_shape():
    """Telegram lays a video's placeholder out from the numbers it is given,
    not from the stream, so a render sent without them arrives square on a
    phone. The engine that wrote the file is the one that knows."""
    events = [
        {"event": "progress", "frames": 60, "of": 180, "per_second": 40.0, "left_seconds": 3.0},
        {"event": "video", "width": 1280, "height": 720, "seconds": 3.0},
    ]
    assert runner._video_meta(events) == (1280, 720, 3)


def test_a_render_without_that_event_still_sends():
    """An engine that never said: the video goes anyway, it just goes without
    the hints. Refusing to send would be a far worse failure than a wrong
    placeholder."""
    assert runner._video_meta([{"event": "progress", "frames": 1, "of": 2}]) == (None, None, None)
    assert runner._video_meta([]) == (None, None, None)


def test_an_unreadable_event_is_not_an_unsent_video():
    """The stream is a contract between two programs and contracts drift. A
    field that is missing or is suddenly a string costs the placeholder, not
    the render."""
    assert runner._video_meta([{"event": "video", "width": 1920}]) == (None, None, None)
    assert runner._video_meta([{"event": "video", "width": "wide", "height": 1, "seconds": 1}]) == (
        None,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_the_render_result_carries_the_shape_through(monkeypatch, tmp_path):
    script = tmp_path / "dossier"
    script.write_text(
        "#!/bin/sh\n"
        'while [ "$1" != "--out" ]; do shift; done\n'
        'echo made > "$2"\n'
        'echo "dossier: video 1920x1080 12.500s" >&2\n'
        'echo \'{"event":"video","width":1920,"height":1080,"seconds":12.5}\'\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    result = await runner.video("r.osr", str(tmp_path), str(tmp_path / "v.mp4"))
    assert (result.width, result.height, result.duration) == (1920, 1080, 12)
    # The prose still arrives, and is still what a person is shown afterwards.
    assert any("1920x1080" in line for line in result.report)


@pytest.mark.asyncio
async def test_a_render_asks_the_engine_for_events(monkeypatch, tmp_path):
    """The flag is what makes the rest of this work, and it is easy to lose in
    a list of arguments assembled in one place and used by two commands."""
    seen = tmp_path / "argv"
    script = tmp_path / "dossier"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{seen}"\n'
        'while [ "$1" != "--out" ]; do shift; done\n'
        'echo made > "$2"\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    await runner.video("r.osr", str(tmp_path), str(tmp_path / "v.mp4"))
    assert "--events" in seen.read_text().split()


def test_time_left_keeps_seconds_where_someone_is_watching():
    """Rounded to whole minutes, the end of every render reads "~1 мин" and
    then "~0 мин" — and that is the stretch anyone is still looking at."""
    from bot.handlers.dossier.handlers import _left

    assert _left(4) == "4 с"
    assert _left(59) == "59 с"
    # Minutes only once there are any, and the seconds stay two digits so the
    # line does not change width as it counts down.
    assert _left(60) == "1 мин 00 с"
    assert _left(95) == "1 мин 35 с"
    assert _left(125) == "2 мин 05 с"
    # A negative estimate is arithmetic, not news: the engine's own rate can
    # overshoot on the last tick.
    assert _left(-3) == "0 с"


def _verdict(**over):
    base = {
        "ours": {"300": 10, "100": 2, "50": 0, "miss": 1},
        "theirs": {"300": 11, "100": 1, "50": 0, "miss": 1},
        "our_max_combo": 20, "their_max_combo": 21,
        "our_accuracy": 90.0, "their_accuracy": 91.0,
        "exact": False, "player": "tester", "mods": "NM", "objects": 13,
        "finished": True, "judged": 13,
    }
    base.update(over)
    return base


def test_the_verdict_message_carries_the_answer_and_not_the_explanations():
    """The table is read every time a replay is sent; the explanations are read
    when something looks wrong. Stacking the second under the first meant five
    paragraphs under a table nobody had finished reading."""
    from bot.handlers.dossier.handlers import _format

    text = _format(_verdict(misses={"circle": 3, "slider": 0, "spinner": 0,
                                    "geometry_suspects": 2, "with_nearby_click": 3,
                                    "median_overshoot_px": 4.0}), "Some map [Hard]")
    assert "Расхождение." in text
    assert "Наши промахи" not in text


def test_an_early_end_stays_in_the_message_rather_than_behind_a_button():
    """A table covering 802 of 1894 objects under a heading that says 1894 is
    misread in the first second, so this one cannot wait for a tap."""
    from bot.handlers.dossier.handlers import _format

    text = _format(_verdict(finished=False, judged=802, objects=1894), "Some map [Hard]")
    assert "802 из 1894" in text


def test_a_section_with_nothing_to_say_gets_no_button():
    """A button that opens an empty page costs a tap to learn nothing."""
    from bot.handlers.dossier.handlers import _verdict_keyboard

    quiet = _verdict(misses=None, lenient_tails=0, counts_match=True)
    sections = [
        b.callback_data
        for row in _verdict_keyboard("tok", quiet).inline_keyboard
        for b in row
        if b.callback_data.startswith("dsa:")
    ]
    assert "dsa:tok:misses" not in sections
    assert "dsa:tok:tails" not in sections
    # The render row is always there.
    assert any(
        b.callback_data == "dsr:tok"
        for row in _verdict_keyboard("tok", quiet).inline_keyboard
        for b in row
    )


def test_the_settings_screen_marks_what_is_already_chosen():
    """A settings screen that only shows what you can change makes you tap
    something to find out what is already true.

    The screen moved into `sts` — it used to hang off the replay, which meant
    the settings existed only while a replay did. The marking is the part worth
    keeping a test on.
    """
    from bot.handlers.profile.settings_menu.render import _render_kb
    from bot.handlers.dossier.renders import Choices

    chosen = Choices(size="1920x1080", fps=30, mute=True)
    # Only the pick-one rows: the skin list marks its own choice too, and the
    # switches say what they are by being ticked rather than by being marked.
    marked = [
        b.text
        for row in _render_kb(chosen, sharing=False, lang="ru").inline_keyboard
        for b in row
        if b.text.startswith("● ") and ":skin:" not in (b.callback_data or "")
    ]
    assert marked == ["● 1080p", "● 30 fps"]
    # And the sound, which is a switch: ticked because it is muted.
    muted = [
        b.text
        for row in _render_kb(chosen, sharing=False, lang="ru").inline_keyboard
        for b in row
        if (b.callback_data or "").startswith("st:rnd:mute:")
    ]
    assert muted == ["☑️ Без звука"]
    assert "1920x1080" in chosen.summary() and "30 fps" in chosen.summary()


def test_settings_are_remembered_per_user():
    """Re-picking the size for every replay is the friction that makes people
    stop using a tool."""
    from bot.handlers.dossier import renders

    renders.choices(4242).size = "854x480"
    assert renders.choices(4242).size == "854x480"
    assert renders.choices(9999).size == "1280x720", "and one user's choice is not everyone's"


def test_a_scoreboard_row_carries_the_mods_it_was_set_with():
    """These rows are each player's best score whatever they used, so a no-mod
    million sits beside a HardRock DoubleTime run. The numbers are honest and
    the impression is not — the mods are what tell the two apart."""
    from services.dossier.rivals import _row

    row = _row(
        "Uika Misumi",
        {"score": 12345678, "accuracy": 0.9921, "mods": [{"acronym": "HD"}, {"acronym": "DT"}]},
    )
    assert row.split("\t")[:4] == ["Uika Misumi", "12345678", "99.21", "HDDT"]


def test_no_mods_leaves_the_column_empty_rather_than_saying_NM():
    from services.dossier.rivals import _row

    # The column is present and empty, rather than carrying the word for "none".
    for mods in ([], ["NM"]):
        assert _row("sw1t", {"score": 900, "accuracy": 0.95, "mods": mods}).split("\t")[3] == ""


def test_a_tab_in_a_name_cannot_break_the_columns():
    """A name is the one field that could contain the separator."""
    from services.dossier.rivals import _row

    row = _row("bad\tname", {"score": 5, "mods": []})
    fields = row.split("\t")
    assert fields[0] == "bad name"
    assert fields[1] == "5", "and the score is still the second column"


def test_a_scoreless_player_is_left_out_entirely():
    from services.dossier.rivals import _row

    assert _row("nobody", {"score": 0}) is None
    assert _row("nobody", {}) is None


def test_the_collector_ranks_by_score_and_skips_players_with_none():
    import asyncio

    from services.dossier.rivals import collect

    class Client:
        async def get_user_beatmap_scores(self, beatmap_id, user_id):
            return {
                10: [{"score": 500, "accuracy": 0.90, "mods": []}],
                11: [{"score": 900, "accuracy": 0.99, "mods": [{"acronym": "HR"}]}],
                12: [],
            }.get(user_id, [])

    class Player:
        def __init__(self, uid, name):
            self.id, self.osu_user_id, self.osu_username = uid, uid, name

    class Session:
        """Two queries in order: the chat's players, then what we already know.

        Nothing is on record here, so every player is asked — which is the path
        worth testing, the local shortcut being the one that skips it.
        """

        def __init__(self):
            # The membership check comes first now, then the chat's players,
            # then what we already know.
            self.answers = [
                [1],
                [Player(10, "a"), Player(11, "b"), Player(12, "c")],
                [],
            ]

        async def execute(self, _query):
            rows = self.answers.pop(0) if self.answers else []

            class Result:
                def scalars(self):
                    class Scalars:
                        def all(self):
                            return rows

                        def first(self):
                            return rows[0] if rows else None

                    return Scalars()

            return Result()

    rows = asyncio.run(collect(Client(), Session(), -100, 4242, player="a")).splitlines()
    assert [r.split("\t")[0] for r in rows] == ["b", "a"], "best first, and c has no score"


def test_no_beatmap_means_no_scoreboard_rather_than_an_error():
    import asyncio

    from services.dossier.rivals import collect

    assert asyncio.run(collect(None, None, -100, 0)) == ""


def test_the_osu_file_is_rejected_when_it_is_not_the_revision_the_replay_used():
    """osu! serves the map as it is *now*. A map revised since the replay was set
    comes back a different file, which would be judged against the wrong notes
    without ever looking wrong."""
    from utils.osu import beatmap_osu

    body = b"osu file format v14\n\n[HitObjects]\n256,192,1000,1,0\n"
    assert beatmap_osu._keep(body, "0" * 32, 1) is False


def test_an_empty_answer_means_the_map_was_deleted_rather_than_that_the_fetch_failed():
    """ppy answers 200 with nothing at all for a map deleted since it was played.
    The id is real; the file is not."""
    from utils.osu import beatmap_osu

    assert beatmap_osu._keep(b"", "0" * 32, 1) is False


def test_an_error_page_is_not_mistaken_for_a_beatmap():
    from utils.osu import beatmap_osu

    assert beatmap_osu._keep(b"<!DOCTYPE html><html>404", "0" * 32, 1) is False


def test_a_graveyard_map_has_no_leaderboard_to_read():
    """osu! keeps scores for ranked, approved, qualified and loved. Everywhere
    else `get_user_beatmap_scores` has nothing to return however many times it is
    asked — so the whole chat's worth of requests would be spent learning what
    the status already says."""
    from services.dossier.rivals import has_leaderboard

    assert has_leaderboard({"status": "ranked"})
    assert has_leaderboard({"status": "loved"})
    assert not has_leaderboard({"status": "graveyard"})
    assert not has_leaderboard({"status": "pending"})
    assert not has_leaderboard({"status": "wip"})
    # Unknown counts as yes: guessing "no" would silently drop a scoreboard that
    # exists, and guessing "yes" costs a few empty answers.
    assert has_leaderboard({})
    assert has_leaderboard(None)


@pytest.mark.asyncio
async def test_an_empty_scoreboard_names_its_reason(monkeypatch):
    """Several quite different causes call for different responses — choose a
    chat, expect nothing, render somebody who is here, wait for one of them to
    play it, or come and look at a bug. Drawing nothing and saying nothing makes
    all of them look like the last."""
    from bot.handlers.dossier import handlers
    from bot.handlers.dossier.handlers import _why_no_scoreboard

    assert "в личке" in await _why_no_scoreboard({"chat_id": None})
    assert "graveyard" in await _why_no_scoreboard(
        {"chat_id": -100, "beatmap_status": "graveyard"}
    )

    # The chat check is asked of the same function the gate uses, so the two
    # cannot drift apart. Stubbed here rather than given a database.
    async def stranger(_session, _chat_id, _player):
        return False

    async def member(_session, _chat_id, _player):
        return True

    monkeypatch.setattr(handlers.dossier, "plays_here", stranger)
    said = await _why_no_scoreboard(
        {"chat_id": -100, "beatmap_status": "ranked", "player": "mrekk"}
    )
    assert "mrekk" in said and "нет в беседе" in said

    monkeypatch.setattr(handlers.dossier, "plays_here", member)
    assert "ни у кого" in await _why_no_scoreboard(
        {"chat_id": -100, "beatmap_status": "ranked", "player": "sw1t"}
    )


def test_scores_we_already_hold_are_not_asked_for_again():
    """The profile sync writes every attempt it sees into UserMapAttempt, so a
    map the chat played recently is often already here — and one SQL query beats
    forty round trips through a rate limiter."""
    import asyncio

    from services.dossier.rivals import collect

    asked = []

    class Client:
        async def get_user_beatmap_scores(self, beatmap_id, user_id):
            asked.append(user_id)
            return [{"score": 100, "accuracy": 0.5, "mods": []}]

    class Player:
        def __init__(self, uid, name):
            self.id, self.osu_user_id, self.osu_username = uid, uid, name

    class Attempt:
        def __init__(self, uid, score):
            self.user_id, self.score, self.accuracy, self.mods = uid, score, 0.99, "HR"

    class Session:
        def __init__(self):
            # Players, then the attempts already on record: 10 is known, 11 is not.
            self.answers = [
                [1],
                [Player(10, "known"), Player(11, "unknown")],
                [Attempt(10, 5000)],
            ]

        async def execute(self, _query):
            rows = self.answers.pop(0) if self.answers else []

            class Result:
                def scalars(self):
                    class Scalars:
                        def all(self):
                            return rows

                        def first(self):
                            return rows[0] if rows else None

                    return Scalars()

            return Result()

    rows = asyncio.run(collect(Client(), Session(), -100, 4242, player="a")).splitlines()
    assert asked == [11], "only the player we had nothing for was asked about"
    assert rows[0].startswith("known\t5000"), "and the recorded score is the better one"


def test_the_scoreboard_uses_the_same_scoring_as_the_replay():
    """The API answers with two scores three orders of magnitude apart. A stable
    replay's own row is a ScoreV1 total in the hundreds of millions; putting
    lazer's standardised million beside it said nothing except that the columns
    disagreed about what a point is."""
    from services.dossier.rivals import _row

    both = {"total_score": 712_345, "legacy_total_score": 41_800_000, "accuracy": 0.99, "mods": []}
    assert _row("x", both, lazer=True).split("\t")[1] == "712345"
    assert _row("x", both, lazer=False).split("\t")[1] == "41800000"


def test_a_lazer_score_has_no_place_on_a_stable_board():
    """It has no ScoreV1 total, and there is no honest conversion: ScoreV1 depends
    on the map's difficulty multiplier and the combo carried into every hit, both
    of which lazer's scoring deliberately throws away."""
    from services.dossier.rivals import _row

    lazer_only = {"total_score": 712_345, "accuracy": 0.99, "mods": []}
    assert _row("x", lazer_only, lazer=True) is not None
    assert _row("x", lazer_only, lazer=False) is None


def test_the_local_shortcut_is_skipped_when_the_currency_would_not_match():
    """UserMapAttempt.score holds whatever the profile sync picked, which is
    lazer's standardised total — so on a stable board it must not be used."""
    import asyncio

    from services.dossier.rivals import collect

    asked = []

    class Client:
        async def get_user_beatmap_scores(self, beatmap_id, user_id):
            asked.append(user_id)
            return [{"legacy_total_score": 9_000_000, "accuracy": 0.97, "mods": []}]

    class Player:
        def __init__(self, uid, name):
            self.id, self.osu_user_id, self.osu_username = uid, uid, name

    class Attempt:
        def __init__(self, uid, score):
            self.user_id, self.score, self.accuracy, self.mods = uid, score, 0.99, ""

    def session():
        class Session:
            def __init__(self):
                self.answers = [[1], [Player(10, "a")], [Attempt(10, 700_000)]]

            async def execute(self, _query):
                rows = self.answers.pop(0) if self.answers else []

                class Result:
                    def scalars(self):
                        class Scalars:
                            def all(self):
                                return rows

                            def first(self):
                                return rows[0] if rows else None

                        return Scalars()

                return Result()

        return Session()

    asked.clear()
    asyncio.run(collect(Client(), session(), -100, 1, lazer=True, player="a"))
    assert asked == [], "on a lazer board the recorded score is the right currency"

    asked.clear()
    rows = asyncio.run(
        collect(Client(), session(), -100, 1, lazer=False, player="a")
    ).splitlines()
    assert asked == [10], "on a stable board it has to be asked for again"
    assert rows[0].split("\t")[1] == "9000000"


def test_a_jpeg_avatar_reaches_the_engine_as_a_png():
    """The engine has one image decoder and no network, so PNG is all it takes —
    and osu! serves JPEG about as often as PNG. Without this step half the rows
    would draw without a face for no reason anybody could see."""
    import io
    import tempfile

    from PIL import Image

    from services.dossier.rivals import pictures_for

    def blob(size, fmt):
        buffer = io.BytesIO()
        Image.new("RGB", size, (200, 60, 60)).save(buffer, fmt)
        return buffer.getvalue()

    class Player:
        osu_user_id = 4242
        avatar_data = blob((256, 256), "JPEG")
        cover_data = blob((1500, 400), "JPEG")

    work = tempfile.mkdtemp()
    avatar, cover = pictures_for(Player(), work, "4242")
    with Image.open(avatar) as image:
        assert image.format == "PNG"
        assert image.size == (128, 128)
    with Image.open(cover) as image:
        assert image.format == "PNG"


def test_a_rectangular_avatar_is_cropped_rather_than_squashed():
    """An avatar is drawn square. Squashing a wide one is worse than losing its
    edges — a face stretched sideways is the first thing anybody notices."""
    import io
    import tempfile

    from PIL import Image

    from services.dossier.rivals import pictures_for

    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), (10, 200, 10)).save(buffer, "PNG")

    class Player:
        osu_user_id = 1
        avatar_data = buffer.getvalue()
        cover_data = None

    avatar, cover = pictures_for(Player(), tempfile.mkdtemp(), "1")
    with Image.open(avatar) as image:
        assert image.width == image.height
    assert cover is None, "and a player with no cover gets no path"


def test_a_row_carries_its_picture_paths():
    from services.dossier.rivals import _row

    row = _row("x", {"total_score": 500, "accuracy": 0.9, "mods": []}, True, "/a.png", "/c.png")
    assert row.split("\t")[4:] == ["/a.png", "/c.png"]
    bare = _row("x", {"total_score": 500, "accuracy": 0.9, "mods": []})
    assert bare.split("\t")[4:] == ["", ""], "and a row without them still has the columns"


def test_a_player_with_only_a_url_gets_their_face_fetched():
    """`avatar_data` is written by the profile sync, and only when the URL has
    changed — so a member whose profile has not been synced since the caching was
    added has a perfectly good URL and no bytes. The board then drew one face and
    empty frames beside it, which looked like the same picture on every row."""
    import asyncio

    from services.dossier.rivals import ensure_pictures

    asked = []

    class Client:
        async def _download_image_bytes(self, url):
            asked.append(url)
            return b"bytes-for-" + url.encode()

    class Player:
        def __init__(self, name, has_data):
            self.osu_username = name
            self.avatar_url = f"https://a.example/{name}.jpg"
            self.cover_url = None
            self.avatar_data = b"already here" if has_data else None
            self.cover_data = None

    class Session:
        async def commit(self):
            pass

    cached, missing = Player("cached", True), Player("missing", False)
    asyncio.run(ensure_pictures(Client(), Session(), [cached, missing]))

    assert asked == ["https://a.example/missing.jpg"], "only the one we lacked"
    assert missing.avatar_data == b"bytes-for-https://a.example/missing.jpg"
    assert cached.avatar_data == b"already here", "and the cached one is untouched"


def test_fetching_faces_survives_a_dead_image_host():
    """A missing face is not worth a render."""
    import asyncio

    from services.dossier.rivals import ensure_pictures

    class Client:
        async def _download_image_bytes(self, url):
            raise OSError("no")

    class Player:
        osu_username = "x"
        avatar_url = "https://a.example/x.jpg"
        cover_url = None
        avatar_data = None
        cover_data = None

    class Session:
        async def commit(self):
            pass

    player = Player()
    asyncio.run(ensure_pictures(Client(), Session(), [player]))
    assert player.avatar_data is None


# ── the reel ─────────────────────────────────────────────────────────────

def test_a_reels_shape_is_the_reels_and_not_its_first_clips():
    """A reel reports its shape once per clip and once for the file it cut them
    into. Read forwards, a twenty-eight-second reel of six-second clips is
    labelled six — and Telegram believes it and draws its scrubber from it."""
    events = [
        {"event": "clip", "index": 1, "of": 5, "at_ms": 41100.0, "reason": "the densest stretch"},
        {"event": "video", "width": 1920, "height": 1080, "seconds": 6.0},
        {"event": "clip", "index": 2, "of": 5, "at_ms": 96200.0, "reason": "kiai"},
        {"event": "video", "width": 1920, "height": 1080, "seconds": 6.0},
        {"event": "video", "width": 1920, "height": 1080, "seconds": 28.4},
    ]
    assert runner._video_meta(events) == (1920, 1080, 28)


def test_progress_carries_which_clip_it_belongs_to():
    """The frame counter restarts at every clip, so a bar built from it alone
    fills to a hundred percent five times — which reads as a render starting
    over rather than as a reel getting on with it."""
    assert runner._clip_of({"index": 2, "of": 5}) == (2, 5)
    assert runner._clip_of({"event": "progress", "frames": 1}) is None

    tick = {"frames": 120, "of": 360, "per_second": 40.0, "left_seconds": 6.0}
    assert runner._progress_of(tick, (2, 5)).clip == (2, 5)
    assert runner._progress_of(tick, None).clip is None
    assert runner._progress_of(tick, None).fraction == pytest.approx(1 / 3)
    # A tick this side cannot read costs a counter update, not a render.
    assert runner._progress_of({"frames": 120}, None) is None


@pytest.mark.asyncio
async def test_the_reel_is_rendered_with_the_same_look_as_a_full_render(
    monkeypatch, tmp_path
):
    """One argument builder for both, so a reel cannot quietly stop wearing the
    deployment's skin the next time the render options change."""
    seen = tmp_path / "args.txt"
    script = tmp_path / "dossier"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" >> {seen}\n'
        'case "$1$2" in *--json*) echo \'{"clips":[{"from_ms":1000,"to_ms":7000,'
        '"scorer":"choke","reason":"a 1425x run breaks 63% of the way in",'
        '"detail":{"combo":1425,"through":0.628}}]}\'; exit 0;; esac\n'
        'while [ "$1" != "--out" ]; do shift; done\n'
        'echo made > "$2"\n'
        'echo "dossier: video 1280x720 28.400s" >&2\n'
        'echo \'{"event":"video","width":1280,"height":720,"seconds":28.4}\'\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))
    monkeypatch.setattr(runner, "DOSSIER_SKIN", "classic")

    result = await runner.exhibit(
        "r.osr", str(tmp_path), str(tmp_path / "reel.mp4"), budget_s=24, clip_s=6
    )

    args = seen.read_text().split("\n")
    assert args[0] == "exhibit"
    assert args[args.index("--skin") + 1] == "classic"
    assert args[args.index("--for") + 1] == "24"
    assert result.render.duration == 28
    assert [m.scorer for m in result.selection.clips] == ["choke"]
    assert result.selection.clips[0].stamp() == "0:01"
    # Six seconds of map at no rate mod is six seconds of watching.
    assert result.selection.watch_seconds() == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_a_play_with_nothing_to_show_says_so(monkeypatch, tmp_path):
    """A replay of somebody quitting twelve seconds in has no moments. That is
    a real answer, and rendering an empty reel would be a worse one."""
    script = tmp_path / "dossier"
    script.write_text("#!/bin/sh\necho '{\"clips\":[]}'\n")
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    with pytest.raises(runner.DossierError, match="нечего показать"):
        await runner.exhibit("r.osr", str(tmp_path), str(tmp_path / "reel.mp4"))


def test_the_reel_carries_its_reasons_under_the_video():
    """The reasons are the whole claim the feature makes. Behind a button, a
    reel is thirty seconds to be taken on trust; under the video, it is thirty
    seconds somebody can disagree with."""
    from bot.handlers.dossier.handlers import _caption

    moments = [
        runner.Moment(
            41_105.0, 47_105.0, "storm", "the densest stretch, 65 objects",
            {"objects": 65, "of_densest": 1.0},
        ),
        runner.Moment(
            186_230.0, 192_230.0, "choke", "a 1425x run breaks 63% in",
            {"combo": 1425, "through": 0.628},
        ),
    ]
    caption = _caption("Deeo_XD — Chambarising", runner.Selection(moments, 1.0))
    assert "0:41 — самый плотный участок карты, 65 объектов" in caption
    assert "3:06 — серия 1425x рвётся на 63% пути" in caption

    # A full render has no selection and keeps the caption it always had.
    assert _caption("Deeo_XD — Chambarising", None) == "Deeo_XD — Chambarising"


def test_a_long_reel_loses_its_last_line_rather_than_its_caption():
    """Telegram cuts a caption past a thousand characters without saying so,
    which would take the title with it."""
    from bot.handlers.dossier.handlers import _caption

    many = [
        runner.Moment(
            i * 10_000.0, i * 10_000.0 + 6_000.0, "storm", "x" * 120,
            {"objects": 60, "of_densest": 0.5},
        )
        for i in range(20)
    ]
    caption = _caption("title", runner.Selection(many, 1.0))
    assert len(caption) <= 1000
    assert caption.startswith("title")


def test_a_moment_speaks_the_language_the_bot_speaks():
    """The engine's own sentence is English, because a terminal is where it
    lives. Phrasing it here from the numbers is why the numbers ship."""
    choke = runner.Moment(0.0, 6000.0, "choke", "english", {"combo": 1425, "through": 0.628})
    assert choke.say() == "серия 1425x рвётся на 63% пути"

    # Russian counts in threes, and `1 промахов` is how a bot sounds foreign.
    for misses, expect in [(1, "1 промах"), (3, "3 промаха"), (42, "42 промаха"), (5, "5 промахов")]:
        moment = runner.Moment(0.0, 1.0, "scramble", "", {"misses": misses, "refused": 0})
        assert moment.say() == f"{expect} подряд"

    both = runner.Moment(0.0, 1.0, "scramble", "", {"misses": 42, "refused": 33})
    assert both.say() == "42 промаха и 33 отказанных клика подряд"


def test_the_edges_of_a_play_say_which_edge_and_how_it_went():
    """"If they are important" was the ask, so the ending has to say which of
    the three endings it was: a death, a landed full combo, or the map simply
    running out."""
    death = runner.Moment(
        0.0, 6000.0, "finale", "",
        {"failed": True, "accuracy": 72.09, "combo": 185, "full_combo": False},
    )
    assert death.say() == "игра обрывается — полоса пустеет на 185x, 72.09%"

    landed = runner.Moment(
        0.0, 6000.0, "finale", "",
        {"failed": False, "accuracy": 100.0, "combo": 2435, "full_combo": True},
    )
    assert landed.say() == "доигрывает — 2435x без единого срыва, 100.00%"

    ran_out = runner.Moment(
        0.0, 6000.0, "finale", "",
        {"failed": False, "accuracy": 96.92, "combo": 258, "full_combo": False},
    )
    assert ran_out.say() == "чем всё кончается — 258x, 96.92%"


def test_the_hardest_movement_says_so_only_when_it_is_the_hardest():
    fastest = runner.Moment(0.0, 1.0, "travel", "", {"speed": 914.2, "of_fastest": 1.0})
    assert fastest.say() == "самое тяжёлое движение в игре, 914 osu!px в секунду"
    merely = runner.Moment(0.0, 1.0, "travel", "", {"speed": 525.0, "of_fastest": 0.57})
    assert merely.say() == "тяжёлое движение, 525 osu!px в секунду"


def test_an_unrecognised_reason_falls_back_to_the_engines_own_words():
    """A seventh scorer added on the engine's side must not silence a moment
    here — the English sentence is a worse answer than a translation and a much
    better one than a blank line."""
    unknown = runner.Moment(0.0, 6000.0, "sparkle", "something new happened", {"n": 1})
    assert unknown.say() == "something new happened"

    # …and so must a reason whose numbers do not match what this side expects.
    broken = runner.Moment(0.0, 6000.0, "choke", "a 900x run breaks", {})
    assert broken.say() == "a 900x run breaks"


@pytest.mark.asyncio
async def test_the_engine_decides_the_length_unless_asked(monkeypatch, tmp_path):
    """How long a reel should be is a property of the play — a clean run of a
    quiet map has three things worth showing and a disaster on a marathon has a
    dozen. Passing a default from this side would be the bot guessing at an
    answer the engine computes."""
    seen = tmp_path / "args.txt"
    script = tmp_path / "dossier"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > {seen}\n'
        'echo \'{"rate":1.0,"clips":[]}\'\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    await runner.moments("r.osr", str(tmp_path))
    args = seen.read_text().split("\n")
    assert "--for" not in args and "--clip" not in args

    await runner.moments("r.osr", str(tmp_path), budget_s=40)
    args = seen.read_text().split("\n")
    assert args[args.index("--for") + 1] == "40"


def test_the_length_of_a_reel_is_counted_in_seconds_of_watching():
    """The spans are map time and a rate mod compresses them, so a reel of six
    six-second clips under DoubleTime is twenty-four seconds to watch and not
    thirty-six. Adding the spans up raw promises a minute and sends forty
    seconds."""
    clips = [runner.Moment(i * 10_000.0, i * 10_000.0 + 6_000.0, "storm", "", {}) for i in range(6)]
    assert runner.Selection(clips, 1.0).watch_seconds() == pytest.approx(36.0)
    assert runner.Selection(clips, 1.5).watch_seconds() == pytest.approx(24.0)
    # A replay whose header lost the rate must not divide by zero.
    assert runner.Selection(clips, 0.0).watch_seconds() == pytest.approx(36.0)


@pytest.mark.asyncio
async def test_a_selection_already_in_hand_is_not_asked_for_again(monkeypatch, tmp_path):
    """The bot names the moments in the message somebody stares at while the
    render runs, so it has the answer before the render starts. Asking again
    judges the same replay a third time for something already in hand."""
    calls = tmp_path / "calls.txt"
    script = tmp_path / "dossier"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$1$2" >> {calls}\n'
        'case "$1$2" in *--json*) echo \'{"rate":1.0,"clips":[]}\'; exit 0;; esac\n'
        'while [ "$1" != "--out" ]; do shift; done\n'
        'echo made > "$2"\n'
        'echo "dossier: video 1280x720 12.000s" >&2\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    known = runner.Selection(
        [runner.Moment(0.0, 6000.0, "choke", "", {"combo": 900, "through": 0.8})], 1.0
    )
    result = await runner.exhibit(
        "r.osr", str(tmp_path), str(tmp_path / "reel.mp4"), chosen=known
    )

    assert result.selection is known
    assert "--json" not in calls.read_text(), "the engine was asked to choose twice"


def test_a_brush_with_death_is_said_in_full():
    """The number that matters is how low it got, and the one that makes it a
    brush rather than an ending is how far it came back."""
    from services.dossier import runner as r

    moment = r.Moment(0.0, 6000.0, "brink", "", {"low": 1.4, "recovered_to": 37.2})
    assert moment.say() == "полоса падает до 1% и возвращается к 37%"


def test_a_clip_holding_two_moments_says_both():
    """A strong jump pattern is the hardest movement in the map *and* where the
    misses are. One clip, two lines — and the second is indented, because it
    shares the first one's seconds rather than adding more."""
    from bot.handlers.dossier.handlers import _caption

    merged = runner.Moment(
        266_500.0, 276_600.0, "scramble", "",
        {"misses": 42, "refused": 33},
        runner.Moment(266_500.0, 276_600.0, "travel", "", {"speed": 964.0, "of_fastest": 1.0}),
    )
    caption = _caption("title", runner.Selection([merged], 1.0))
    assert "4:26 — 42 промаха и 33 отказанных клика подряд" in caption
    assert "· самое тяжёлое движение в игре, 964 osu!px в секунду" in caption

    # The seconds are counted once, not twice: the two share a clip.
    assert runner.Selection([merged], 1.0).watch_seconds() == pytest.approx(10.1)


def test_tapping_says_how_hard_the_fingers_were_working():
    """Tapping is not density: a stretch of long sliders is dense while the
    hand does almost nothing."""
    hardest = runner.Moment(
        0.0, 6000.0, "tapping", "", {"per_second": 11.2, "of_hardest": 1.0, "taps": 67}
    )
    assert hardest.say() == "самый частый тап в игре, 67 нажатий по 11.2 в секунду"
    merely = runner.Moment(
        0.0, 6000.0, "tapping", "", {"per_second": 8.4, "of_hardest": 0.72, "taps": 51}
    )
    assert merely.say() == "частый тап, 51 нажатие по 8.4 в секунду"


def test_a_player_who_is_not_in_the_chat_gets_no_scoreboard():
    """Throw mrekk's replay at the bot and the left of the frame would become a
    stranger's run ranked among people he has never met, with an empty circle
    where his face would be. The row would still be computed — the engine takes
    the player's own score from the replay — which is what makes this worth
    refusing rather than leaving to look after itself."""
    import asyncio

    from services.dossier.rivals import collect

    class Client:
        async def get_user_beatmap_scores(self, beatmap_id, user_id):
            raise AssertionError("nobody should be asked about a board we refuse")

    class Session:
        def __init__(self):
            # The membership query finds nothing: this name is not in the chat.
            self.answers = [[]]

        async def execute(self, _query):
            rows = self.answers.pop(0) if self.answers else []

            class Result:
                def scalars(self):
                    class Scalars:
                        def all(self):
                            return rows

                        def first(self):
                            return rows[0] if rows else None

                    return Scalars()

            return Result()

    assert asyncio.run(collect(Client(), Session(), -100, 4242, player="mrekk")) == ""


def test_a_replay_with_no_player_name_gets_no_scoreboard():
    """An unnamed replay cannot be shown to belong to anybody here, and a board
    is a comparison that needs the person it is about in it."""
    import asyncio

    from services.dossier.rivals import plays_here

    class Session:
        async def execute(self, _query):
            raise AssertionError("an empty name should not reach the database")

    assert asyncio.run(plays_here(Session(), -100, None)) is False
    assert asyncio.run(plays_here(Session(), -100, "   ")) is False

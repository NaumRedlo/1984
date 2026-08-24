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


async def test_osus_own_sounds_are_passed_only_when_a_host_has_them(monkeypatch, tmp_path):
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

    monkeypatch.setattr(runner, "DOSSIER_GAME_SOUNDS", "")
    await runner.video("r.osr", str(tmp_path), str(tmp_path / "v.mp4"))
    assert "--game-sounds" not in seen.read_text().split()

    monkeypatch.setattr(runner, "DOSSIER_GAME_SOUNDS", str(tmp_path / "osu-kit"))
    await runner.video("r.osr", str(tmp_path), str(tmp_path / "v.mp4"))
    args = seen.read_text().split()
    assert args[args.index("--game-sounds") + 1] == str(tmp_path / "osu-kit")


def test_the_finished_video_reports_its_own_shape():
    events = [
        {"event": "progress", "frames": 60, "of": 180, "per_second": 40.0, "left_seconds": 3.0},
        {"event": "video", "width": 1280, "height": 720, "seconds": 3.0},
    ]
    assert runner._video_meta(events) == (1280, 720, 3)


def test_a_render_without_that_event_still_sends():
    assert runner._video_meta([{"event": "progress", "frames": 1, "of": 2}]) == (None, None, None)
    assert runner._video_meta([]) == (None, None, None)


def test_an_unreadable_event_is_not_an_unsent_video():
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
    from bot.handlers.dossier.handlers import _left

    # In the reader's language now, so the test asks in both.
    assert _left(4, "en") == "4s"
    assert _left(59, "en") == "59s"
    assert _left(60, "en") == "1 min 00s"
    assert _left(95, "en") == "1 min 35s"
    assert _left(-3, "en") == "0s"

    assert _left(4, "ru") == "4 с"
    assert _left(125, "ru") == "2 мин 05 с"


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
    from bot.handlers.dossier.handlers import _format

    text = _format(_verdict(misses={"circle": 3, "slider": 0, "spinner": 0,
                                    "geometry_suspects": 2, "with_nearby_click": 3,
                                    "median_overshoot_px": 4.0}), "Some map [Hard]")
    assert "Расхождение." in text
    assert "Наши промахи" not in text


def test_an_early_end_stays_in_the_message_rather_than_behind_a_button():
    from bot.handlers.dossier.handlers import _format

    text = _format(_verdict(finished=False, judged=802, objects=1894), "Some map [Hard]")
    assert "802 из 1894" in text


def test_a_section_with_nothing_to_say_gets_no_button():
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
    from bot.handlers.profile.settings_menu.render import _quality_kb, _render_kb
    from bot.handlers.dossier.renders import Choices

    chosen = Choices(size="1920x1080", fps=30, mute=True)
    # The size and the frame rate are typed rather than picked, so the screen
    # states them on their own row instead of marking one button out of five.
    said = [
        b.text
        for row in _quality_kb(chosen, lang="ru").inline_keyboard
        for b in row
        if (b.callback_data or "").startswith("st:typed:")
    ]
    assert said[:2] == ["Размер — 1920×1080", "Кадры — 30 fps"]
    from bot.handlers.profile.settings_menu import sound

    muted = [
        b.text
        for row in sound._kb(chosen, lang="ru").inline_keyboard
        for b in row
        if (b.callback_data or "").startswith("st:rnd:mute:")
    ]
    assert muted == ["☑️ Без звука"]
    # `×`, not the letter x: the summary is a picture's size, and the letter
    # reads as a variable in a line of numbers.
    assert "1920×1080" in chosen.summary() and "30 fps" in chosen.summary()


def test_settings_are_remembered_per_user():
    from bot.handlers.dossier import renders

    renders.choices(4242).size = "854x480"
    assert renders.choices(4242).size == "854x480"
    assert renders.choices(9999).size == "1280x720", "and one user's choice is not everyone's"


def test_a_scoreboard_row_carries_the_mods_it_was_set_with():
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
        def __init__(self):
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
    from utils.osu import beatmap_osu

    body = b"osu file format v14\n\n[HitObjects]\n256,192,1000,1,0\n"
    assert beatmap_osu._keep(body, "0" * 32, 1) is False


def test_an_empty_answer_means_the_map_was_deleted_rather_than_that_the_fetch_failed():
    from utils.osu import beatmap_osu

    assert beatmap_osu._keep(b"", "0" * 32, 1) is False


def test_an_error_page_is_not_mistaken_for_a_beatmap():
    from utils.osu import beatmap_osu

    assert beatmap_osu._keep(b"<!DOCTYPE html><html>404", "0" * 32, 1) is False


def test_a_graveyard_map_has_no_leaderboard_to_read():
    from services.dossier.rivals import has_leaderboard

    assert has_leaderboard({"status": "ranked"})
    assert has_leaderboard({"status": "loved"})
    assert not has_leaderboard({"status": "graveyard"})
    assert not has_leaderboard({"status": "pending"})
    assert not has_leaderboard({"status": "wip"})
    assert has_leaderboard({})
    assert has_leaderboard(None)


@pytest.mark.asyncio
async def test_an_empty_scoreboard_names_its_reason(monkeypatch):
    from bot.handlers.dossier import handlers
    from bot.handlers.dossier.handlers import _why_no_scoreboard

    # The reasons are localised; the test reads them in English, which is what
    # a reader who never set a language gets.
    assert "private chat" in await _why_no_scoreboard({"chat_id": None}, "en")
    assert "graveyard" in await _why_no_scoreboard(
        {"chat_id": -100, "beatmap_status": "graveyard"}, "en"
    )

    async def stranger(_session, _chat_id, _player):
        return False

    async def member(_session, _chat_id, _player):
        return True

    monkeypatch.setattr(handlers.dossier, "plays_here", stranger)
    said = await _why_no_scoreboard(
        {"chat_id": -100, "beatmap_status": "ranked", "player": "mrekk"}, "en"
    )
    assert "mrekk" in said and "not in this chat" in said

    monkeypatch.setattr(handlers.dossier, "plays_here", member)
    assert "nobody in this chat" in await _why_no_scoreboard(
        {"chat_id": -100, "beatmap_status": "ranked", "player": "sw1t"}, "en"
    )


def test_scores_we_already_hold_are_not_asked_for_again():
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
    from services.dossier.rivals import _row

    both = {"total_score": 712_345, "legacy_total_score": 41_800_000, "accuracy": 0.99, "mods": []}
    assert _row("x", both, lazer=True).split("\t")[1] == "712345"
    assert _row("x", both, lazer=False).split("\t")[1] == "41800000"


def test_a_lazer_score_has_no_place_on_a_stable_board():
    from services.dossier.rivals import _row

    lazer_only = {"total_score": 712_345, "accuracy": 0.99, "mods": []}
    assert _row("x", lazer_only, lazer=True) is not None
    assert _row("x", lazer_only, lazer=False) is None


def test_the_local_shortcut_is_skipped_when_the_currency_would_not_match():
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
    events = [
        {"event": "clip", "index": 1, "of": 5, "at_ms": 41100.0, "reason": "the densest stretch"},
        {"event": "video", "width": 1920, "height": 1080, "seconds": 6.0},
        {"event": "clip", "index": 2, "of": 5, "at_ms": 96200.0, "reason": "kiai"},
        {"event": "video", "width": 1920, "height": 1080, "seconds": 6.0},
        {"event": "video", "width": 1920, "height": 1080, "seconds": 28.4},
    ]
    assert runner._video_meta(events) == (1920, 1080, 28)


def test_progress_carries_which_clip_it_belongs_to():
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
    script = tmp_path / "dossier"
    script.write_text("#!/bin/sh\necho '{\"clips\":[]}'\n")
    script.chmod(0o755)
    monkeypatch.setattr(runner, "DOSSIER_BIN", str(script))

    with pytest.raises(runner.DossierError, match="нечего показать"):
        await runner.exhibit("r.osr", str(tmp_path), str(tmp_path / "reel.mp4"))


def test_the_reel_carries_its_reasons_under_the_video():
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
    choke = runner.Moment(0.0, 6000.0, "choke", "english", {"combo": 1425, "through": 0.628})
    assert choke.say() == "серия 1425x рвётся на 63% пути"

    # Russian counts in threes, and `1 промахов` is how a bot sounds foreign.
    for misses, expect in [(1, "1 промах"), (3, "3 промаха"), (42, "42 промаха"), (5, "5 промахов")]:
        moment = runner.Moment(0.0, 1.0, "scramble", "", {"misses": misses, "refused": 0})
        assert moment.say() == f"{expect} подряд"

    both = runner.Moment(0.0, 1.0, "scramble", "", {"misses": 42, "refused": 33})
    assert both.say() == "42 промаха и 33 отказанных клика подряд"


def test_the_edges_of_a_play_say_which_edge_and_how_it_went():
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
    unknown = runner.Moment(0.0, 6000.0, "sparkle", "something new happened", {"n": 1})
    assert unknown.say() == "something new happened"

    # …and so must a reason whose numbers do not match what this side expects.
    broken = runner.Moment(0.0, 6000.0, "choke", "a 900x run breaks", {})
    assert broken.say() == "a 900x run breaks"


@pytest.mark.asyncio
async def test_the_engine_decides_the_length_unless_asked(monkeypatch, tmp_path):
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
    clips = [runner.Moment(i * 10_000.0, i * 10_000.0 + 6_000.0, "storm", "", {}) for i in range(6)]
    assert runner.Selection(clips, 1.0).watch_seconds() == pytest.approx(36.0)
    assert runner.Selection(clips, 1.5).watch_seconds() == pytest.approx(24.0)
    # A replay whose header lost the rate must not divide by zero.
    assert runner.Selection(clips, 0.0).watch_seconds() == pytest.approx(36.0)


@pytest.mark.asyncio
async def test_a_selection_already_in_hand_is_not_asked_for_again(monkeypatch, tmp_path):
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
    from services.dossier import runner as r

    moment = r.Moment(0.0, 6000.0, "brink", "", {"low": 1.4, "recovered_to": 37.2})
    assert moment.say() == "полоса падает до 1% и возвращается к 37%"


def test_a_clip_holding_two_moments_says_both():
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
    hardest = runner.Moment(
        0.0, 6000.0, "tapping", "", {"per_second": 11.2, "of_hardest": 1.0, "taps": 67}
    )
    assert hardest.say() == "самый частый тап в игре, 67 нажатий по 11.2 в секунду"
    merely = runner.Moment(
        0.0, 6000.0, "tapping", "", {"per_second": 8.4, "of_hardest": 0.72, "taps": 51}
    )
    assert merely.say() == "частый тап, 51 нажатие по 8.4 в секунду"


def test_a_player_who_is_not_in_the_chat_gets_no_scoreboard():
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
    import asyncio

    from services.dossier.rivals import plays_here

    class Session:
        async def execute(self, _query):
            raise AssertionError("an empty name should not reach the database")

    assert asyncio.run(plays_here(Session(), -100, None)) is False
    assert asyncio.run(plays_here(Session(), -100, "   ")) is False

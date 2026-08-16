"""Unpacking somebody else's zip.

An `.osk` arrives from whoever sent it, so most of these are about the archive
being hostile rather than about it being a skin. The rest are about what osu!
considers a skin folder to be, which is flatter and narrower than what people
put in archives.
"""

import os
import zipfile

import pytest

from services.dossier import skins


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(skins, "SKIN_STORE_DIR", str(tmp_path / "skins"))
    return tmp_path


def osk(tmp_path, entries: dict[str, bytes], name="pack.osk") -> str:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for inner, body in entries.items():
            archive.writestr(inner, body)
    return str(path)


# ── the archive being hostile ─────────────────────────────────────────────

def test_a_path_that_climbs_out_of_the_folder_lands_inside_it(tmp_path):
    """`../../.ssh/authorized_keys` is a legal zip entry name. Nothing here
    builds a path out of one — only the last segment is used — so an escaping
    name becomes an ordinary file in the store and goes no further.

    Written first as "the archive is rejected", which the code does not do and
    does not need to: there is no path to reject when there is no path.
    """
    archive = osk(tmp_path, {"../escaped.png": b"x", "hitcircle.png": b"y"})
    name = skins.import_osk(archive, "pack.osk")

    folder = skins.folder_of(name)
    assert sorted(os.listdir(folder)) == ["escaped.png", "hitcircle.png"]
    assert not os.path.exists(os.path.join(folder, "..", "escaped.png")), (
        "nothing was written beside the store"
    )


def test_an_archive_that_promises_more_than_we_hold_is_refused(tmp_path, monkeypatch):
    """A few hundred kilobytes of zip can be gigabytes of zeroes."""
    monkeypatch.setattr(skins, "MAX_UNPACKED_BYTES", 1024)
    archive = osk(tmp_path, {"hitcircle.png": b"0" * 4096})
    with pytest.raises(skins.SkinRejected, match="МБ"):
        skins.import_osk(archive, "pack.osk")


def test_a_declaration_is_not_taken_on_trust(tmp_path, monkeypatch):
    """The size in a zip header is written by whoever made it. The same
    question is asked again of what actually comes out."""
    monkeypatch.setattr(skins, "MAX_UNPACKED_BYTES", 4096)
    archive = osk(tmp_path, {"hitcircle.png": b"0" * 2048, "cursor.png": b"0" * 2048})
    # Both fit the declared ceiling together only just; lower it under them
    # after the declaration has been read.
    monkeypatch.setattr(skins, "MAX_UNPACKED_BYTES", 3000)
    with pytest.raises(skins.SkinRejected):
        skins.import_osk(archive, "pack.osk")


def test_something_that_is_not_an_archive_says_so(tmp_path):
    path = tmp_path / "not.osk"
    path.write_bytes(b"this is not a zip file")
    with pytest.raises(skins.SkinRejected, match="архив"):
        skins.import_osk(str(path), "not.osk")


def test_an_absurd_number_of_files_is_not_a_skin(tmp_path, monkeypatch):
    monkeypatch.setattr(skins, "MAX_FILES", 3)
    archive = osk(tmp_path, {f"hit{i}.png": b"x" for i in range(5)})
    with pytest.raises(skins.SkinRejected):
        skins.import_osk(archive, "pack.osk")


# ── what a skin folder is ─────────────────────────────────────────────────

def test_an_archive_that_wraps_its_files_in_a_folder_still_works(tmp_path):
    """Most of them do. osu! reads only the top of a skin folder, so left
    nested the engine would find an empty one."""
    archive = osk(tmp_path, {"my skin/hitcircle.png": b"x", "my skin/skin.ini": b"[General]"})
    name = skins.import_osk(archive, "pack.osk")
    files = os.listdir(skins.folder_of(name))
    assert sorted(files) == ["hitcircle.png", "skin.ini"]


def test_only_the_files_the_engine_reads_are_kept(tmp_path):
    """A readme and the author's own sources are weight we would otherwise
    carry to a worker for nothing."""
    archive = osk(tmp_path, {
        "hitcircle.png": b"x",
        "readme.txt": b"hello",
        "source.psd": b"big",
        "hitnormal.wav": b"w",
    })
    name = skins.import_osk(archive, "pack.osk")
    assert sorted(os.listdir(skins.folder_of(name))) == ["hitcircle.png", "hitnormal.wav"]


def test_an_archive_with_nothing_we_read_is_refused(tmp_path):
    archive = osk(tmp_path, {"readme.txt": b"hello"})
    with pytest.raises(skins.SkinRejected):
        skins.import_osk(archive, "pack.osk")


# ── the store ─────────────────────────────────────────────────────────────

def test_a_skin_is_named_after_its_file_and_can_be_found_again(tmp_path):
    archive = osk(tmp_path, {"hitcircle.png": b"x"})
    name = skins.import_osk(archive, "doki dt mix v3.osk")
    assert name == "doki dt mix v3"
    assert skins.available() == [name]
    assert skins.folder_of(name)


def test_a_name_that_is_not_in_the_store_resolves_to_nothing(tmp_path):
    """It arrives from a callback, and a callback is user input. Joining it
    onto the store and hoping is how a path traversal gets a second chance."""
    assert skins.folder_of("../../etc") is None
    assert skins.folder_of("nothing here") is None


def test_sending_the_same_skin_again_replaces_it(tmp_path):
    """Updating a skin is sending the file again; asking somebody to delete it
    first would be a step with no purpose."""
    first = osk(tmp_path, {"hitcircle.png": b"one"}, name="a.osk")
    second = osk(tmp_path, {"cursor.png": b"two"}, name="b.osk")
    skins.import_osk(first, "same.osk")
    skins.import_osk(second, "same.osk")
    assert skins.available() == ["same"]
    assert os.listdir(skins.folder_of("same")) == ["cursor.png"]


def test_a_failed_import_leaves_the_skin_that_was_there(tmp_path):
    """Unpacked into a staging folder and swapped in whole, so a bad archive
    never leaves half a skin somebody can select and render with."""
    good = osk(tmp_path, {"hitcircle.png": b"one"}, name="a.osk")
    skins.import_osk(good, "same.osk")

    bad = osk(tmp_path, {"readme.txt": b"nothing we read"}, name="b.osk")
    with pytest.raises(skins.SkinRejected):
        skins.import_osk(bad, "same.osk")

    assert skins.available() == ["same"]
    assert os.listdir(skins.folder_of("same")) == ["hitcircle.png"]


def test_a_skin_can_be_forgotten(tmp_path):
    skins.import_osk(osk(tmp_path, {"hitcircle.png": b"x"}), "gone.osk")
    assert skins.forget("gone") is True
    assert skins.available() == []
    assert skins.forget("gone") is False


# ── samples the engine can actually read ──────────────────────────────────

def _sample(path: str) -> None:
    """A real, tiny compressed sample, made by the same tool that reads it.

    Written as an `.mp3` rather than the `.ogg` the reported skin ships, for a
    dull reason: not every ffmpeg build can *encode* Vorbis — the one this was
    written on cannot — while every one of them decodes it. Both formats take
    the same path through the code, so the fixture picks the one that can be
    made anywhere.
    """
    import subprocess

    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=0.05", path],
        check=True, capture_output=True,
    )


def test_a_skins_hitsounds_are_converted_to_what_the_engine_reads(tmp_path):
    """Reported: a skin's pictures worked and it had no hitsounds at all.

    It had plenty — as `.ogg`. `dossier-audio` has no dependencies and decodes
    WAV alone, so the engine found one sample in that folder where the skin
    ships fifteen. Converted on the way into the store, with ffmpeg, which a
    render already needs.
    """
    from services.dossier import skins

    _sample(str(tmp_path / "normal-hitnormal.mp3"))
    skins._to_wav(str(tmp_path))

    assert (tmp_path / "normal-hitnormal.wav").exists()
    assert (tmp_path / "normal-hitnormal.mp3").exists(), "the original is left alone"


def test_a_skins_own_wav_is_not_overwritten_by_its_ogg(tmp_path):
    """A skin shipping both has said which it means: osu! would take the
    `.wav`, so converting over it would replace the skin's answer with ours."""
    from services.dossier import skins

    _sample(str(tmp_path / "soft-hitclap.mp3"))
    (tmp_path / "soft-hitclap.wav").write_bytes(b"the skin's own")
    skins._to_wav(str(tmp_path))

    assert (tmp_path / "soft-hitclap.wav").read_bytes() == b"the skin's own"


def test_a_sample_that_will_not_convert_leaves_no_wreckage(tmp_path):
    """A skin with a truncated file — the one this was tested against ships
    four — must still import. Half a `.wav` is worse than none, because the
    engine would read it."""
    from services.dossier import skins

    (tmp_path / "nightcore-kick.ogg").write_bytes(b"")
    skins._to_wav(str(tmp_path))

    assert not (tmp_path / "nightcore-kick.wav").exists()


def test_pictures_are_left_alone(tmp_path):
    from services.dossier import skins

    (tmp_path / "hitcircle.png").write_bytes(b"not really a png")
    skins._to_wav(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["hitcircle.png"]

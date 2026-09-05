import hashlib
import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts import engine

def test_the_tag_is_read_from_the_line_pip_reads(tmp_path):
    named = tmp_path / "requirements.txt"
    named.write_text(
        "aiogram==3.29.1\n"
        "# a comment about the engine\n"
        "dossier @ git+https://github.com/NaumRedlo/Dossier@v0.4.2#subdirectory=client\n"
    )
    assert engine.wanted_tag(str(named)) == "v0.4.2"

def test_this_bot_actually_pins_a_version(tmp_path):
    tag = engine.wanted_tag()
    assert tag.startswith("v"), tag
    assert tag != "main", "the engine is pinned to a branch, not a version"

def test_a_requirements_file_that_names_no_engine_says_so(tmp_path):
    named = tmp_path / "requirements.txt"
    named.write_text("aiogram==3.29.1\n")
    with pytest.raises(engine.Unavailable) as refused:
        engine.wanted_tag(str(named))

    assert "dossier @ git+" in str(refused.value)

def test_a_missing_requirements_file_is_a_sentence_not_a_traceback(tmp_path):
    with pytest.raises(engine.Unavailable):
        engine.wanted_tag(str(tmp_path / "nothing here"))

@pytest.mark.parametrize("system, machine, expected", [
    ("linux", "x86_64", "linux-x64"),
    ("linux", "AMD64", "linux-x64"),
    ("darwin", "arm64", "macos-arm64"),
    ("win32", "AMD64", "windows-x64"),
])
def test_each_machine_gets_the_release_built_for_it(
    monkeypatch, system, machine, expected
):
    monkeypatch.setattr(engine.sys, "platform", system)
    monkeypatch.setattr(engine.platform, "machine", lambda: machine)
    assert engine.slug() == expected

def test_a_machine_nobody_builds_for_is_told_what_is_built(monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.setattr(engine.platform, "machine", lambda: "aarch64")
    with pytest.raises(engine.Unavailable) as refused:
        engine.slug()
    said = str(refused.value)
    assert "linux-x64" in said and "macos-arm64" in said
    assert "cargo build --release" in said

def _an_archive(named: str = "dossier-v1.0.0-linux-x64") -> bytes:
    made = io.BytesIO()
    with zipfile.ZipFile(made, "w") as archive:
        archive.writestr(f"{named}/dossier", b"#!/bin/sh\necho dossier 0.1.0 (abc1234)\n")
        archive.writestr(f"{named}/assets/fonts/keep.txt", b"the font would be here")
    return made.getvalue()

@pytest.fixture
def somewhere(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "HOME", str(tmp_path))
    monkeypatch.setattr(engine, "ENGINES", str(tmp_path / "engines"))
    monkeypatch.setattr(engine, "CURRENT", str(tmp_path / "engine"))
    monkeypatch.setattr(engine, "slug", lambda: "linux-x64")
    return tmp_path

def _served(monkeypatch, body: bytes, digest: str = "") -> list[str]:
    asked = []

    def fetch(url):
        asked.append(url)
        if url.endswith(".sha256"):
            told = digest or hashlib.sha256(body).hexdigest()
            return f"{told}  an-archive.zip\n".encode()
        return body

    monkeypatch.setattr(engine, "_fetch", fetch)
    return asked

def test_a_release_is_unpacked_and_the_binary_is_runnable(somewhere, monkeypatch):
    body = _an_archive()
    _served(monkeypatch, body)
    landing = engine.download("v1.0.0", "v1.0.0-linux-x64")

    binary = os.path.join(landing, "dossier")
    assert os.path.isfile(binary)

    assert os.access(binary, os.X_OK)

    assert os.path.isfile(os.path.join(landing, "assets", "fonts", "keep.txt"))

def test_an_archive_that_does_not_match_its_hash_is_not_unpacked(
    somewhere, monkeypatch
):
    _served(monkeypatch, _an_archive(), digest="0" * 64)
    with pytest.raises(engine.Unavailable) as refused:
        engine.download("v1.0.0", "v1.0.0-linux-x64")

    assert "не совпало" in str(refused.value)
    assert not os.path.exists(os.path.join(str(somewhere), "engines", "v1.0.0-linux-x64"))

def test_the_hash_is_fetched_before_anything_is_written(somewhere, monkeypatch):
    asked = _served(monkeypatch, _an_archive())
    engine.download("v1.0.0", "v1.0.0-linux-x64")
    assert any(url.endswith(".sha256") for url in asked), asked

def test_a_missing_hash_file_stops_it_rather_than_being_shrugged_off(
    somewhere, monkeypatch
):
    monkeypatch.setattr(engine, "_fetch", lambda url: b"" if url.endswith(".sha256") else _an_archive())
    with pytest.raises(engine.Unavailable) as refused:
        engine.download("v1.0.0", "v1.0.0-linux-x64")
    assert "нечем проверить" in str(refused.value)

def test_the_link_is_never_absent_while_it_moves(somewhere):
    first = somewhere / "engines" / "v1"
    second = somewhere / "engines" / "v2"
    for made in (first, second):
        made.mkdir(parents=True)

    engine.point_at(str(first))
    assert os.path.realpath(engine.CURRENT) == str(first)

    engine.point_at(str(second))
    assert os.path.realpath(engine.CURRENT) == str(second)
    assert engine.installed() == "v2"

def test_the_old_version_is_kept_so_going_back_is_a_link(somewhere, monkeypatch):
    _served(monkeypatch, _an_archive("dossier-v1.0.0-linux-x64"))
    engine.point_at(engine.download("v1.0.0", "v1.0.0-linux-x64"))
    _served(monkeypatch, _an_archive("dossier-v2.0.0-linux-x64"))
    engine.point_at(engine.download("v2.0.0", "v2.0.0-linux-x64"))

    kept = sorted(os.listdir(engine.ENGINES))
    assert kept == ["v1.0.0-linux-x64", "v2.0.0-linux-x64"]
    assert engine.installed() == "v2.0.0-linux-x64"

def test_a_host_already_on_the_right_version_fetches_no_archive(
    somewhere, monkeypatch, tmp_path
):
    monkeypatch.setattr(engine, "wanted_tag", lambda *_a: "v1.0.0")
    _served(monkeypatch, _an_archive())
    engine.ensure()

    asked = _served(monkeypatch, _an_archive())
    assert engine.ensure().endswith("dossier")
    assert not [url for url in asked if url.endswith(".zip")], asked

def test_force_downloads_again_anyway(somewhere, monkeypatch):
    monkeypatch.setattr(engine, "wanted_tag", lambda *_a: "v1.0.0")
    _served(monkeypatch, _an_archive())
    engine.ensure()
    asked = _served(monkeypatch, _an_archive())
    engine.ensure(force=True)
    assert asked, "--force did not fetch"

def test_a_link_pointing_at_a_folder_with_no_binary_is_replaced(
    somewhere, monkeypatch
):
    monkeypatch.setattr(engine, "wanted_tag", lambda *_a: "v1.0.0")
    hollow = somewhere / "engines" / "v1.0.0-linux-x64"
    hollow.mkdir(parents=True)
    engine.point_at(str(hollow))

    _served(monkeypatch, _an_archive())
    assert os.path.isfile(engine.ensure())

def test_a_tag_that_moved_is_noticed_rather_than_assumed_fixed(
    somewhere, monkeypatch
):
    monkeypatch.setattr(engine, "wanted_tag", lambda *_a: "v1.0.0")
    _served(monkeypatch, _an_archive())
    engine.ensure()

    moved = _an_archive("dossier-v1.0.0-linux-x64") + b"\x00 and one byte more"
    asked = _served(monkeypatch, moved)
    engine.ensure()
    assert any(url.endswith(".zip") for url in asked), "it did not fetch again"
    assert engine._here("v1.0.0-linux-x64") == hashlib.sha256(moved).hexdigest()

def test_an_unchanged_tag_still_costs_only_a_hash(somewhere, monkeypatch):
    monkeypatch.setattr(engine, "wanted_tag", lambda *_a: "v1.0.0")
    _served(monkeypatch, _an_archive())
    engine.ensure()

    asked = _served(monkeypatch, _an_archive())
    engine.ensure()
    assert asked == [
        "https://github.com/NaumRedlo/Dossier/releases/download/v1.0.0/"
        "dossier-v1.0.0-linux-x64.zip.sha256"
    ], asked

def test_a_release_it_cannot_ask_about_is_left_alone(somewhere, monkeypatch):
    monkeypatch.setattr(engine, "wanted_tag", lambda *_a: "v1.0.0")
    _served(monkeypatch, _an_archive())
    engine.ensure()

    def unreachable(_url):
        raise engine.Unavailable("no network")

    monkeypatch.setattr(engine, "_fetch", unreachable)
    assert os.path.isfile(engine.ensure())

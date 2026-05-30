"""Unit tests for services.map_import.parser.parse_import_target.

Covers:
  - osu! URL forms (beatmap, beatmapset, specific-diff, short forms)
  - bare ID heuristic
  - Google Drive shareable / direct uc?id= / folder (unsupported)
  - MediaFire page / direct CDN host / domain w/o /file/ (unsupported)
  - Direct archive URLs (.zip / .osz / .7z / .rar)
  - Mega — unsupported
  - Garbage tokens — UNKNOWN
"""

from __future__ import annotations

from services.map_import.parser import (
    ImportTarget,
    TargetKind,
    parse_import_target,
    parse_many,
)


# ── osu! URL forms ─────────────────────────────────────────────────────────


def test_beatmapset_url():
    t = parse_import_target("https://osu.ppy.sh/beatmapsets/789")
    assert t.kind == TargetKind.BEATMAPSET
    assert t.id == 789


def test_beatmapset_with_specific_diff():
    t = parse_import_target("https://osu.ppy.sh/beatmapsets/789#osu/123")
    assert t.kind == TargetKind.BEATMAP
    assert t.id == 123


def test_beatmaps_new_form():
    t = parse_import_target("https://osu.ppy.sh/beatmaps/456")
    assert t.kind == TargetKind.BEATMAP
    assert t.id == 456


def test_b_short_form():
    t = parse_import_target("https://osu.ppy.sh/b/12345")
    assert t.kind == TargetKind.BEATMAP
    assert t.id == 12345


def test_bare_id_defaults_to_beatmap():
    t = parse_import_target("987654")
    assert t.kind == TargetKind.BEATMAP
    assert t.id == 987654


# ── Google Drive ───────────────────────────────────────────────────────────


def test_gdrive_file_d_view_rewrites_to_direct():
    raw = "https://drive.google.com/file/d/abc123XYZ/view?usp=sharing"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.FILE_URL
    assert t.raw == raw
    assert t.download_url == (
        "https://drive.google.com/uc?export=download&id=abc123XYZ"
    )
    assert t.scrape is None


def test_gdrive_uc_with_id_kept_as_direct():
    raw = "https://drive.google.com/uc?id=DEF456&export=download"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.FILE_URL
    # Parser normalises to the canonical form.
    assert t.download_url == (
        "https://drive.google.com/uc?export=download&id=DEF456"
    )


def test_gdrive_folder_or_other_unsupported():
    raw = "https://drive.google.com/drive/folders/SOMEFOLDER"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.UNSUPPORTED
    assert "Google Drive" in (t.reason or "")


# ── MediaFire ─────────────────────────────────────────────────────────────


def test_mediafire_file_page_marks_scrape():
    raw = "https://www.mediafire.com/file/abc123/maps.zip/file"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.FILE_URL
    assert t.scrape == "mediafire"
    assert t.download_url == raw


def test_mediafire_direct_cdn_host_no_scrape():
    raw = "https://download1234.mediafire.com/abcd/file/maps.zip"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.FILE_URL
    assert t.scrape is None
    assert t.download_url == raw


def test_mediafire_unknown_path_unsupported():
    raw = "https://www.mediafire.com/somewhere/else"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.UNSUPPORTED
    assert "MediaFire" in (t.reason or "")


# ── Mega ──────────────────────────────────────────────────────────────────


def test_mega_nz_unsupported():
    raw = "https://mega.nz/file/abc#xyz"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.UNSUPPORTED
    assert "Mega" in (t.reason or "")


def test_mega_io_unsupported():
    raw = "https://mega.io/folder/abc"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.UNSUPPORTED


# ── Direct archive links ──────────────────────────────────────────────────


def test_direct_zip_url():
    raw = "https://files.example.com/dump/mappack.zip"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.FILE_URL
    assert t.download_url == raw
    assert t.scrape is None


def test_direct_osz_url():
    raw = "https://cdn.example.com/maps/beatmap.osz"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.FILE_URL


def test_direct_7z_url():
    raw = "https://example.com/archive.7z"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.FILE_URL


def test_url_with_query_string_kept():
    raw = "https://example.com/maps.zip?token=abc&v=1"
    t = parse_import_target(raw)
    assert t.kind == TargetKind.FILE_URL
    assert t.download_url == raw


def test_non_archive_url_is_unknown():
    # Plain HTML page with no recognised hint — bubbles up as UNKNOWN.
    t = parse_import_target("https://example.com/some-page.html")
    assert t.kind == TargetKind.UNKNOWN


# ── Garbage / edge cases ──────────────────────────────────────────────────


def test_empty_string_unknown():
    t = parse_import_target("")
    assert t.kind == TargetKind.UNKNOWN


def test_whitespace_only_unknown():
    t = parse_import_target("   ")
    assert t.kind == TargetKind.UNKNOWN


def test_garbage_unknown():
    t = parse_import_target("hello world")
    assert t.kind == TargetKind.UNKNOWN


# ── parse_many ────────────────────────────────────────────────────────────


def test_parse_many_mixed_sources():
    text = (
        "123456 "
        "https://osu.ppy.sh/beatmapsets/789 "
        "https://drive.google.com/file/d/ABC/view "
        "https://mega.nz/file/X"
    )
    results = parse_many(text)
    assert len(results) == 4
    assert results[0].kind == TargetKind.BEATMAP
    assert results[1].kind == TargetKind.BEATMAPSET
    assert results[2].kind == TargetKind.FILE_URL
    assert results[3].kind == TargetKind.UNSUPPORTED


def test_parse_many_commas_and_whitespace():
    text = "123,456\nhttps://osu.ppy.sh/b/789"
    results = parse_many(text)
    assert len(results) == 3
    assert all(r.kind == TargetKind.BEATMAP for r in results)

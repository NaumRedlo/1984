"""GPU render mode: NVENC spatch branch and the fit-to-size guard
(utils/osu/danser_renderer). No GPU or ffmpeg is exercised here."""

import json
import os

from utils.osu import danser_renderer as dr


def test_spatch_cpu_uses_libx264_and_user_resolution(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", False)
    patch = json.loads(dr._build_spatch({"resolution": "960x540"}))
    rec = patch["Recording"]
    assert rec["Encoder"] == "libx264"
    assert "libx264" in rec
    assert rec["FrameWidth"] == 960 and rec["FrameHeight"] == 540
    assert rec["FPS"] == 60


def test_spatch_gpu_default_1080p(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_HEVC", False)
    monkeypatch.setattr(dr, "RENDER_GPU_RESOLUTION", "1920x1080")
    # No settings -> the GPU mode default resolution.
    patch = json.loads(dr._build_spatch(None))
    rec = patch["Recording"]
    assert rec["Encoder"] == "h264_nvenc"
    assert rec["h264_nvenc"]["RateControl"] == "cq"
    assert rec["FrameWidth"] == 1920 and rec["FrameHeight"] == 1080
    assert rec["FPS"] == 60


def test_spatch_gpu_honors_user_resolution(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_HEVC", False)
    monkeypatch.setattr(dr, "RENDER_GPU_RESOLUTION", "1920x1080")
    # The /settings menu lets users drop to 720p even in GPU mode.
    patch = json.loads(dr._build_spatch({"resolution": "1280x720"}))
    assert patch["Recording"]["FrameWidth"] == 1280
    assert patch["Recording"]["FrameHeight"] == 720


def test_spatch_cpu_clamps_to_720(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", False)
    # 1080p on the CPU box is impractical — clamp down.
    patch = json.loads(dr._build_spatch({"resolution": "1920x1080"}))
    assert patch["Recording"]["FrameHeight"] == 720


def test_spatch_applies_hud_toggles(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_HEVC", False)
    patch = json.loads(dr._build_spatch({
        "resolution": "1920x1080",
        "show_pp_counter": False,
        "show_scoreboard": True,
        "show_result_screen": False,
        "show_strain_graph": False,
        "show_hit_counter": False,
        "show_seizure_warning": False,
        "bg_dim": 40,
        "cursor_size": 1.5,
    }))
    g = patch["Gameplay"]
    assert g["PPCounter"]["Show"] is False
    assert g["ScoreBoard"]["Show"] is True
    assert g["ShowResultsScreen"] is False
    assert g["StrainGraph"]["Show"] is False
    assert g["HitCounter"]["Show"] is False
    assert patch["Playfield"]["SeizureWarning"]["Enabled"] is False
    assert patch["Playfield"]["Background"]["Dim"]["Normal"] == 0.4
    # Default cursor is sized by Cursor.CursorSize (base 12), not Skin.Cursor.Scale.
    assert patch["Cursor"]["CursorSize"] == 18


def test_spatch_gpu_single_pass_bitrate(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_HEVC", False)
    monkeypatch.setattr(dr, "RENDER_FIT_MAX_MB", 50)
    # A known length -> render straight to a size-targeted bitrate (single pass),
    # so the fit re-encode is skipped.
    patch = json.loads(dr._build_spatch({"resolution": "1920x1080", "length_seconds": 120}))
    rec = patch["Recording"]["h264_nvenc"]
    assert rec["RateControl"] == "vbr"
    assert rec["Bitrate"].endswith("k")
    assert "CQ" not in rec


def test_spatch_gpu_cq_without_length(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_HEVC", False)
    monkeypatch.setattr(dr, "RENDER_FIT_MAX_MB", 50)
    # No length -> can't size a bitrate -> stay on quality-targeted CQ.
    patch = json.loads(dr._build_spatch({"resolution": "1920x1080"}))
    assert patch["Recording"]["h264_nvenc"]["RateControl"] == "cq"


def test_target_bitrate_scales_with_length(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_FIT_MAX_MB", 50)
    # A longer map must get a lower bitrate to fit the same cap.
    assert dr._target_video_kbps(300) < dr._target_video_kbps(60)


def test_spatch_skin_hitsounds_toggle(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_HEVC", False)
    on = json.loads(dr._build_spatch({"resolution": "1920x1080", "use_skin_hitsounds": True}))
    off = json.loads(dr._build_spatch({"resolution": "1920x1080", "use_skin_hitsounds": False}))
    assert on["Audio"]["IgnoreBeatmapSamples"] is True
    assert off["Audio"]["IgnoreBeatmapSamples"] is False


def test_spatch_gpu_hevc(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_HEVC", True)
    monkeypatch.setattr(dr, "RENDER_GPU_RESOLUTION", "1920x1080")
    patch = json.loads(dr._build_spatch(None))
    rec = patch["Recording"]
    assert rec["Encoder"] == "hevc_nvenc"
    assert "hevc_nvenc" in rec
    assert "h264_nvenc" not in rec


def test_spatch_disables_heavy_effects(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", False)
    patch = json.loads(dr._build_spatch(None))
    bg = patch["Playfield"]["Background"]
    assert bg["LoadStoryboards"] is False
    assert patch["Playfield"]["Bloom"]["Enabled"] is False


async def test_fit_noop_when_under_cap(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 1000)
    out = await dr.fit_video_to_size(str(f), max_bytes=10_000, gpu=False)
    assert out == str(f)            # untouched
    assert f.read_bytes() == b"x" * 1000


def test_sanitize_skin_name():
    assert dr.sanitize_skin_name("My Cool Skin.osk") == "My Cool Skin"
    # basename strips the path -> no traversal possible
    assert dr.sanitize_skin_name("../../etc/passwd") == "passwd"
    assert dr.sanitize_skin_name("") == ""
    for bad in ("../../etc/passwd", "a/b/c", "x\\y"):
        out = dr.sanitize_skin_name(bad)
        assert "/" not in out and ".." not in out


def test_install_skin_unpacks(monkeypatch, tmp_path):
    import io, zipfile
    monkeypatch.setattr(dr, "DANSER_SKINS_DIR", str(tmp_path))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skin.ini", "[General]\nName: Test")
        zf.writestr("cursor.png", b"\x89PNG")
    name = dr.install_skin(buf.getvalue(), "Test Skin")
    assert name == "Test Skin"
    assert (tmp_path / "Test Skin" / "skin.ini").is_file()
    assert (tmp_path / "Test Skin" / "cursor.png").is_file()
    assert "Test Skin" in dr.list_skins()


def test_install_skin_rejects_zip_slip(monkeypatch, tmp_path):
    import io, zipfile, pytest
    monkeypatch.setattr(dr, "DANSER_SKINS_DIR", str(tmp_path))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "pwned")
    with pytest.raises(dr.DanserError):
        dr.install_skin(buf.getvalue(), "Evil")
    assert not (tmp_path.parent / "evil.txt").exists()


def test_spatch_applies_skin(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_HEVC", False)
    patch = json.loads(dr._build_spatch({
        "resolution": "1920x1080", "skin": "MySkin", "cursor_size": 1.2,
    }))
    assert patch["Skin"]["CurrentSkin"] == "MySkin"
    # A non-default skin must drive its own cursor/colours, else danser draws
    # its own over the skin.
    assert patch["Skin"]["UseColorsFromSkin"] is True
    assert patch["Skin"]["Cursor"]["UseSkinCursor"] is True
    assert patch["Skin"]["Cursor"]["Scale"] == 1.2
    assert patch["Objects"]["Colors"]["UseSkinComboColors"] is True
    # The default-cursor key must NOT be set when a skin cursor is in use.
    assert "Cursor" not in patch
    # Playfield outline is danser's overlay — always off.
    assert patch["Gameplay"]["Boundaries"]["Enabled"] is False


def test_spatch_default_skin_keeps_danser_cursor(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_HEVC", False)
    patch = json.loads(dr._build_spatch({
        "resolution": "1920x1080", "skin": "default", "cursor_size": 1.5,
    }))
    assert patch["Skin"]["CurrentSkin"] == "default"
    assert "UseColorsFromSkin" not in patch["Skin"]
    assert "Objects" not in patch
    assert patch["Cursor"]["CursorSize"] == 18


async def test_fit_noop_when_disabled(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 5000)
    out = await dr.fit_video_to_size(str(f), max_bytes=0, gpu=False)
    assert out == str(f)            # max_bytes<=0 disables the fit


async def test_fit_iterates_until_under_cap(monkeypatch, tmp_path):
    max_bytes = 5_000_000
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x" * 7_000_000)  # over the cap -> fit engages

    async def fake_probe(p):
        return 1920, 1080, 20  # 20s

    # Simulate NVENC VBR overshooting its target bitrate by 1.5x — enough to
    # exceed the cap even at the conservative _FIT_SAFETY, forcing a retry.
    calls = []

    async def fake_encode(s, out, video_kbps, gpu):
        calls.append(video_kbps)
        size = int(video_kbps * 1000 / 8 * 20 * 1.5)
        with open(out, "wb") as f:
            f.write(b"0" * size)
        return True

    monkeypatch.setattr(dr, "probe_video", fake_probe)
    monkeypatch.setattr(dr, "_encode_at_bitrate", fake_encode)

    result = await dr.fit_video_to_size(str(src), max_bytes, gpu=True)
    assert os.path.getsize(result) <= max_bytes   # landed under the cap
    assert len(calls) >= 2                          # took at least one correction
    assert calls[1] < calls[0]                      # bitrate scaled down

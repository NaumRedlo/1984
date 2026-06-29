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
        "bg_dim": 40,
        "cursor_size": 1.2,
    }))
    g = patch["Gameplay"]
    assert g["PPCounter"]["Show"] is False
    assert g["ScoreBoard"]["Show"] is True
    assert g["ShowResultsScreen"] is False
    assert patch["Playfield"]["Background"]["Dim"]["Normal"] == 0.4
    assert patch["Skin"]["Cursor"]["Scale"] == 1.2


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

    # Simulate NVENC VBR overshooting its target bitrate by 1.3x.
    calls = []

    async def fake_encode(s, out, video_kbps, gpu):
        calls.append(video_kbps)
        size = int(video_kbps * 1000 / 8 * 20 * 1.3)
        with open(out, "wb") as f:
            f.write(b"0" * size)
        return True

    monkeypatch.setattr(dr, "probe_video", fake_probe)
    monkeypatch.setattr(dr, "_encode_at_bitrate", fake_encode)

    result = await dr.fit_video_to_size(str(src), max_bytes, gpu=True)
    assert os.path.getsize(result) <= max_bytes   # landed under the cap
    assert len(calls) >= 2                          # took at least one correction
    assert calls[1] < calls[0]                      # bitrate scaled down

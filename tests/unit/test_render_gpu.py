"""GPU render mode: NVENC spatch branch and the fit-to-size guard
(utils/osu/danser_renderer). No GPU or ffmpeg is exercised here."""

import json

from utils.osu import danser_renderer as dr


def test_spatch_cpu_uses_libx264_and_user_resolution(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", False)
    patch = json.loads(dr._build_spatch({"resolution": "960x540"}))
    rec = patch["Recording"]
    assert rec["Encoder"] == "libx264"
    assert "libx264" in rec
    assert rec["FrameWidth"] == 960 and rec["FrameHeight"] == 540
    assert rec["FPS"] == 60


def test_spatch_gpu_uses_nvenc_and_1080p(monkeypatch):
    monkeypatch.setattr(dr, "RENDER_GPU", True)
    monkeypatch.setattr(dr, "RENDER_GPU_RESOLUTION", "1920x1080")
    # In GPU mode the per-user CPU-era resolution is ignored in favour of 1080p.
    patch = json.loads(dr._build_spatch({"resolution": "960x540"}))
    rec = patch["Recording"]
    assert rec["Encoder"] == "h264_nvenc"
    assert rec["h264_nvenc"]["RateControl"] == "cq"
    assert rec["FrameWidth"] == 1920 and rec["FrameHeight"] == 1080
    assert rec["FPS"] == 60


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

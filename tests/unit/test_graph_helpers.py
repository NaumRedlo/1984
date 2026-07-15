"""The unified graph standard (services/image/base.py): _smooth_points
(Catmull-Rom densification) and _aa_graph_curve (supersampled+LANCZOS-
downscaled line/fill render) — shared by every card's line graph
(profile.py's rank history, recent.py/map_card.py's strain graph) so a
graph's smoothing/anti-aliasing is defined once, not per renderer."""

from PIL import Image

from services.image.core import CardRenderer


def test_smooth_points_passes_through_original_samples():
    """Catmull-Rom interpolates THROUGH every input point — smoothing must
    not distort the underlying data, only densify between samples."""
    r = CardRenderer()
    pts = [(0, 10), (10, 40), (20, 5), (30, 30), (40, 20)]
    smooth = r._smooth_points(pts, samples_per_segment=8)
    # Every original point sits at an exact multiple of samples_per_segment
    # in the resampled output.
    for i, p in enumerate(pts):
        j = i * 8
        sx, sy = smooth[j]
        assert abs(sx - p[0]) < 1e-6
        assert abs(sy - p[1]) < 1e-6


def test_smooth_points_densifies():
    r = CardRenderer()
    pts = [(0, 0), (10, 10), (20, 0), (30, 10)]
    smooth = r._smooth_points(pts, samples_per_segment=8)
    assert len(smooth) > len(pts)


def test_smooth_points_under_three_returns_unchanged():
    r = CardRenderer()
    assert r._smooth_points([]) == []
    assert r._smooth_points([(1, 2)]) == [(1, 2)]
    assert r._smooth_points([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]


def test_aa_graph_curve_renders_without_crashing():
    r = CardRenderer()
    img = Image.new("RGB", (200, 100), (10, 10, 15))
    pts = r._smooth_points([(10, 80), (60, 20), (110, 60), (160, 30), (190, 50)])
    r._aa_graph_curve(img, 10, 10, 180, 80, pts,
                      line_color=(230, 90, 90), line_width=3, fill_color=(230, 90, 90, 60))
    assert img.size == (200, 100)


def test_aa_graph_curve_handles_degenerate_input():
    """Fewer than 2 points, or a zero-size plot box, must no-op rather than crash."""
    r = CardRenderer()
    img = Image.new("RGB", (100, 100), (0, 0, 0))
    r._aa_graph_curve(img, 0, 0, 50, 50, [], line_color=(255, 0, 0))
    r._aa_graph_curve(img, 0, 0, 50, 50, [(1, 1)], line_color=(255, 0, 0))
    r._aa_graph_curve(img, 0, 0, 0, 0, [(1, 1), (2, 2)], line_color=(255, 0, 0))

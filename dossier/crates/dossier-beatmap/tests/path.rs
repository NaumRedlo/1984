//! Slider path tests.
//!
//! Cases are built from shapes whose answer is known analytically — a straight
//! line, a semicircle, a symmetric quadratic — so an assertion failure means
//! the maths is wrong rather than "the curve moved a bit".

use dossier_beatmap::{CurveType, Point, SliderPath};

const EPS: f64 = 1e-6;

fn p(x: f64, y: f64) -> Point {
    Point { x, y }
}

fn assert_close(a: f64, b: f64, tol: f64, what: &str) {
    assert!((a - b).abs() <= tol, "{what}: {a} vs {b} (tol {tol})");
}

fn assert_point_close(got: Point, want: Point, tol: f64, what: &str) {
    assert!(
        got.x.sub_check(want.x, tol) && got.y.sub_check(want.y, tol),
        "{what}: ({}, {}) vs ({}, {})",
        got.x,
        got.y,
        want.x,
        want.y
    );
}

trait Close {
    fn sub_check(self, other: f64, tol: f64) -> bool;
}
impl Close for f64 {
    fn sub_check(self, other: f64, tol: f64) -> bool {
        (self - other).abs() <= tol
    }
}

// ── linear ───────────────────────────────────────────────────────────────

#[test]
fn a_straight_slider_has_the_length_of_its_line() {
    let path = SliderPath::new(CurveType::Linear, &[p(0.0, 0.0), p(300.0, 400.0)], None);
    assert_close(path.length(), 500.0, EPS, "3-4-5 triangle");
    assert_point_close(
        path.position_at(0.5).unwrap(),
        p(150.0, 200.0),
        EPS,
        "midpoint",
    );
    assert_point_close(path.position_at(0.0).unwrap(), p(0.0, 0.0), EPS, "start");
    assert_point_close(path.position_at(1.0).unwrap(), p(300.0, 400.0), EPS, "end");
}

#[test]
fn progress_outside_the_path_is_clamped() {
    let path = SliderPath::new(CurveType::Linear, &[p(0.0, 0.0), p(100.0, 0.0)], None);
    assert_point_close(
        path.position_at(-5.0).unwrap(),
        p(0.0, 0.0),
        EPS,
        "before start",
    );
    assert_point_close(
        path.position_at(9.0).unwrap(),
        p(100.0, 0.0),
        EPS,
        "past end",
    );
}

#[test]
fn a_multi_segment_line_measures_the_whole_chain() {
    let path = SliderPath::new(
        CurveType::Linear,
        &[p(0.0, 0.0), p(100.0, 0.0), p(100.0, 100.0)],
        None,
    );
    assert_close(path.length(), 200.0, EPS, "two 100px legs");
    // Halfway is the corner.
    assert_point_close(path.position_at(0.5).unwrap(), p(100.0, 0.0), EPS, "corner");
}

// ── the authored length wins ─────────────────────────────────────────────

#[test]
fn the_path_is_trimmed_to_the_length_the_map_authored() {
    // Geometry says 200px; the map says 120. osu! walks only 120.
    let path = SliderPath::new(
        CurveType::Linear,
        &[p(0.0, 0.0), p(200.0, 0.0)],
        Some(120.0),
    );
    assert_close(path.length(), 120.0, EPS, "trimmed length");
    assert_point_close(
        path.position_at(1.0).unwrap(),
        p(120.0, 0.0),
        EPS,
        "new end",
    );
    assert_point_close(
        path.position_at(0.5).unwrap(),
        p(60.0, 0.0),
        EPS,
        "new midpoint",
    );
}

#[test]
fn a_length_beyond_the_geometry_is_extrapolated_not_clamped() {
    // This test asserted the opposite, and was wrong. osu! stretches the last
    // segment to meet the authored length; it does not stop the ball at the
    // end of the drawn curve. Clamping cost a full-combo play 86 combo on an
    // old map whose sliders draw half the length they declare.
    let path = SliderPath::new(
        CurveType::Linear,
        &[p(0.0, 0.0), p(100.0, 0.0)],
        Some(999.0),
    );
    assert_close(
        path.length(),
        999.0,
        EPS,
        "stretched to the authored length",
    );
    assert_point_close(path.position_at(999.0).unwrap(), p(999.0, 0.0), EPS, "end");
}

#[test]
fn a_zero_length_slider_collapses_to_its_start() {
    let path = SliderPath::new(
        CurveType::Linear,
        &[p(10.0, 10.0), p(90.0, 10.0)],
        Some(0.0),
    );
    assert_eq!(path.length(), 0.0);
    assert_point_close(path.position_at(0.7).unwrap(), p(10.0, 10.0), EPS, "start");
}

// ── bezier ───────────────────────────────────────────────────────────────

#[test]
fn a_quadratic_bezier_passes_through_its_analytic_midpoint() {
    // For control points A, B, C the curve at t=0.5 is (A + 2B + C) / 4.
    let (a, b, c) = (p(0.0, 0.0), p(100.0, 100.0), p(200.0, 0.0));
    let path = SliderPath::new(CurveType::Bezier, &[a, b, c], None);

    let want = p((a.x + 2.0 * b.x + c.x) / 4.0, (a.y + 2.0 * b.y + c.y) / 4.0);
    // The path is arc-length parameterised, and this curve is symmetric, so
    // half the distance is also the t=0.5 point.
    assert_point_close(path.position_at(0.5).unwrap(), want, 0.5, "bezier midpoint");
    assert_point_close(path.position_at(0.0).unwrap(), a, EPS, "start");
    assert_point_close(path.position_at(1.0).unwrap(), c, EPS, "end");
}

#[test]
fn a_bezier_bulges_away_from_its_chord() {
    let path = SliderPath::new(
        CurveType::Bezier,
        &[p(0.0, 0.0), p(100.0, 100.0), p(200.0, 0.0)],
        None,
    );
    // The curve rises off the y=0 chord but never reaches the control point.
    let mid = path.position_at(0.5).unwrap();
    assert!(mid.y > 10.0 && mid.y < 100.0, "midpoint y = {}", mid.y);
    // And it is longer than the chord, shorter than the control polygon.
    let polygon = 2.0 * (100.0f64.hypot(100.0));
    assert!(path.length() > 200.0 && path.length() < polygon);
}

#[test]
fn a_repeated_control_point_starts_a_new_bezier_segment() {
    // Maps encode a sharp corner by duplicating a point (a red anchor). The
    // corner must stay sharp rather than being smoothed across.
    let corner = p(100.0, 0.0);
    let path = SliderPath::new(
        CurveType::Bezier,
        &[p(0.0, 0.0), corner, corner, p(100.0, 100.0)],
        None,
    );
    assert_close(path.length(), 200.0, 0.5, "two straight legs");
    assert_point_close(path.position_at(0.5).unwrap(), corner, 0.5, "the corner");
}

// ── perfect circle ───────────────────────────────────────────────────────

#[test]
fn a_semicircle_has_the_arc_length_of_a_semicircle() {
    // Through (0,0), (50,50), (100,0): a half turn of radius 50.
    let path = SliderPath::new(
        CurveType::PerfectCircle,
        &[p(0.0, 0.0), p(50.0, 50.0), p(100.0, 0.0)],
        None,
    );
    assert_close(path.length(), std::f64::consts::PI * 50.0, 0.5, "π·r");
    assert_point_close(path.position_at(0.5).unwrap(), p(50.0, 50.0), 0.5, "apex");
    assert_point_close(path.position_at(1.0).unwrap(), p(100.0, 0.0), 0.5, "end");
}

#[test]
fn the_arc_bends_the_way_the_middle_point_says() {
    let up = SliderPath::new(
        CurveType::PerfectCircle,
        &[p(0.0, 0.0), p(50.0, 50.0), p(100.0, 0.0)],
        None,
    );
    let down = SliderPath::new(
        CurveType::PerfectCircle,
        &[p(0.0, 0.0), p(50.0, -50.0), p(100.0, 0.0)],
        None,
    );
    assert!(up.position_at(0.5).unwrap().y > 0.0);
    assert!(down.position_at(0.5).unwrap().y < 0.0);
}

#[test]
fn collinear_perfect_circle_points_fall_back_to_a_bezier() {
    // Three points on a line have no circumcircle. The game doesn't reject the
    // slider — it draws it as a bezier — so neither do we.
    let path = SliderPath::new(
        CurveType::PerfectCircle,
        &[p(0.0, 0.0), p(50.0, 0.0), p(100.0, 0.0)],
        None,
    );
    assert!(!path.is_empty());
    assert_close(path.length(), 100.0, 0.5, "straight line");
    assert_point_close(
        path.position_at(0.5).unwrap(),
        p(50.0, 0.0),
        0.5,
        "midpoint",
    );
}

#[test]
fn a_perfect_curve_with_more_than_three_points_falls_back_too() {
    let path = SliderPath::new(
        CurveType::PerfectCircle,
        &[p(0.0, 0.0), p(50.0, 50.0), p(100.0, 0.0), p(150.0, 50.0)],
        None,
    );
    assert!(!path.is_empty());
    assert!(path.length() > 0.0);
}

// ── catmull ──────────────────────────────────────────────────────────────

#[test]
fn a_catmull_path_runs_through_its_control_points() {
    let path = SliderPath::new(
        CurveType::Catmull,
        &[p(0.0, 0.0), p(50.0, 50.0), p(100.0, 0.0)],
        None,
    );
    assert_point_close(path.position_at(0.0).unwrap(), p(0.0, 0.0), EPS, "start");
    assert_point_close(path.position_at(1.0).unwrap(), p(100.0, 0.0), 1.0, "end");
    assert!(path.length() > 100.0, "curved, so longer than the chord");
}

// ── repeats ──────────────────────────────────────────────────────────────

#[test]
fn repeat_sliders_bounce_back_along_the_path() {
    let path = SliderPath::new(CurveType::Linear, &[p(0.0, 0.0), p(100.0, 0.0)], None);

    // Slide 0 runs forward, slide 1 runs back, slide 2 forward again.
    assert_point_close(
        path.position_at_slide(0.5, 3).unwrap(),
        p(50.0, 0.0),
        EPS,
        "out",
    );
    assert_point_close(
        path.position_at_slide(1.0, 3).unwrap(),
        p(100.0, 0.0),
        EPS,
        "far end",
    );
    assert_point_close(
        path.position_at_slide(1.5, 3).unwrap(),
        p(50.0, 0.0),
        EPS,
        "back",
    );
    assert_point_close(
        path.position_at_slide(2.0, 3).unwrap(),
        p(0.0, 0.0),
        EPS,
        "home",
    );
    assert_point_close(
        path.position_at_slide(2.5, 3).unwrap(),
        p(50.0, 0.0),
        EPS,
        "out again",
    );
    assert_point_close(
        path.position_at_slide(3.0, 3).unwrap(),
        p(100.0, 0.0),
        EPS,
        "finish",
    );
}

#[test]
fn a_single_slide_never_reverses() {
    let path = SliderPath::new(CurveType::Linear, &[p(0.0, 0.0), p(100.0, 0.0)], None);
    assert_point_close(
        path.position_at_slide(1.0, 1).unwrap(),
        p(100.0, 0.0),
        EPS,
        "end",
    );
    // Past the end it stays put rather than bouncing.
    assert_point_close(
        path.position_at_slide(5.0, 1).unwrap(),
        p(100.0, 0.0),
        EPS,
        "clamped",
    );
}

// ── degenerate input ─────────────────────────────────────────────────────

#[test]
fn a_single_control_point_yields_a_point_path() {
    let path = SliderPath::new(CurveType::Bezier, &[p(42.0, 7.0)], None);
    assert_eq!(path.length(), 0.0);
    assert_point_close(
        path.position_at(0.5).unwrap(),
        p(42.0, 7.0),
        EPS,
        "the point",
    );
}

#[test]
fn no_control_points_yields_an_empty_path() {
    let path = SliderPath::new(CurveType::Bezier, &[], None);
    assert!(path.is_empty());
    assert_eq!(path.position_at(0.5), None);
    assert_eq!(path.length(), 0.0);
}

#[test]
fn identical_control_points_terminate_instead_of_recursing_forever() {
    // A degenerate polygon never looks "flat enough" by curvature alone; the
    // depth guard is what stops subdivision from blowing the stack.
    let path = SliderPath::new(
        CurveType::Bezier,
        &[p(10.0, 10.0), p(10.0, 10.0), p(10.0, 10.0)],
        None,
    );
    assert_close(path.length(), 0.0, EPS, "no distance covered");
}

#[test]
fn non_finite_coordinates_are_dropped_rather_than_poisoning_the_path() {
    let path = SliderPath::new(
        CurveType::Linear,
        &[p(0.0, 0.0), p(f64::NAN, 5.0), p(100.0, 0.0)],
        None,
    );
    assert_close(path.length(), 100.0, EPS, "the NaN point is skipped");
}

// ── segments, for drawing a slider that is still growing ─────────────────

#[test]
fn a_segment_of_the_whole_path_keeps_every_point() {
    let path = SliderPath::new(
        CurveType::Linear,
        &[p(0.0, 0.0), p(100.0, 0.0), p(100.0, 100.0)],
        Some(200.0),
    );
    let (start, interior, end) = path.segment(0.0, 1.0).expect("the whole path is a segment");
    assert_eq!(start, p(0.0, 0.0));
    assert_eq!(end, p(100.0, 100.0));
    // The ends are interpolated, so the points sitting on them are not repeated.
    assert!(!interior.contains(&start), "the start would be drawn twice");
    assert!(!interior.contains(&end), "and so would the end");
}

#[test]
fn a_half_segment_ends_halfway_along() {
    let path = SliderPath::new(
        CurveType::Linear,
        &[p(0.0, 0.0), p(100.0, 0.0)],
        Some(100.0),
    );
    let (start, _, end) = path.segment(0.0, 0.5).unwrap();
    assert_eq!(start, p(0.0, 0.0));
    assert!((end.x - 50.0).abs() < 1e-6, "{end:?}");
}

#[test]
fn a_segment_can_start_partway_in() {
    // What a retracting slider asks for: the body behind the ball is gone.
    let path = SliderPath::new(
        CurveType::Linear,
        &[p(0.0, 0.0), p(100.0, 0.0)],
        Some(100.0),
    );
    let (start, _, end) = path.segment(0.75, 1.0).unwrap();
    assert!((start.x - 75.0).abs() < 1e-6, "{start:?}");
    assert_eq!(end, p(100.0, 0.0));
}

#[test]
fn an_empty_segment_is_nothing_to_draw() {
    // A slider that has not begun growing has no body, and asking for a
    // zero-length stretch should say so rather than hand back a dot.
    let path = SliderPath::new(
        CurveType::Linear,
        &[p(0.0, 0.0), p(100.0, 0.0)],
        Some(100.0),
    );
    assert!(path.segment(0.0, 0.0).is_none());
    assert!(
        path.segment(0.6, 0.4).is_none(),
        "reversed ends draw nothing"
    );
}

// ── the authored length wins in both directions ──────────────────────────

#[test]
fn a_path_shorter_than_its_authored_length_is_stretched_to_it() {
    // osu! stretches the final segment rather than letting the ball stop
    // early:
    //
    // ```csharp
    // Vector2 dir = (calculatedPath[pathEndIndex] - calculatedPath[pathEndIndex - 1]).Normalized();
    // calculatedPath[pathEndIndex] = calculatedPath[pathEndIndex - 1] + dir * (float)(expectedDistance - cumulativeLength[^1]);
    // ```
    //
    // Old maps do this constantly. `Kona-Chan: Farucon Pan!`, file format v4,
    // has sliders drawing 32 osu!pixels against an authored 65 — and the next
    // object sits where the stretched path ends, not where the drawn one does.
    let path = SliderPath::new(CurveType::Linear, &[p(0.0, 0.0), p(0.0, -32.0)], Some(65.0));

    assert!((path.length() - 65.0).abs() < 1e-9, "{}", path.length());
    let end = path.position_at(65.0).expect("the path has an end");
    assert!(
        end.x.abs() < EPS && (end.y + 65.0).abs() < EPS,
        "the end carries on along the last segment: ({}, {})",
        end.x,
        end.y
    );
}

#[test]
fn a_path_longer_than_its_authored_length_is_still_cut_to_it() {
    let path = SliderPath::new(CurveType::Linear, &[p(0.0, 0.0), p(100.0, 0.0)], Some(40.0));
    assert!((path.length() - 40.0).abs() < 1e-9);
    let end = path.position_at(40.0).expect("the path has an end");
    assert!((end.x - 40.0).abs() < EPS && end.y.abs() < EPS);
}

#[test]
fn a_single_point_has_no_direction_to_stretch_along() {
    // Nothing to extrapolate from, so it stays put rather than inventing a
    // heading and flinging the ball off the playfield.
    let path = SliderPath::new(CurveType::Linear, &[p(10.0, 10.0)], Some(65.0));
    assert_eq!(path.length(), 0.0);
}

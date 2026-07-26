//! Slider paths: control points in, a walkable polyline out.
//!
//! Every curve type is flattened to a polyline with cumulative distances, and
//! everything downstream asks the same question — "where is the ball at
//! progress *t*" — regardless of how the curve was authored.
//!
//! Two behaviours here are the game's, not geometry's, and are easy to get
//! wrong:
//!
//! * **The authored length wins.** A slider states its pixel length, and it is
//!   routinely shorter than the curve actually drawn by the control points.
//!   osu! walks the path only that far, so the tail of the geometry is unused.
//! * **A perfect-circle slider with collinear points is not an error.** The
//!   game silently treats it as a bezier, because an arc through three points
//!   on a line has no finite centre.

use crate::hitobject::{CurveType, Point};

/// How far a flattened segment may stray from the true curve, in osu!pixels.
/// Well under a rendered pixel at any sane resolution, and it bounds the work:
/// subdivision stops as soon as the chord is this close.
const FLATNESS_TOLERANCE: f64 = 0.25;

/// Ceiling on de Casteljau recursion. Degenerate control polygons (all points
/// identical, NaN coordinates from a corrupt file) would otherwise never look
/// flat and would recurse until the stack gave out.
const MAX_SUBDIVISION_DEPTH: u32 = 16;

/// Arc and Catmull spans are sampled at a fixed rate rather than adaptively —
/// their curvature is bounded, so a per-span count keyed to length is enough.
const SAMPLES_PER_100PX: f64 = 25.0;
const MIN_SPAN_SAMPLES: usize = 4;

impl Point {
    fn add(self, other: Self) -> Self {
        Self {
            x: self.x + other.x,
            y: self.y + other.y,
        }
    }

    fn sub(self, other: Self) -> Self {
        Self {
            x: self.x - other.x,
            y: self.y - other.y,
        }
    }

    fn scale(self, k: f64) -> Self {
        Self {
            x: self.x * k,
            y: self.y * k,
        }
    }

    fn distance(self, other: Self) -> f64 {
        self.sub(other).length()
    }

    fn length(self) -> f64 {
        self.x.hypot(self.y)
    }

    fn lerp(self, other: Self, t: f64) -> Self {
        self.add(other.sub(self).scale(t))
    }

    fn is_finite(self) -> bool {
        self.x.is_finite() && self.y.is_finite()
    }
}

/// A flattened slider path with cumulative distances along it.
#[derive(Debug, Clone, PartialEq)]
pub struct SliderPath {
    points: Vec<Point>,
    /// `cumulative[i]` is the distance from the start to `points[i]`.
    cumulative: Vec<f64>,
    length: f64,
}

impl SliderPath {
    /// Flatten `control_points` and trim to `expected_length` (the value the
    /// map authored). Pass `None` to keep the geometry's own length.
    pub fn new(
        curve_type: CurveType,
        control_points: &[Point],
        expected_length: Option<f64>,
    ) -> Self {
        let points = flatten(curve_type, control_points);
        Self::from_polyline(points, expected_length)
    }

    fn from_polyline(mut points: Vec<Point>, expected_length: Option<f64>) -> Self {
        points.retain(|p| p.is_finite());
        if points.is_empty() {
            return Self {
                points: Vec::new(),
                cumulative: Vec::new(),
                length: 0.0,
            };
        }

        let mut cumulative = Vec::with_capacity(points.len());
        let mut total = 0.0;
        cumulative.push(0.0);
        for pair in points.windows(2) {
            total += pair[0].distance(pair[1]);
            cumulative.push(total);
        }

        let mut path = Self {
            points,
            cumulative,
            length: total,
        };
        if let Some(target) = expected_length {
            path.trim_to(target);
        }
        path
    }

    /// Walk only `target` pixels of the geometry, as the game does.
    ///
    /// A target longer than the curve is clamped rather than extrapolated: the
    /// ball stops at the end of the drawn path, which is what osu! shows.
    fn trim_to(&mut self, target: f64) {
        if !target.is_finite() || target <= 0.0 {
            self.points.truncate(1);
            self.cumulative.truncate(1);
            self.length = 0.0;
            return;
        }
        if target >= self.length {
            return;
        }

        let idx = self.cumulative.partition_point(|&d| d < target);
        let before = idx - 1;
        let span = self.cumulative[idx] - self.cumulative[before];
        let t = if span > 0.0 {
            (target - self.cumulative[before]) / span
        } else {
            0.0
        };
        let cut = self.points[before].lerp(self.points[idx], t);

        self.points.truncate(idx);
        self.cumulative.truncate(idx);
        self.points.push(cut);
        self.cumulative.push(target);
        self.length = target;
    }

    /// Path length in osu!pixels, after trimming.
    /// Shift the whole path. Distances along it are unchanged, so the cached
    /// cumulative lengths stay valid — which is why stacking can move a slider
    /// after the fact instead of re-flattening it.
    pub fn translate(&mut self, dx: f64, dy: f64) {
        for point in &mut self.points {
            point.x += dx;
            point.y += dy;
        }
    }

    pub fn length(&self) -> f64 {
        self.length
    }

    pub fn is_empty(&self) -> bool {
        self.points.is_empty()
    }

    /// The flattened polyline, for drawing the slider body.
    pub fn points(&self) -> &[Point] {
        &self.points
    }

    /// Position at `progress` along a single traversal, clamped to `[0, 1]`.
    pub fn position_at(&self, progress: f64) -> Option<Point> {
        let first = *self.points.first()?;
        if self.length <= 0.0 {
            return Some(first);
        }
        let target = progress.clamp(0.0, 1.0) * self.length;

        let idx = self.cumulative.partition_point(|&d| d < target).max(1);
        let (before, after) = (idx - 1, idx.min(self.points.len() - 1));
        let span = self.cumulative[after] - self.cumulative[before];
        let t = if span > 0.0 {
            (target - self.cumulative[before]) / span
        } else {
            0.0
        };
        Some(self.points[before].lerp(self.points[after], t))
    }

    /// Position across a repeating slider, where `progress` runs `0..slides`.
    ///
    /// Odd slides run backwards — that's what a repeat *is* — so the local
    /// progress is mirrored on them.
    pub fn position_at_slide(&self, progress: f64, slides: u32) -> Option<Point> {
        let slides = slides.max(1) as f64;
        let p = progress.clamp(0.0, slides);
        let index = p.floor().min(slides - 1.0);
        let mut local = p - index;
        if (index as u64) % 2 == 1 {
            local = 1.0 - local;
        }
        self.position_at(local)
    }
}

// ── flattening ───────────────────────────────────────────────────────────

fn flatten(curve_type: CurveType, control: &[Point]) -> Vec<Point> {
    let control: Vec<Point> = control.iter().copied().filter(|p| p.is_finite()).collect();
    if control.len() < 2 {
        return control;
    }

    match curve_type {
        CurveType::Linear => control,
        CurveType::PerfectCircle => {
            circular_arc(&control).unwrap_or_else(|| bezier_chain(&control))
        }
        CurveType::Catmull => catmull_chain(&control),
        CurveType::Bezier => bezier_chain(&control),
    }
}

/// A bezier "curve" is really a chain of them: a control point repeated back to
/// back marks the end of one segment and the start of the next, which is how
/// maps encode a sharp corner (red anchors in the editor).
fn bezier_chain(control: &[Point]) -> Vec<Point> {
    let mut out = vec![control[0]];
    let mut start = 0;

    for i in 0..control.len() {
        let is_last = i == control.len() - 1;
        let is_repeat = !is_last && control[i] == control[i + 1];
        if !(is_last || is_repeat) {
            continue;
        }

        let segment = &control[start..=i];
        if segment.len() >= 2 {
            approximate_bezier(segment, &mut out, 0);
        }
        start = i + 1;
    }
    out
}

/// Recursive de Casteljau: split until the control polygon is within tolerance
/// of its chord, then emit the endpoint.
fn approximate_bezier(control: &[Point], out: &mut Vec<Point>, depth: u32) {
    if depth >= MAX_SUBDIVISION_DEPTH || is_flat(control) {
        out.push(*control.last().expect("segment is non-empty"));
        return;
    }

    let (left, right) = split_bezier(control);
    approximate_bezier(&left, out, depth + 1);
    approximate_bezier(&right, out, depth + 1);
}

/// Flat when every interior control point sits within tolerance of the chord
/// between the first and last.
fn is_flat(control: &[Point]) -> bool {
    let (first, last) = (control[0], control[control.len() - 1]);
    let chord = last.sub(first);
    let chord_len = chord.length();

    control[1..control.len().saturating_sub(1)].iter().all(|p| {
        let offset = p.sub(first);
        let distance = if chord_len > f64::EPSILON {
            // |cross| / |chord| — perpendicular distance to the chord line.
            (chord.x * offset.y - chord.y * offset.x).abs() / chord_len
        } else {
            // Degenerate chord: fall back to plain distance from the endpoint.
            offset.length()
        };
        distance <= FLATNESS_TOLERANCE
    })
}

fn split_bezier(control: &[Point]) -> (Vec<Point>, Vec<Point>) {
    let n = control.len();
    let mut scratch: Vec<Point> = control.to_vec();
    let mut left = Vec::with_capacity(n);
    let mut right = Vec::with_capacity(n);

    left.push(scratch[0]);
    right.push(scratch[n - 1]);
    for level in 1..n {
        for i in 0..(n - level) {
            scratch[i] = scratch[i].lerp(scratch[i + 1], 0.5);
        }
        left.push(scratch[0]);
        right.push(scratch[n - level - 1]);
    }
    right.reverse();
    (left, right)
}

/// Circular arc through exactly three points.
///
/// Returns `None` when there are not three points or they're collinear — the
/// caller then treats the slider as a bezier, matching the game.
fn circular_arc(control: &[Point]) -> Option<Vec<Point>> {
    let [a, b, c] = control else { return None };
    let (a, b, c) = (*a, *b, *c);

    // Twice the signed area of the triangle; zero means no circumcircle.
    let d = 2.0 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y));
    if d.abs() < 1e-6 {
        return None;
    }

    let (sa, sb, sc) = (
        a.x * a.x + a.y * a.y,
        b.x * b.x + b.y * b.y,
        c.x * c.x + c.y * c.y,
    );
    let centre = Point {
        x: (sa * (b.y - c.y) + sb * (c.y - a.y) + sc * (a.y - b.y)) / d,
        y: (sa * (c.x - b.x) + sb * (a.x - c.x) + sc * (b.x - a.x)) / d,
    };
    if !centre.is_finite() {
        return None;
    }

    let radius = centre.distance(a);
    let angle_of = |p: Point| (p.y - centre.y).atan2(p.x - centre.x);
    let (start, end) = (angle_of(a), angle_of(c));

    // Sweep from start to end the way that passes through the middle point;
    // the cross product tells us which way that is.
    let cross = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    let mut sweep = end - start;
    if cross < 0.0 {
        while sweep > 0.0 {
            sweep -= std::f64::consts::TAU;
        }
    } else {
        while sweep < 0.0 {
            sweep += std::f64::consts::TAU;
        }
    }

    let arc_len = (radius * sweep).abs();
    let steps = sample_count(arc_len);
    let mut out = Vec::with_capacity(steps + 1);
    for i in 0..=steps {
        let t = i as f64 / steps as f64;
        let angle = start + sweep * t;
        out.push(Point {
            x: centre.x + radius * angle.cos(),
            y: centre.y + radius * angle.sin(),
        });
    }
    Some(out)
}

/// Legacy centripetal-style Catmull-Rom, sampled span by span. Endpoints are
/// duplicated so the first and last spans have neighbours to work with.
fn catmull_chain(control: &[Point]) -> Vec<Point> {
    let mut out = vec![control[0]];

    for i in 0..control.len().saturating_sub(1) {
        let p0 = control[i.saturating_sub(1)];
        let p1 = control[i];
        let p2 = control[i + 1];
        let p3 = *control.get(i + 2).unwrap_or(&p2);

        let steps = sample_count(p1.distance(p2));
        for step in 1..=steps {
            let t = step as f64 / steps as f64;
            out.push(catmull_point(p0, p1, p2, p3, t));
        }
    }
    out
}

fn catmull_point(p0: Point, p1: Point, p2: Point, p3: Point, t: f64) -> Point {
    let (t2, t3) = (t * t, t * t * t);
    let term = |a: f64, b: f64, c: f64, d: f64| {
        0.5 * (2.0 * b
            + (-a + c) * t
            + (2.0 * a - 5.0 * b + 4.0 * c - d) * t2
            + (-a + 3.0 * b - 3.0 * c + d) * t3)
    };
    Point {
        x: term(p0.x, p1.x, p2.x, p3.x),
        y: term(p0.y, p1.y, p2.y, p3.y),
    }
}

fn sample_count(span_length: f64) -> usize {
    if !span_length.is_finite() {
        return MIN_SPAN_SAMPLES;
    }
    ((span_length / 100.0 * SAMPLES_PER_100PX).ceil() as usize).max(MIN_SPAN_SAMPLES)
}

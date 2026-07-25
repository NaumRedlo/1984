//! Hit objects.
//!
//! One line encodes all three kinds, distinguished by bits in the `type` field:
//! `x,y,time,type,hitSound,params...`. Circles carry nothing extra, spinners
//! carry an end time, sliders carry a curve.
//!
//! This module records what the file says. Turning control points into a
//! walkable path is [`crate::SliderPath`] — still beatmap geometry, since it
//! depends only on the control points and the authored length, not on time or
//! on the player.

/// The playfield every `.osu` file is authored against, in osu!pixels. Screen
/// resolution never enters the file — it's applied when drawing.
pub const PLAYFIELD_WIDTH: f64 = 512.0;
pub const PLAYFIELD_HEIGHT: f64 = 384.0;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

impl Point {
    /// Middle of the playfield — where every spinner is centred.
    pub const CENTRE: Self = Self {
        x: PLAYFIELD_WIDTH / 2.0,
        y: PLAYFIELD_HEIGHT / 2.0,
    };

    pub fn distance_to(self, other: Self) -> f64 {
        (self.x - other.x).hypot(self.y - other.y)
    }

    /// Reflected across the horizontal midline, which is what HardRock does to
    /// the whole map.
    pub fn mirrored(self) -> Self {
        Self {
            x: self.x,
            y: PLAYFIELD_HEIGHT - self.y,
        }
    }
}

/// How the control points are joined.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CurveType {
    /// `B` — the common case; a chain of beziers split at repeated points.
    Bezier,
    /// `C` — centripetal Catmull-Rom. Legacy, still present in old maps.
    Catmull,
    /// `L` — straight segments.
    Linear,
    /// `P` — a circular arc through three points. Falls back to Bezier when the
    /// points are collinear (the game does the same rather than erroring).
    PerfectCircle,
}

impl CurveType {
    fn from_char(c: char) -> Option<Self> {
        Some(match c {
            'B' => Self::Bezier,
            'C' => Self::Catmull,
            'L' => Self::Linear,
            'P' => Self::PerfectCircle,
            _ => return None,
        })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Slider {
    pub curve_type: CurveType,
    /// Control points, starting with the object's own position.
    pub points: Vec<Point>,
    /// How many times the ball traverses the path: 1 = there and done,
    /// 2 = one repeat, and so on.
    pub slides: u32,
    /// Path length in osu!pixels, as authored. May be absent in old maps.
    pub length: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ObjectKind {
    Circle,
    Slider(Slider),
    Spinner { end_time_ms: f64 },
}

#[derive(Debug, Clone, PartialEq)]
pub struct HitObject {
    pub pos: Point,
    pub time_ms: f64,
    pub new_combo: bool,
    pub kind: ObjectKind,
}

impl HitObject {
    pub fn is_circle(&self) -> bool {
        matches!(self.kind, ObjectKind::Circle)
    }

    pub fn is_slider(&self) -> bool {
        matches!(self.kind, ObjectKind::Slider(_))
    }

    pub fn is_spinner(&self) -> bool {
        matches!(self.kind, ObjectKind::Spinner { .. })
    }

    /// When the object stops being interactive. Circles are instantaneous;
    /// spinners state their end; sliders need tempo and velocity to work it
    /// out, so they report their start until the simulator resolves the path.
    pub fn end_time_ms(&self) -> f64 {
        match &self.kind {
            ObjectKind::Spinner { end_time_ms } => *end_time_ms,
            _ => self.time_ms,
        }
    }
}

pub(crate) mod type_bits {
    pub const CIRCLE: u32 = 1 << 0;
    pub const SLIDER: u32 = 1 << 1;
    pub const NEW_COMBO: u32 = 1 << 2;
    pub const SPINNER: u32 = 1 << 3;
    pub const MANIA_HOLD: u32 = 1 << 7;
}

pub(crate) fn parse_curve(spec: &str) -> Option<(CurveType, Vec<Point>)> {
    let mut parts = spec.split('|');
    let head = parts.next()?;
    let curve_type = CurveType::from_char(head.chars().next()?)?;

    let mut points = Vec::new();
    for token in parts {
        let (x, y) = token.split_once(':')?;
        points.push(Point {
            x: x.trim().parse().ok()?,
            y: y.trim().parse().ok()?,
        });
    }
    Some((curve_type, points))
}

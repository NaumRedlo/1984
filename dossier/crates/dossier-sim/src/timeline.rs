//! Objects resolved onto a timeline.
//!
//! The file says *where* a slider goes but not *how long* it takes — that needs
//! the tempo in force, the slider-velocity multiplier at that instant, and the
//! map's global multiplier, all at once. Resolving it once up front keeps the
//! per-frame path free of lookups.

use dossier_beatmap::{Beatmap, Difficulty, HitObject, ObjectKind, Point, SliderPath};

/// A hit object with its span on the timeline worked out, plus the flattened
/// path for sliders.
#[derive(Debug, Clone)]
pub struct TimedObject {
    /// Index into the beatmap's object list, so callers can get back to it.
    pub index: usize,
    pub pos: Point,
    pub start_ms: f64,
    /// When the object stops being interactive. For a circle this equals
    /// `start_ms`; for a slider it's the end of the last slide.
    pub end_ms: f64,
    pub new_combo: bool,
    pub kind: TimedKind,
}

#[derive(Debug, Clone)]
pub enum TimedKind {
    Circle,
    Slider {
        path: SliderPath,
        slides: u32,
        /// Duration of a single traversal, in milliseconds.
        slide_duration_ms: f64,
    },
    Spinner,
}

impl TimedObject {
    pub fn duration_ms(&self) -> f64 {
        self.end_ms - self.start_ms
    }

    /// Where the slider ball is at `time_ms`, or `None` for other object kinds
    /// and for times outside the slider's span.
    pub fn ball_at(&self, time_ms: f64) -> Option<Point> {
        let TimedKind::Slider {
            path,
            slides,
            slide_duration_ms,
        } = &self.kind
        else {
            return None;
        };
        if time_ms < self.start_ms || time_ms > self.end_ms || *slide_duration_ms <= 0.0 {
            return None;
        }
        let progress = (time_ms - self.start_ms) / slide_duration_ms;
        path.position_at_slide(progress, *slides)
    }
}

/// A beatmap with every object placed on the timeline.
#[derive(Debug, Clone)]
pub struct Timeline {
    pub objects: Vec<TimedObject>,
    /// Difficulty after mods, which is what preempt and hit windows come from.
    pub difficulty: Difficulty,
}

impl Timeline {
    /// Resolve `beatmap` under an already mod-adjusted `difficulty`.
    ///
    /// Difficulty is passed in rather than read off the beatmap because HR and
    /// EZ rewrite it, and every timing question downstream must use the same
    /// adjusted values.
    pub fn build(beatmap: &Beatmap, difficulty: Difficulty) -> Self {
        let objects = beatmap
            .objects
            .iter()
            .enumerate()
            .map(|(index, obj)| resolve(beatmap, &difficulty, index, obj))
            .collect();
        Self {
            objects,
            difficulty,
        }
    }

    /// Objects on screen at `time_ms`: already spawned, not yet finished.
    ///
    /// Linear over the object list. Fine at this stage — the caller that needs
    /// it per video frame will keep a cursor into the list instead.
    pub fn visible_at(&self, time_ms: f64) -> impl Iterator<Item = &TimedObject> {
        let preempt = self.difficulty.preempt_ms();
        self.objects
            .iter()
            .filter(move |o| time_ms >= o.start_ms - preempt && time_ms <= o.end_ms)
    }

    /// How far into its approach an object is at `time_ms`: 0 when it spawns,
    /// 1 when it must be hit. Values outside `[0, 1]` mean it isn't approaching
    /// (not spawned yet, or already due).
    pub fn approach_progress(&self, object: &TimedObject, time_ms: f64) -> f64 {
        let preempt = self.difficulty.preempt_ms();
        if preempt <= 0.0 {
            return 1.0;
        }
        (time_ms - (object.start_ms - preempt)) / preempt
    }
}

fn resolve(
    beatmap: &Beatmap,
    difficulty: &Difficulty,
    index: usize,
    obj: &HitObject,
) -> TimedObject {
    let (kind, end_ms) = match &obj.kind {
        ObjectKind::Circle => (TimedKind::Circle, obj.time_ms),
        ObjectKind::Spinner { end_time_ms } => (TimedKind::Spinner, *end_time_ms),
        ObjectKind::Slider(slider) => {
            let path = SliderPath::new(slider.curve_type, &slider.points, Some(slider.length));
            let slide_duration_ms = slide_duration(beatmap, difficulty, obj.time_ms, path.length());
            let end = obj.time_ms + slide_duration_ms * f64::from(slider.slides.max(1));
            (
                TimedKind::Slider {
                    path,
                    slides: slider.slides.max(1),
                    slide_duration_ms,
                },
                end,
            )
        }
    };

    TimedObject {
        index,
        pos: obj.pos,
        start_ms: obj.time_ms,
        end_ms,
        new_combo: obj.new_combo,
        kind,
    }
}

/// Milliseconds for one traversal of a slider.
///
/// osu! measures slider speed in "how many osu!pixels fit in a beat":
/// `SliderMultiplier * 100`, scaled by whatever green line is in force. Divide
/// the path length by that to get beats, multiply by the beat length to get
/// time.
fn slide_duration(beatmap: &Beatmap, difficulty: &Difficulty, time_ms: f64, length: f64) -> f64 {
    let beat_length = beatmap
        .timing
        .timing_point_at(time_ms)
        .map_or(0.0, |p| p.beat_length);
    let pixels_per_beat =
        difficulty.slider_multiplier * 100.0 * beatmap.timing.velocity_at(time_ms);

    if beat_length <= 0.0 || pixels_per_beat <= 0.0 || !length.is_finite() {
        // A map with no timing point can't place the slider in time; treat it
        // as instantaneous rather than producing an infinity.
        return 0.0;
    }
    length / pixels_per_beat * beat_length
}

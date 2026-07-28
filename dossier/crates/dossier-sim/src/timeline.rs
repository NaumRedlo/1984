//! Objects resolved onto a timeline.
//!
//! The file says *where* a slider goes but not *how long* it takes — that needs
//! the tempo in force, the slider-velocity multiplier at that instant, and the
//! map's global multiplier, all at once. Resolving it once up front keeps the
//! per-frame path free of lookups.

use dossier_beatmap::{Beatmap, Difficulty, HitObject, ObjectKind, Point, SliderPath};
use dossier_replay::{bits, Mods};

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
    /// How high this object sits in a stack of overlapping ones; zero when it
    /// is not stacked. Kept rather than discarded after the shift is applied,
    /// because stable's note lock consults it: a click whose predecessor is an
    /// unjudged stacked object passes through untouched.
    pub stack_height: i32,
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
        /// Tick times measured from the start of one traversal, ascending. The
        /// same offsets serve every slide — see [`TimedObject::tick_times`].
        tick_offsets_ms: Vec<f64>,
    },
    Spinner,
}

impl TimedObject {
    pub fn duration_ms(&self) -> f64 {
        self.end_ms - self.start_ms
    }

    pub fn is_slider(&self) -> bool {
        matches!(self.kind, TimedKind::Slider { .. })
    }

    pub fn is_spinner(&self) -> bool {
        matches!(self.kind, TimedKind::Spinner)
    }

    /// How long one traversal of this slider takes, or `None` for other kinds.
    pub fn slide_duration_ms(&self) -> Option<f64> {
        match &self.kind {
            TimedKind::Slider {
                slide_duration_ms, ..
            } => Some(*slide_duration_ms),
            _ => None,
        }
    }

    /// Where the slider ball is at `time_ms`, or `None` for other object kinds
    /// and for times outside the slider's span.
    pub fn ball_at(&self, time_ms: f64) -> Option<Point> {
        let TimedKind::Slider {
            path,
            slides,
            slide_duration_ms,
            ..
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

    /// Absolute times of every slider tick, ascending, across all slides.
    ///
    /// A reversed slide walks the path backwards, so the ball meets the same
    /// ticks in the opposite order — their offsets are mirrored within the
    /// slide, and the absolute times still come out ascending.
    pub fn tick_times(&self) -> Vec<f64> {
        let TimedKind::Slider {
            slides,
            slide_duration_ms,
            tick_offsets_ms,
            ..
        } = &self.kind
        else {
            return Vec::new();
        };

        let mut times = Vec::with_capacity(tick_offsets_ms.len() * *slides as usize);
        for slide in 0..*slides {
            let base = self.start_ms + f64::from(slide) * slide_duration_ms;
            if slide % 2 == 0 {
                times.extend(tick_offsets_ms.iter().map(|o| base + o));
            } else {
                times.extend(
                    tick_offsets_ms
                        .iter()
                        .rev()
                        .map(|o| base + slide_duration_ms - o),
                );
            }
        }
        times
    }

    /// Move the object and, for a slider, its whole path. Used by stacking,
    /// which decides how far to nudge things only after every path is built.
    pub(crate) fn translate(&mut self, dx: f64, dy: f64) {
        self.pos.x += dx;
        self.pos.y += dy;
        if let TimedKind::Slider { path, .. } = &mut self.kind {
            path.translate(dx, dy);
        }
    }

    /// Absolute times at which the ball turns around: one per repeat, so a
    /// slider with `slides == 1` has none.
    pub fn repeat_times(&self) -> Vec<f64> {
        let TimedKind::Slider {
            slides,
            slide_duration_ms,
            ..
        } = &self.kind
        else {
            return Vec::new();
        };
        (1..*slides)
            .map(|s| self.start_ms + f64::from(s) * slide_duration_ms)
            .collect()
    }
}

/// A beatmap with every object placed on the timeline.
#[derive(Debug, Clone)]
pub struct Timeline {
    pub objects: Vec<TimedObject>,
    /// Difficulty after mods, which is what preempt and hit windows come from.
    pub difficulty: Difficulty,
    pub mods: Mods,
    /// Pauses the map declares, as (start, end) in milliseconds. Carried
    /// through because what follows a break arrives with no warning from the
    /// rhythm, and the game puts arrows up to supply one.
    pub breaks: Vec<(f64, f64)>,
    /// The map's timing, kept so anything downstream can find the beat. Slider
    /// durations are resolved here and need it no further, but a renderer does:
    /// a cue that pulses with the music has to know where the music's pulse is.
    pub timing: dossier_beatmap::Timing,
}

impl Timeline {
    /// Resolve `beatmap` as played under `mods`.
    ///
    /// Mods are applied here rather than by the caller because they change two
    /// unrelated things at once — the difficulty numbers and, for HardRock, the
    /// geometry — and letting those drift apart is how a judge ends up testing
    /// hits against un-mirrored positions.
    pub fn build(beatmap: &Beatmap, mods: Mods) -> Self {
        let difficulty = apply_mods(beatmap.difficulty, mods);
        let mirror = mods.contains(bits::HARD_ROCK);
        let mut objects: Vec<TimedObject> = beatmap
            .objects
            .iter()
            .enumerate()
            .map(|(index, obj)| resolve(beatmap, &difficulty, index, obj, mirror))
            .collect();

        // After mirroring, because HardRock moves the objects and stacks are
        // decided by where objects actually end up.
        crate::stacking::apply(
            &mut objects,
            &difficulty,
            beatmap.stack_leniency,
            beatmap.format_version,
        );

        Self {
            objects,
            difficulty,
            mods,
            breaks: beatmap.breaks.clone(),
            timing: beatmap.timing.clone(),
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

/// HardRock and Easy rewrite the difficulty; they're mutually exclusive, and
/// osu! rejects a replay carrying both.
fn apply_mods(difficulty: Difficulty, mods: Mods) -> Difficulty {
    if mods.contains(bits::HARD_ROCK) {
        difficulty.hard_rock()
    } else if mods.contains(bits::EASY) {
        difficulty.easy()
    } else {
        difficulty
    }
}

fn resolve(
    beatmap: &Beatmap,
    difficulty: &Difficulty,
    index: usize,
    obj: &HitObject,
    mirror: bool,
) -> TimedObject {
    let flip = |p: Point| if mirror { p.mirrored() } else { p };

    let (kind, end_ms) = match &obj.kind {
        ObjectKind::Circle => (TimedKind::Circle, obj.time_ms),
        ObjectKind::Spinner { end_time_ms } => (TimedKind::Spinner, *end_time_ms),
        ObjectKind::Slider(slider) => {
            let points: Vec<Point> = slider.points.iter().map(|p| flip(*p)).collect();
            let path = SliderPath::new(slider.curve_type, &points, Some(slider.length));
            let slides = slider.slides.max(1);
            let slide_duration_ms = slide_duration(beatmap, difficulty, obj.time_ms, path.length());
            let tick_offsets_ms = tick_offsets(beatmap, difficulty, obj.time_ms, slide_duration_ms);
            (
                TimedKind::Slider {
                    path,
                    slides,
                    slide_duration_ms,
                    tick_offsets_ms,
                },
                obj.time_ms + slide_duration_ms * f64::from(slides),
            )
        }
    };

    TimedObject {
        index,
        pos: flip(obj.pos),
        start_ms: obj.time_ms,
        end_ms,
        new_combo: obj.new_combo,
        // Filled in by stacking, which is the only thing that knows.
        stack_height: 0,
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

/// Tick times within one traversal.
///
/// Ticks are spaced by distance — one every `scoring_distance / tick_rate`
/// osu!pixels — but the ball moves at a constant speed along a slide, so that
/// works out to a constant `beat_length / tick_rate` in time, free of the
/// slider velocity. The final tick is dropped when it would land on top of the
/// slider's end; osu! uses an eighth of a tick as the threshold.
fn tick_offsets(
    beatmap: &Beatmap,
    difficulty: &Difficulty,
    time_ms: f64,
    slide_duration_ms: f64,
) -> Vec<f64> {
    let beat_length = beatmap
        .timing
        .timing_point_at(time_ms)
        .map_or(0.0, |p| p.beat_length);
    let spacing = beat_length / difficulty.slider_tick_rate.max(0.1);

    if spacing <= 0.0 || !spacing.is_finite() || slide_duration_ms <= 0.0 {
        return Vec::new();
    }

    let limit = slide_duration_ms - spacing / 8.0;
    let mut offsets = Vec::new();
    let mut t = spacing;
    while t < limit {
        offsets.push(t);
        t += spacing;
        // A pathological map could otherwise spin here for a very long time.
        if offsets.len() >= 10_000 {
            break;
        }
    }
    offsets
}
